#!/usr/bin/env python3
"""CaseWeave Day 1 data-plane CLI.

    python scripts/pipeline.py generate          # synthetic entities + transactions -> parquet
    python scripts/pipeline.py ingest            # parquet -> DuckDB (+ Neo4j, + Redpanda)
    python scripts/pipeline.py score             # River scoring + rule pack -> alerts
    python scripts/pipeline.py all               # the three above, in order

Flags:
    --direct       skip Redpanda, load DuckDB straight from parquet
    --no-graph     skip Neo4j (useful when only iterating on rules)
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from caseweave import config as cfg
from caseweave.db import duck
from caseweave.generator.entities import build_population
from caseweave.generator.stream import generate as generate_txs
from caseweave.ingest.resolution import merge_stats, resolve
from caseweave.scoring.online import score_stream
from caseweave.scoring.rules import run_rules

P_PARTIES = cfg.RAW_DIR / "parties.parquet"
P_ADDR = cfg.RAW_DIR / "addresses.parquet"
P_ACCOUNTS = cfg.RAW_DIR / "accounts.parquet"
P_TX = cfg.RAW_DIR / "transactions.parquet"
P_LINKS = cfg.RAW_DIR / "entity_links.parquet"


def _df(models) -> pd.DataFrame:
    return pd.DataFrame([m.model_dump() for m in models])


def cmd_generate(_args) -> None:
    # Seeded stdlib RNG is the correct choice here: this is synthetic data
    # generation and reproducibility matters more than unpredictability.
    rng = random.Random(cfg.SEED)  # noqa: S311
    today = date(2026, 7, 29)

    pop = build_population(rng, today)
    txs = generate_txs(pop, rng, today)
    parties, links = resolve(pop.parties)

    _df(pop.addresses).to_parquet(P_ADDR, index=False)
    _df(parties).to_parquet(P_PARTIES, index=False)
    _df(pop.accounts).to_parquet(P_ACCOUNTS, index=False)
    _df(txs).to_parquet(P_TX, index=False)
    _df(links).to_parquet(P_LINKS, index=False)

    stats = merge_stats(parties)
    planted = {t.gt_typology for t in txs if t.gt_typology.value != "none"}
    subjects = {t.gt_subject_party_id for t in txs if t.gt_subject_party_id}

    print(f"  addresses     {len(pop.addresses):>7,}")
    print(f"  parties       {len(parties):>7,}")
    print(f"  accounts      {len(pop.accounts):>7,}")
    print(f"  transactions  {len(txs):>7,}")
    print(f"  entity_links  {len(links):>7,}")
    print(
        f"  ER clusters   {stats['merged_clusters']:>7,}  "
        f"(planted duplicates: {len(pop.duplicate_pairs)})"
    )
    print(f"  typologies    {sorted(t.value for t in planted)}")
    print(f"  gt subjects   {len(subjects)}")


def cmd_ingest(args) -> None:
    if not P_TX.exists():
        sys.exit("no parquet found — run `generate` first")

    if not args.direct:
        from caseweave.ingest.consumer import drain_topic
        from caseweave.ingest.producer import replay_to_topic

        n = replay_to_topic(pd.read_parquet(P_TX))
        print(f"  produced      {n:>7,} -> {cfg.TOPIC_TRANSACTIONS}")
        tx = drain_topic(expected=n)
        print(f"  consumed      {len(tx):>7,}")
    else:
        tx = pd.read_parquet(P_TX)
        print(f"  direct load   {len(tx):>7,} (Redpanda bypassed)")

    con = duck.connect()
    duck.replace_table(con, "addresses", pd.read_parquet(P_ADDR))
    duck.replace_table(con, "parties", pd.read_parquet(P_PARTIES))
    duck.replace_table(con, "accounts", pd.read_parquet(P_ACCOUNTS))
    duck.replace_table(con, "transactions", tx)
    duck.replace_table(con, "entity_links", pd.read_parquet(P_LINKS))
    for k, v in duck.counts(con).items():
        print(f"  duckdb.{k:<14} {v:>7,}")
    con.close()

    if not args.no_graph:
        from caseweave.db.graph import build_graph

        counts = build_graph()
        for k, v in counts.items():
            print(f"  neo4j.{k:<15} {v:>7,}")


def cmd_score(args) -> None:
    con = duck.connect()
    tx = con.execute("SELECT * FROM transactions ORDER BY ts").fetchdf()
    if tx.empty:
        sys.exit("transactions table is empty — run `ingest` first")

    tx_scores, acc_scores = score_stream(tx)
    duck.replace_table(
        con,
        "tx_scores",
        pd.DataFrame({"tx_id": list(tx_scores), "anomaly_score": list(tx_scores.values())}),
    )
    print(f"  scored        {len(tx_scores):>7,} transactions")

    alerts = run_rules(con, acc_scores, use_graph=not args.no_graph)
    if not alerts:
        sys.exit("rule pack produced 0 alerts — thresholds are wrong, not the data")

    df = pd.DataFrame([a.model_dump() for a in alerts])
    duck.replace_table(con, "alerts", df)

    tp = int(df["gt_label"].sum())
    print(
        f"  alerts        {len(df):>7,}  (true positives {tp}, "
        f"false-positive rate {1 - tp / len(df):.1%})"
    )
    print("\n  by rule:")
    for code, grp in df.groupby("rule_code"):
        print(f"    {code}  {len(grp):>3}  tp={int(grp['gt_label'].sum()):>2}")
    con.close()


def main() -> None:
    ap = argparse.ArgumentParser(prog="pipeline")
    ap.add_argument("command", choices=["generate", "ingest", "score", "all"])
    ap.add_argument("--direct", action="store_true", help="bypass Redpanda")
    ap.add_argument("--no-graph", action="store_true", help="skip Neo4j load")
    args = ap.parse_args()

    steps = {"generate": cmd_generate, "ingest": cmd_ingest, "score": cmd_score}
    order = ["generate", "ingest", "score"] if args.command == "all" else [args.command]
    for name in order:
        print(f"\n[{name}]")
        steps[name](args)
    print()


if __name__ == "__main__":
    main()
