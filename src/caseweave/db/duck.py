"""DuckDB analytical store.

DuckDB holds the flat transaction history and the alert queue. Neo4j holds the
relationship structure. Two stores, two jobs — do not try to do graph traversal
in SQL or aggregation in Cypher.

Known limitation, stated up front: DuckDB takes a global write lock, so this is
a single-writer design. Fine for a demo and for batch scoring, not a pattern to
carry into a concurrent production service.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from caseweave import config as cfg

SCHEMA = """
CREATE TABLE IF NOT EXISTS addresses (
    address_id VARCHAR PRIMARY KEY, line1 VARCHAR, city VARCHAR,
    region VARCHAR, postcode VARCHAR, country VARCHAR
);
CREATE TABLE IF NOT EXISTS parties (
    party_id VARCHAR PRIMARY KEY, canonical_id VARCHAR, name VARCHAR,
    first_name VARCHAR, last_name VARCHAR, dob DATE, party_type VARCHAR,
    country VARCHAR, address_id VARCHAR, device_id VARCHAR, email VARCHAR,
    phone VARCHAR, kyc_risk VARCHAR, industry VARCHAR, onboarded_at DATE
);
CREATE TABLE IF NOT EXISTS accounts (
    account_id VARCHAR PRIMARY KEY, party_id VARCHAR, account_type VARCHAR,
    currency VARCHAR, opened_at DATE, status VARCHAR
);
CREATE TABLE IF NOT EXISTS transactions (
    tx_id VARCHAR PRIMARY KEY, ts TIMESTAMP,
    src_account_id VARCHAR, dst_account_id VARCHAR,
    src_party_id VARCHAR, dst_party_id VARCHAR,
    amount DOUBLE, currency VARCHAR, channel VARCHAR,
    src_country VARCHAR, dst_country VARCHAR, memo VARCHAR,
    is_cash BOOLEAN, is_cross_border BOOLEAN,
    gt_typology VARCHAR, gt_subject_party_id VARCHAR
);
CREATE TABLE IF NOT EXISTS entity_links (
    link_id VARCHAR PRIMARY KEY, party_id_a VARCHAR, party_id_b VARCHAR,
    method VARCHAR, score DOUBLE
);
CREATE TABLE IF NOT EXISTS alerts (
    alert_id VARCHAR PRIMARY KEY, created_at TIMESTAMP,
    subject_party_id VARCHAR, subject_account_id VARCHAR,
    rule_code VARCHAR, rule_name VARCHAR, trigger_reason VARCHAR,
    anomaly_score DOUBLE, window_start TIMESTAMP, window_end TIMESTAMP,
    tx_count INTEGER, total_amount DOUBLE, status VARCHAR,
    gt_label BOOLEAN, gt_typology VARCHAR
);
CREATE TABLE IF NOT EXISTS tx_scores (
    tx_id VARCHAR PRIMARY KEY, anomaly_score DOUBLE
);
CREATE TABLE IF NOT EXISTS dispositions (
    disposition_id VARCHAR PRIMARY KEY, alert_id VARCHAR,
    subject_party_id VARCHAR, decision VARCHAR, reason_code VARCHAR,
    reviewer VARCHAR, decided_at TIMESTAMP
);
"""


# Table identifiers cannot be parameterised in SQL, so any identifier that
# reaches a query string is validated against this allowlist first. The names
# here are internal constants today, but an allowlist is cheap and it means a
# future caller that passes user input cannot turn this into an injection.
TABLES: frozenset[str] = frozenset(
    {
        "addresses",
        "parties",
        "accounts",
        "transactions",
        "entity_links",
        "alerts",
        "tx_scores",
        "dispositions",
    }
)


def _checked(table: str) -> str:
    if table not in TABLES:
        raise ValueError(f"unknown table {table!r}; expected one of {sorted(TABLES)}")
    return table


def connect(path: Path | None = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(path or cfg.DUCKDB_PATH), read_only=read_only)
    if not read_only:
        con.execute(SCHEMA)
    return con


def replace_table(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame) -> int:
    """Load a dataframe into an existing table.

    Guarded against the empty-result footgun: an upstream failure that yields
    zero rows must not silently wipe a populated table. Refuse instead.
    """
    if df is None or df.empty:
        raise ValueError(
            f"refusing to load '{table}': upstream produced 0 rows. "
            "Fix the producer before re-running — this guard exists because a "
            "silent empty load destroys every downstream metric."
        )
    t = _checked(table)
    con.register("_incoming", df)
    con.execute(f"DELETE FROM {t}")  # noqa: S608 - identifier allowlisted above
    con.execute(f"INSERT INTO {t} SELECT * FROM _incoming")  # noqa: S608
    con.unregister("_incoming")
    return len(df)


def insert_disposition(
    con: duckdb.DuckDBPyConnection,
    disposition_id: str,
    alert_id: str,
    subject_party_id: str | None,
    decision: str,
    reason_code: str,
    reviewer: str,
    decided_at,
) -> None:
    """Append one disposition record. Deliberately NOT replace_table — a
    disposition history accumulates across every case ever reviewed; wiping
    and reloading it on every write would destroy the exact long-term memory
    this table exists to provide."""
    con.execute(
        "INSERT INTO dispositions VALUES (?, ?, ?, ?, ?, ?, ?)",
        [disposition_id, alert_id, subject_party_id, decision, reason_code, reviewer, decided_at],
    )


def counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    out = {}
    for t in sorted(TABLES):
        row = con.execute(f"SELECT count(*) FROM {_checked(t)}").fetchone()  # noqa: S608
        assert row is not None, f"COUNT(*) on {t} returned no row — should be impossible"
        out[t] = row[0]
    return out
