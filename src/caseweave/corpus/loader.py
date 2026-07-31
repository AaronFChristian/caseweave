"""Regulatory corpus ingest into pgvector.

Chunking is heading-aware rather than fixed-window: these documents are
structured by markdown heading, and a chunk that straddles two typologies
retrieves badly for both. Each chunk keeps its document and section title so
the Day 2 narrative agent can cite "the structuring typology note, observable
indicators section" rather than an opaque chunk id.

Embeddings run locally. A 384-dimension model on CPU is fast enough for a
corpus this size and costs nothing, which matters when the API budget is
reserved for generation.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from caseweave import config as cfg

DDL = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS reg_chunks (
    chunk_id     TEXT PRIMARY KEY,
    doc_id       TEXT NOT NULL,
    doc_title    TEXT NOT NULL,
    section      TEXT NOT NULL,
    ordinal      INT  NOT NULL,
    content      TEXT NOT NULL,
    token_est    INT  NOT NULL,
    embedding    vector({cfg.EMBEDDING_DIM})
);
CREATE INDEX IF NOT EXISTS reg_chunks_hnsw
    ON reg_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS reg_chunks_fts
    ON reg_chunks USING gin (to_tsvector('english', content));
"""

MIN_CHARS = 220
MAX_CHARS = 1400


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    doc_title: str
    section: str
    ordinal: int
    content: str
    token_est: int


def _split(md: str) -> list[tuple[str, str, str]]:
    """Split markdown into (doc_title, section_title, body) on level-2 headings."""
    title_m = re.search(r"^#\s+(.+)$", md, re.M)
    title = title_m.group(1).strip() if title_m else "Untitled"
    parts = re.split(r"^##\s+(.+)$", md, flags=re.M)
    out: list[tuple[str, str, str]] = []
    preamble = re.sub(r"^#\s+.+$", "", parts[0], flags=re.M).strip()
    if preamble:
        out.append((title, "Overview", preamble))
    for i in range(1, len(parts) - 1, 2):
        out.append((title, parts[i].strip(), parts[i + 1].strip()))
    return out


def chunk_dir(path: Path | None = None) -> list[Chunk]:
    path = path or cfg.REGULATORY_DIR
    files = sorted(path.glob("*.md"))
    if not files:
        raise FileNotFoundError(f"no markdown corpus found in {path}")

    chunks: list[Chunk] = []
    for f in files:
        doc_id = f.stem
        for title, section, body in _split(f.read_text()):
            buf = body
            while buf:
                if len(buf) <= MAX_CHARS:
                    piece, buf = buf, ""
                else:
                    cut = buf.rfind("\n", MIN_CHARS, MAX_CHARS)
                    cut = cut if cut > 0 else MAX_CHARS
                    piece, buf = buf[:cut], buf[cut:].lstrip()
                piece = piece.strip()
                if len(piece) < 60:
                    continue
                cid = hashlib.sha256(f"{doc_id}|{section}|{piece[:120]}".encode()).hexdigest()[:20]
                chunks.append(
                    Chunk(
                        chunk_id=cid,
                        doc_id=doc_id,
                        doc_title=title,
                        section=section,
                        ordinal=len(chunks),
                        content=piece,
                        token_est=len(piece) // 4,
                    )
                )
    return chunks


_MODEL_CACHE: dict[str, SentenceTransformer] = {}  # noqa: F821 - type imported lazily below


def _get_model():
    """Cache the loaded model at module level. Loading a SentenceTransformer
    is a ~100-200ms+ disk read even when weights are already downloaded;
    reloading it inside a loop (e.g. once per golden-set case) is pure
    overhead with no behavioural difference — caught in the Day 3 backtest,
    where 30 cases each printed a fresh 'Loading weights' and pushed mean
    latency to 3.2s/case despite every LLM call being mocked and instant."""
    key = cfg.EMBEDDING_MODEL
    if key not in _MODEL_CACHE:
        from sentence_transformers import SentenceTransformer

        _MODEL_CACHE[key] = SentenceTransformer(key)
    return _MODEL_CACHE[key]


def embed(texts: list[str]):
    model = _get_model()
    return model.encode(texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False)


def ingest() -> int:
    import psycopg
    from pgvector.psycopg import register_vector

    chunks = chunk_dir()
    if not chunks:
        raise ValueError("refusing to load 0 chunks")

    vectors = embed([f"{c.doc_title} — {c.section}\n{c.content}" for c in chunks])

    with psycopg.connect(cfg.POSTGRES_DSN, autocommit=True) as conn:
        conn.execute(DDL)
        register_vector(conn)
        conn.execute("TRUNCATE reg_chunks")
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO reg_chunks
                   (chunk_id, doc_id, doc_title, section, ordinal, content, token_est, embedding)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                [
                    (
                        c.chunk_id,
                        c.doc_id,
                        c.doc_title,
                        c.section,
                        c.ordinal,
                        c.content,
                        c.token_est,
                        v,
                    )
                    for c, v in zip(chunks, vectors, strict=True)
                ],
            )
    return len(chunks)


def search(query: str, k: int = 5) -> list[dict]:
    """Vector search. Day 2 replaces this with hybrid BM25 + vector + rerank."""
    import psycopg
    from pgvector.psycopg import register_vector

    qv = embed([query])[0]
    with psycopg.connect(cfg.POSTGRES_DSN) as conn:
        register_vector(conn)
        rows = conn.execute(
            """SELECT chunk_id, doc_title, section, content,
                      1 - (embedding <=> %s) AS score
               FROM reg_chunks ORDER BY embedding <=> %s LIMIT %s""",
            (qv, qv, k),
        ).fetchall()
    return [
        {
            "chunk_id": r[0],
            "doc_title": r[1],
            "section": r[2],
            "content": r[3],
            "score": round(float(r[4]), 4),
        }
        for r in rows
    ]
