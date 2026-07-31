"""Neo4j transaction knowledge graph.

Model
-----
    (:Party)-[:OWNS]->(:Account)
    (:Account)-[:SENT_TO {tx_id, amount, ts, channel}]->(:Account)
    (:Party)-[:RESIDES_AT]->(:Address)
    (:Party)-[:USES_DEVICE]->(:Device)
    (:Party)-[:LINKED_TO {method, score}]-(:Party)

One SENT_TO relationship per transaction rather than an aggregated edge.
Aggregates lose the temporal ordering that every layering typology depends on.

Cypher policy
-------------
Every query in CYPHER_TEMPLATES is a fixed, parameterised string. On Day 2 the
agent selects a template by name and supplies parameters; it never authors
Cypher. That closes the injection path and makes every query a reviewable
artifact rather than a model output.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from neo4j import GraphDatabase

from caseweave import config as cfg

CONSTRAINTS = [
    "CREATE CONSTRAINT party_id IF NOT EXISTS FOR (p:Party) REQUIRE p.party_id IS UNIQUE",
    "CREATE CONSTRAINT account_id IF NOT EXISTS FOR (a:Account) REQUIRE a.account_id IS UNIQUE",
    "CREATE CONSTRAINT address_id IF NOT EXISTS FOR (x:Address) REQUIRE x.address_id IS UNIQUE",
    "CREATE CONSTRAINT device_id IF NOT EXISTS FOR (d:Device) REQUIRE d.device_id IS UNIQUE",
    "CREATE INDEX sent_ts IF NOT EXISTS FOR ()-[r:SENT_TO]-() ON (r.ts)",
    "CREATE INDEX party_canonical IF NOT EXISTS FOR (p:Party) ON (p.canonical_id)",
]

CYPHER_TEMPLATES: dict[str, str] = {
    # --- used by the Day 1 rule pack -------------------------------------
    "detect_cycles": """
        MATCH path = (a:Account)-[r:SENT_TO*3..6]->(a)
        WHERE ALL(i IN range(0, size(r)-2)
                  WHERE r[i].ts <= r[i+1].ts
                    AND duration.inDays(datetime(r[i].ts), datetime(r[i+1].ts)).days <= 7)
          AND ALL(x IN r WHERE x.amount >= $min_amount)
        WITH a, r,
             reduce(s = 0.0, x IN r | s + x.amount) AS total,
             r[0].ts AS ws, r[size(r)-1].ts AS we
        RETURN a.account_id AS acc, size(r) AS hops, total,
               ws, we, size(r) AS n
        ORDER BY total DESC
    """,
    # --- reserved for the Day 2 network-analysis agent -------------------
    "khop_counterparties": """
        MATCH (s:Account {account_id: $account_id})-[r:SENT_TO*1..2]-(c:Account)
        WHERE c.account_id <> $account_id
        RETURN DISTINCT c.account_id AS counterparty,
               size(r) AS hops
        LIMIT $limit
    """,
    "fan_in_senders": """
        MATCH (s:Account)-[r:SENT_TO]->(t:Account {account_id: $account_id})
        WHERE datetime(r.ts) >= datetime($since)
        RETURN s.account_id AS sender, count(r) AS n, sum(r.amount) AS total
        ORDER BY total DESC LIMIT $limit
    """,
    "shared_identifiers": """
        MATCH (p:Party {party_id: $party_id})-[:RESIDES_AT|USES_DEVICE]->(x)<-[:RESIDES_AT|USES_DEVICE]-(q:Party)
        WHERE q.party_id <> $party_id
        RETURN q.party_id AS party_id, q.name AS name, labels(x)[0] AS shared_via
        LIMIT $limit
    """,
    "path_to_high_risk": """
        MATCH (s:Account {account_id: $account_id}), (t:Account)<-[:OWNS]-(p:Party)
        WHERE p.kyc_risk = 'high' AND t.account_id <> $account_id
        MATCH path = shortestPath((s)-[:SENT_TO*1..4]-(t))
        RETURN t.account_id AS target, p.party_id AS party_id,
               length(path) AS hops LIMIT $limit
    """,
}


def driver():
    return GraphDatabase.driver(cfg.NEO4J_URI, auth=(cfg.NEO4J_USER, cfg.NEO4J_PASSWORD))


def build_graph() -> dict[str, int]:
    """Load parquet into Neo4j. Idempotent: safe to re-run."""
    parties = pd.read_parquet(cfg.RAW_DIR / "parties.parquet")
    accounts = pd.read_parquet(cfg.RAW_DIR / "accounts.parquet")
    addresses = pd.read_parquet(cfg.RAW_DIR / "addresses.parquet")
    txs = pd.read_parquet(cfg.RAW_DIR / "transactions.parquet")
    links = pd.read_parquet(cfg.RAW_DIR / "entity_links.parquet")

    if txs.empty or parties.empty:
        raise ValueError("refusing to build graph from empty parquet — run `generate` first")

    txs = txs.dropna(subset=["src_account_id", "dst_account_id"]).copy()
    # Neo4j's datetime() parses strict ISO-8601 only. pandas' default
    # astype(str) on a Timestamp gives "2026-06-23 16:33:00" (space
    # separator), which datetime() rejects with a SyntaxError at query time,
    # not at load time — the load succeeds and R006 silently skips instead.
    # strftime with a literal "T" is what datetime() actually accepts.
    txs["ts"] = pd.to_datetime(txs["ts"]).dt.strftime("%Y-%m-%dT%H:%M:%S")
    parties = parties.copy()
    for col in ("dob", "onboarded_at"):
        parties[col] = pd.to_datetime(parties[col]).dt.strftime("%Y-%m-%d")
        parties[col] = parties[col].where(parties[col].notna(), None)

    with driver() as drv, drv.session() as ses:
        ses.run("MATCH (n) DETACH DELETE n")
        for c in CONSTRAINTS:
            ses.run(c)

        ses.run(
            """UNWIND $rows AS r MERGE (x:Address {address_id: r.address_id})
               SET x.city = r.city, x.region = r.region, x.postcode = r.postcode""",
            rows=addresses.to_dict("records"),
        )
        ses.run(
            """UNWIND $rows AS r MERGE (p:Party {party_id: r.party_id})
               SET p.canonical_id = r.canonical_id, p.name = r.name,
                   p.party_type = r.party_type, p.country = r.country,
                   p.kyc_risk = r.kyc_risk, p.industry = r.industry
               MERGE (a:Address {address_id: r.address_id})
               MERGE (p)-[:RESIDES_AT]->(a)
               FOREACH (_ IN CASE WHEN r.device_id IS NULL THEN [] ELSE [1] END |
                   MERGE (d:Device {device_id: r.device_id})
                   MERGE (p)-[:USES_DEVICE]->(d))""",
            rows=parties.to_dict("records"),
        )
        ses.run(
            """UNWIND $rows AS r MERGE (a:Account {account_id: r.account_id})
               SET a.account_type = r.account_type, a.status = r.status
               WITH a, r MATCH (p:Party {party_id: r.party_id})
               MERGE (p)-[:OWNS]->(a)""",
            rows=accounts.to_dict("records"),
        )
        ses.run(
            """UNWIND $rows AS r
               MATCH (p:Party {party_id: r.party_id_a}), (q:Party {party_id: r.party_id_b})
               MERGE (p)-[l:LINKED_TO {method: r.method}]->(q) SET l.score = r.score""",
            rows=links.to_dict("records"),
        )

        for i in range(0, len(txs), 5000):
            ses.run(
                """UNWIND $rows AS r
                   MATCH (s:Account {account_id: r.src_account_id}),
                         (d:Account {account_id: r.dst_account_id})
                   CREATE (s)-[:SENT_TO {tx_id: r.tx_id, amount: r.amount,
                                         ts: r.ts, channel: r.channel,
                                         memo: r.memo}]->(d)""",
                rows=txs.iloc[i : i + 5000].to_dict("records"),
            )

        return {
            "nodes": ses.run("MATCH (n) RETURN count(n) AS c").single()["c"],
            "sent_to": ses.run("MATCH ()-[r:SENT_TO]->() RETURN count(r) AS c").single()["c"],
            "linked_to": ses.run("MATCH ()-[r:LINKED_TO]->() RETURN count(r) AS c").single()["c"],
        }


def run_template(name: str, **params: Any) -> list[dict]:
    """Execute a named template. Unknown names are rejected, not interpolated."""
    if name not in CYPHER_TEMPLATES:
        raise KeyError(f"unknown Cypher template: {name!r}")
    with driver() as drv, drv.session() as ses:
        return [dict(r) for r in ses.run(CYPHER_TEMPLATES[name], **params)]


def detect_cycles(min_amount: float = 10_000.0) -> pd.DataFrame:
    """R006 — layering cycle detection. Graph-only by necessity.

    The originator of a round-trip ring sends on the first hop and receives on
    the last, so it never presents the inbound-then-outbound pattern the
    tabular pass-through rule looks for. No amount of SQL tuning finds it.
    """
    rows = run_template("detect_cycles", min_amount=min_amount)
    if not rows:
        return pd.DataFrame(columns=["acc", "ws", "we", "n", "total"])
    df = pd.DataFrame(rows)
    df = df.groupby("acc", as_index=False).agg(
        ws=("ws", "min"), we=("we", "max"), n=("n", "max"), total=("total", "max")
    )
    df["ws"] = pd.to_datetime(df["ws"])
    df["we"] = pd.to_datetime(df["we"])
    return df
