from caseweave import config as cfg
from caseweave.corpus.loader import MAX_CHARS, chunk_dir


def test_chunks_produced():
    chunks = chunk_dir()
    assert len(chunks) >= cfg.GATE_MIN_CORPUS_CHUNKS


def test_chunks_respect_size_bounds():
    for c in chunk_dir():
        assert 60 <= len(c.content) <= MAX_CHARS + 200


def test_chunks_carry_citable_provenance():
    """A chunk that cannot be cited by document and section is useless to an
    attribution-conditioned narrative."""
    for c in chunk_dir():
        assert c.doc_title and c.section and c.doc_id


def test_chunk_ids_unique():
    ids = [c.chunk_id for c in chunk_dir()]
    assert len(ids) == len(set(ids))
