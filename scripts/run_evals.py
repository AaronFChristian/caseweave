#!/usr/bin/env python3
"""Run the golden-set backtest.

    uv run python scripts/run_evals.py                 # mock mode, free, default
    uv run python scripts/run_evals.py --live           # real API, needs ANTHROPIC_API_KEY
    uv run python scripts/run_evals.py --fail-under 0.90

CI runs this in mock mode to guard the harness and guardrail logic against
regression. Mock mode cannot catch a real model drifting in behaviour — only
a live run does that, which is why `run_case.py`'s manual live check exists
alongside this, not instead of it.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from caseweave.db import duck
from caseweave.eval.harness import run_backtest, save_report, summarize


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="use the real API instead of mocks")
    ap.add_argument(
        "--fail-under",
        type=float,
        default=0.80,
        help="exit 1 if pass_rate is below this (CI gate threshold)",
    )
    args = ap.parse_args()

    if args.live and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("--live requires ANTHROPIC_API_KEY to be set")

    con = duck.connect(read_only=True)
    results = run_backtest(con, mock=not args.live)
    summary = summarize(results)
    path = save_report(results, summary)

    print(f"\n  CaseWeave golden-set backtest ({'LIVE' if args.live else 'MOCK'})")
    print("  " + "-" * 60)
    if not args.live:
        print("  NOTE: mock mode proves the graph/ledger/guardrail WIRING is correct.")
        print("  It does NOT prove narrative quality — the mock model always")
        print("  cooperates. A 100% mock pass rate is a plumbing signal, not a")
        print("  quality signal. Run --live to test actual model behaviour.")
        print("  " + "-" * 60)
    print(f"  cases:              {summary['total_cases']}")
    print(
        f"  passed:             {summary['passed']}/{summary['total_cases']} "
        f"({summary['pass_rate']:.1%})"
    )
    print(f"  mean coverage:      {summary['mean_attribution_coverage']}")
    print(f"  mean latency:       {summary['mean_latency_ms']} ms")
    print(f"  status breakdown:   {summary['status_breakdown']}")
    print("\n  by metric:")
    for m, rate in summary["by_metric_pass_rate"].items():
        print(f"    {m:<28} {rate:.1%}")
    if summary["errors"]:
        print(f"\n  ERRORS on: {summary['errors']}")
    print(f"\n  full report: {path}\n")

    failing = [r for r in results if not r.passed]
    if failing:
        print("  Failing cases:")
        for r in failing[:10]:
            bad = [k for k, v in r.metrics.items() if not v]
            print(f"    {r.alert_id} ({r.rule_code}, gt={r.gt_label}): failed {bad}")
        print()

    if summary["pass_rate"] < args.fail_under:
        print(f"  GATE FAILED: pass rate {summary['pass_rate']:.1%} < {args.fail_under:.0%}\n")
        return 1
    print(f"  GATE PASSED: pass rate {summary['pass_rate']:.1%} >= {args.fail_under:.0%}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
