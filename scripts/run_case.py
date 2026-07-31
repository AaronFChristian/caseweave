#!/usr/bin/env python3
"""Run one case end to end against the real API.

    export ANTHROPIC_API_KEY=sk-ant-...
    uv run python scripts/run_case.py               # picks a true-positive alert
    uv run python scripts/run_case.py --alert AL00012

Spends real tokens: one triage call (Haiku), one narrative call (Sonnet),
plus one judge call per sentence in the draft (Haiku). A typical case is a
few cents. Cost is printed at the end.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from caseweave.agents.graph import cost_ledger_for, run_case
from caseweave.db import duck
from caseweave.llm.gateway import estimated_cost_usd


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--alert", default=None, help="specific alert_id; default picks a true positive"
    )
    ap.add_argument("--no-cache", action="store_true", help="bypass the on-disk LLM response cache")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY not set. This script spends real money — set it explicitly.")

    tracing_on = os.environ.get("LANGCHAIN_TRACING_V2", "false").lower() == "true" and bool(
        os.environ.get("LANGCHAIN_API_KEY")
    )
    logfire_on = bool(os.environ.get("LOGFIRE_TOKEN"))
    print(
        f"LangSmith tracing: {'ON — project ' + os.environ.get('LANGCHAIN_PROJECT', 'default') if tracing_on else 'OFF (no LANGCHAIN_API_KEY / LANGCHAIN_TRACING_V2)'}"
    )
    print(f"Logfire tracing:   {'ON' if logfire_on else 'OFF (no LOGFIRE_TOKEN)'}")
    print()

    con = duck.connect(read_only=True)
    alert_id = args.alert
    if alert_id is None:
        row = con.execute(
            "SELECT alert_id FROM alerts WHERE gt_label ORDER BY alert_id LIMIT 1"
        ).fetchone()
        if row is None:
            sys.exit("no alerts in DuckDB — run `make day1` first")
        alert_id = row[0]

    print(f"running case for {alert_id} ...\n")
    state, ledger = run_case(con, alert_id, use_cache=not args.no_cache)
    cost_ledger = cost_ledger_for(state.case_id)

    print(f"status:              {state.status}")
    print(f"disposition:         {state.disposition}")
    print(f"triage route:        {state.triage.recommended_route if state.triage else 'n/a'}")
    print(f"facts in ledger:     {len(ledger.facts)}")
    print(f"attribution coverage:{state.attribution_coverage}")
    print(f"compliance passed:   {state.compliance_passed}")
    print()
    print("=" * 70)
    print(state.narrative_text)
    print("=" * 70)
    print()
    summary = cost_ledger.summary()
    print(f"LLM calls: {summary['calls']}  (cache hits: {summary['cache_hits']})")
    print(f"tokens:    {summary['total_tokens']:,}")
    print(f"est. cost: ${estimated_cost_usd(cost_ledger):.4f}")
    if tracing_on:
        print(
            f"\nCheck LangSmith → project '{os.environ.get('LANGCHAIN_PROJECT', 'default')}' "
            f"→ Traces for the run named 'CaseWeave investigation — {alert_id}'"
        )
    if logfire_on:
        print(
            "Check Logfire → your project's Live view for spans tagged with this case_id "
            f"({state.case_id})"
        )


if __name__ == "__main__":
    main()
