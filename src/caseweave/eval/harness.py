"""Backtest harness. Runs every golden-set case through the ACTUAL LangGraph
pipeline (agents.graph.run_case) — not a re-implementation of scoring logic,
the real graph. Mock mode patches the gateway the same way
tests/test_graph_mocked.py does, so CI and local dev can run this for free;
live mode requires ANTHROPIC_API_KEY and is never invoked by CI.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb

from caseweave import config as cfg
from caseweave.eval import metrics
from caseweave.eval.golden_set import load_golden_set

REPORT_PATH = cfg.DATA_DIR / "eval_report.json"


# Same fixed mock as tests/test_graph_mocked.py, duplicated intentionally
# rather than imported from tests/ — this harness must be importable and
# runnable from a normal install without pytest as a dependency of the
# package itself, only of the dev extras.
def _fake_call(task, system, messages, *, max_tokens=2048, ledger=None, use_cache=True):
    import re

    from caseweave.llm.gateway import CallResult

    user_text = messages[-1]["content"]
    if task == "triage":
        text = (
            '{"risk_score": 0.8, "typology_hypothesis": "structuring", '
            '"recommended_route": "investigate", "rationale": "mocked"}'
        )
    elif task == "narrative":
        ids = re.findall(r"\[F-\d{3}\]", user_text)
        cite = " ".join(sorted(set(ids))[:3]) or "[F-001]"
        text = f"The subject conducted activity consistent with the alerted pattern {cite}."
    elif task == "judge":
        text = "SUPPORTED"
    else:
        text = "ok"
    return CallResult(text=text, task=task, model="mock", input_tokens=10, output_tokens=10)


@dataclass
class CaseResult:
    alert_id: str
    rule_code: str
    gt_label: bool
    status: str
    disposition: str | None
    attribution_coverage: float | None
    compliance_passed: bool | None
    typology_hypothesis: str | None
    fact_count: int
    metrics: dict[str, bool]
    metric_details: dict[str, str]
    passed: bool
    latency_ms: float
    error: str | None = None


def _run_one(con: duckdb.DuckDBPyConnection, case: dict) -> CaseResult:
    from caseweave.agents.graph import run_case

    t0 = time.monotonic()
    try:
        state, ledger = run_case(con, case["alert_id"])
        latency = (time.monotonic() - t0) * 1000

        checks = [
            metrics.status_acceptable(case, state.status),
            metrics.attribution_coverage_ok(case, state.status, state.attribution_coverage),
            metrics.compliance_ok(case, state.compliance_passed),
            metrics.triage_recognised_signal(
                case, state.triage.typology_hypothesis if state.triage else None
            ),
            metrics.no_false_clearance(case, state.status, state.disposition),
        ]
        return CaseResult(
            alert_id=case["alert_id"],
            rule_code=case["rule_code"],
            gt_label=case["gt_label"],
            status=state.status,
            disposition=state.disposition,
            attribution_coverage=state.attribution_coverage,
            compliance_passed=state.compliance_passed,
            typology_hypothesis=state.triage.typology_hypothesis if state.triage else None,
            fact_count=len(ledger.facts),
            metrics={m.name: m.passed for m in checks},
            metric_details={m.name: m.detail for m in checks},
            passed=all(m.passed for m in checks),
            latency_ms=round(latency, 1),
        )
    except Exception as exc:  # noqa: BLE001 - a case that crashes is a FAIL, not a harness abort
        return CaseResult(
            alert_id=case["alert_id"],
            rule_code=case["rule_code"],
            gt_label=case["gt_label"],
            status="error",
            disposition=None,
            attribution_coverage=None,
            compliance_passed=None,
            typology_hypothesis=None,
            fact_count=0,
            metrics=dict.fromkeys(metrics.ALL_METRICS, False),
            metric_details={},
            passed=False,
            latency_ms=(time.monotonic() - t0) * 1000,
            error=str(exc),
        )


def run_backtest(con: duckdb.DuckDBPyConnection, mock: bool = True) -> list[CaseResult]:
    cases = load_golden_set()

    if mock:
        from unittest.mock import patch

        with (
            patch("caseweave.llm.gateway.call", side_effect=_fake_call),
            patch("caseweave.agents.triage.call", side_effect=_fake_call),
            patch("caseweave.agents.narrative.call", side_effect=_fake_call),
            patch("caseweave.guardrails.attribution.call", side_effect=_fake_call),
        ):
            return [_run_one(con, c) for c in cases]

    return [_run_one(con, c) for c in cases]


def summarize(results: list[CaseResult]) -> dict:
    n = len(results)
    passed = sum(r.passed for r in results)
    by_metric = {
        m: sum(r.metrics.get(m, False) for r in results) / n if n else 0.0
        for m in metrics.ALL_METRICS
    }
    coverages = [r.attribution_coverage for r in results if r.attribution_coverage is not None]
    return {
        "total_cases": n,
        "passed": passed,
        "pass_rate": round(passed / n, 4) if n else 0.0,
        "by_metric_pass_rate": {k: round(v, 4) for k, v in by_metric.items()},
        "mean_attribution_coverage": round(sum(coverages) / len(coverages), 4)
        if coverages
        else None,
        "status_breakdown": {
            s: sum(1 for r in results if r.status == s) for s in {r.status for r in results}
        },
        "mean_latency_ms": round(sum(r.latency_ms for r in results) / n, 1) if n else 0.0,
        "errors": [r.alert_id for r in results if r.error],
    }


def save_report(results: list[CaseResult], summary: dict, path: Path | None = None) -> Path:
    path = path or REPORT_PATH
    path.write_text(
        json.dumps({"summary": summary, "cases": [asdict(r) for r in results]}, indent=2)
    )
    return path
