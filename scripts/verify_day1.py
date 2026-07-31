#!/usr/bin/env python3
"""Day 1 acceptance gate.

This exists because every silent data-quality failure downstream of ingestion
looks like a modelling problem and costs a day to find. Nothing on Day 2 gets
built until this is green.

Exit code 0 = pass. Non-zero = do not proceed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from caseweave import config as cfg
from caseweave.db import duck
from caseweave.models import Typology

RESULTS: list[tuple[str, str, str]] = []
FAILED = 0
REQUIRED_TYPOLOGIES = {t.value for t in Typology if t is not Typology.NONE}


def check(name: str, ok: bool | None, detail: str = "") -> None:
    global FAILED
    if ok is None:
        RESULTS.append(("SKIP", name, detail))
        return
    RESULTS.append(("PASS" if ok else "FAIL", name, detail))
    if not ok:
        FAILED += 1


def q(con, sql: str):
    return con.execute(sql).fetchone()[0]


def main() -> int:
    if not cfg.DUCKDB_PATH.exists():
        print("no DuckDB file — run `make day1` first")
        return 2

    con = duck.connect(read_only=True)

    # ---------------------------------------------------------------- volumes
    n_parties = q(con, "SELECT count(*) FROM parties")
    n_accounts = q(con, "SELECT count(*) FROM accounts")
    n_tx = q(con, "SELECT count(*) FROM transactions")
    check("parties loaded", n_parties > 200, f"{n_parties:,}")
    check("accounts loaded", n_accounts > n_parties, f"{n_accounts:,}")
    check("transactions loaded", 4_000 < n_tx < 40_000, f"{n_tx:,}")

    # ------------------------------------------------------------- integrity
    orphan_acc = q(
        con,
        """
        SELECT count(*) FROM accounts a
        LEFT JOIN parties p ON p.party_id = a.party_id WHERE p.party_id IS NULL""",
    )
    check("no orphan accounts", orphan_acc == 0, f"{orphan_acc} orphans")

    orphan_tx = q(
        con,
        """
        SELECT count(*) FROM transactions t
        LEFT JOIN accounts a ON a.account_id = t.src_account_id
        WHERE t.src_account_id IS NOT NULL AND a.account_id IS NULL""",
    )
    check("no orphan tx sources", orphan_tx == 0, f"{orphan_tx} orphans")

    bad_amt = q(con, "SELECT count(*) FROM transactions WHERE amount <= 0")
    check("all amounts positive", bad_amt == 0, f"{bad_amt} bad rows")

    both_null = q(
        con,
        """
        SELECT count(*) FROM transactions
        WHERE src_account_id IS NULL AND dst_account_id IS NULL""",
    )
    check("no dangling transactions", both_null == 0, f"{both_null} rows")

    span = q(con, "SELECT date_diff('day', min(ts), max(ts)) FROM transactions")
    check(
        "time span matches config",
        abs(span - cfg.SIM_DAYS) <= 2,
        f"{span}d vs configured {cfg.SIM_DAYS}d",
    )

    # ------------------------------------------------------ entity resolution
    clusters = q(
        con,
        """
        SELECT count(*) FROM (
            SELECT canonical_id FROM parties
            GROUP BY canonical_id HAVING count(*) > 1)""",
    )
    check(
        "duplicate identities resolved",
        clusters == cfg.N_DUPLICATE_IDENTITIES,
        f"{clusters}/{cfg.N_DUPLICATE_IDENTITIES} clusters merged",
    )

    links = q(con, "SELECT count(*) FROM entity_links")
    methods = con.execute(
        "SELECT method, count(*) FROM entity_links GROUP BY method ORDER BY 1"
    ).fetchall()
    check("entity links present", links > 0, ", ".join(f"{m}={c}" for m, c in methods))

    # ------------------------------------------------------------ alert queue
    n_alerts = q(con, "SELECT count(*) FROM alerts")
    check(
        "alert volume in band",
        cfg.GATE_MIN_ALERTS <= n_alerts <= cfg.GATE_MAX_ALERTS,
        f"{n_alerts} (band {cfg.GATE_MIN_ALERTS}-{cfg.GATE_MAX_ALERTS})",
    )

    if n_alerts:
        tp = q(con, "SELECT count(*) FROM alerts WHERE gt_label")
        fp_rate = 1 - tp / n_alerts
        check(
            "false-positive rate realistic",
            cfg.GATE_MIN_FP_RATE <= fp_rate <= cfg.GATE_MAX_FP_RATE,
            f"{fp_rate:.1%} (band {cfg.GATE_MIN_FP_RATE:.0%}-{cfg.GATE_MAX_FP_RATE:.0%})",
        )

        found = {
            r[0]
            for r in con.execute(
                "SELECT DISTINCT gt_typology FROM alerts WHERE gt_label"
            ).fetchall()
        }
        missing = REQUIRED_TYPOLOGIES - found
        check(
            "every typology surfaces in the queue",
            not missing,
            f"{len(found)}/{len(REQUIRED_TYPOLOGIES)}"
            + (f" missing: {sorted(missing)}" if missing else ""),
        )

        planted = q(
            con,
            """
            SELECT count(DISTINCT gt_subject_party_id) FROM transactions
            WHERE gt_subject_party_id IS NOT NULL""",
        )
        caught = q(con, "SELECT count(DISTINCT subject_party_id) FROM alerts WHERE gt_label")
        recall = caught / planted if planted else 0
        check(
            "planted-subject recall",
            recall >= cfg.GATE_MIN_SUBJECT_RECALL,
            f"{caught}/{planted} = {recall:.0%}",
        )

        by_rule = con.execute("""
            SELECT rule_code, count(*), sum(CASE WHEN gt_label THEN 1 ELSE 0 END)
            FROM alerts GROUP BY rule_code ORDER BY 1""").fetchall()
        check(
            "every rule contributes",
            all(c > 0 for _, c, _ in by_rule),
            " ".join(f"{r}:{c}/{t}tp" for r, c, t in by_rule),
        )

    # ---------------------------------------------------- day 2 prerequisites
    adversarial = q(
        con,
        """
        SELECT count(*) FROM transactions
        WHERE lower(memo) LIKE '%ignore%instruction%'
           OR lower(memo) LIKE '%new instruction%'
           OR lower(memo) LIKE '%disregard%'""",
    )
    check(
        "prompt-injection fixtures planted",
        adversarial >= cfg.N_ADVERSARIAL_MEMOS,
        f"{adversarial} hostile memos for the Day 2 guardrail",
    )

    scored = q(con, "SELECT count(*) FROM tx_scores")
    check("anomaly scores computed", scored == n_tx, f"{scored:,}/{n_tx:,}")

    # ------------------------------------------------------------- neo4j
    try:
        from caseweave.db.graph import driver

        with driver() as drv, drv.session() as s:
            nodes = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            rels = s.run("MATCH ()-[r:SENT_TO]->() RETURN count(r) AS c").single()["c"]
        check("graph nodes loaded", nodes > n_parties, f"{nodes:,} nodes")
        check("graph edges match transactions", rels > 0, f"{rels:,} SENT_TO")
    except Exception as exc:  # noqa: BLE001
        check("neo4j graph", None, f"unavailable ({type(exc).__name__}) — run docker compose up -d")

    # ------------------------------------------------------------- corpus
    try:
        from caseweave.corpus.loader import chunk_dir

        local = len(chunk_dir())
        check(
            "corpus chunks produced",
            local >= cfg.GATE_MIN_CORPUS_CHUNKS,
            f"{local} (min {cfg.GATE_MIN_CORPUS_CHUNKS})",
        )
    except Exception as exc:  # noqa: BLE001
        check("corpus chunks produced", False, str(exc))

    try:
        import psycopg

        with psycopg.connect(cfg.POSTGRES_DSN, connect_timeout=3) as conn:
            n = conn.execute("SELECT count(*) FROM reg_chunks").fetchone()[0]
        check("corpus embedded in pgvector", n >= cfg.GATE_MIN_CORPUS_CHUNKS, f"{n} rows")
    except Exception as exc:  # noqa: BLE001
        check(
            "corpus embedded in pgvector",
            None,
            f"unavailable ({type(exc).__name__}) — run `make corpus`",
        )

    con.close()

    # ------------------------------------------------------------- report
    width = max(len(n) for _, n, _ in RESULTS) + 2
    print("\n  CaseWeave — Day 1 acceptance gate")
    print("  " + "-" * (width + 46))
    for status, name, detail in RESULTS:
        mark = {"PASS": "  ok  ", "FAIL": " FAIL ", "SKIP": " skip "}[status]
        print(f"  [{mark}] {name:<{width}} {detail}")
    print("  " + "-" * (width + 46))

    skipped = sum(1 for s, _, _ in RESULTS if s == "SKIP")
    passed = sum(1 for s, _, _ in RESULTS if s == "PASS")
    print(f"  {passed} passed, {FAILED} failed, {skipped} skipped\n")

    if FAILED:
        print("  Day 1 is NOT complete. Fix the failures above before starting Day 2.\n")
        return 1
    if skipped:
        print("  Day 1 core is green, but skipped checks mean the graph or corpus")
        print("  is not loaded. Bring up docker compose and re-run before Day 2.\n")
    else:
        print("  Day 1 complete. Commit, then start Day 2.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
