"""Golden-set construction.

The golden set is derived from the SAME ground-truth labels the Day 1
generator plants (`gt_typology`, `gt_label`) — not hand-written from
scratch. This is a deliberate design choice: for a portfolio project,
writing 50 reference SAR narratives by hand would take longer than
everything else in this repo combined, and it wouldn't test anything the
system doesn't already know about itself.

What IS hand-designed is the set of expected properties each case must
satisfy — that's the actual eval contract, not a paraphrase of the input:

  - True-positive alerts (gt_label=True) must end in status "ready_for_review"
    with attribution_coverage >= GATE_MIN_ATTRIBUTION_COVERAGE, UNLESS the
    evidence genuinely doesn't support a full narrative — in which case a
    correct refusal is scored as a pass, not a failure. A system that
    drafts a shaky narrative rather than admit insufficient evidence is
    the failure mode this project exists to prevent; the eval must not
    punish correct refusals.
  - False-positive alerts (gt_label=False) must NOT reach a drafted,
    passing narrative that asserts suspicion — either triage closes them,
    or if investigated, the narrative must not overstate the evidence.
  - Every case's triage typology_hypothesis, where the alert is a true
    positive, should be a non-"none" hypothesis (the model recognised
    something worth investigating), even if it doesn't name the exact
    typology label.

This keeps the golden set honest: it is testing the PIPELINE's judgment,
not just checking whether the model can recite the label we gave it.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from caseweave import config as cfg

GOLDEN_SET_PATH = cfg.DATA_DIR / "golden_set.json"


def build_golden_set(con: duckdb.DuckDBPyConnection, max_per_rule: int = 10) -> list[dict]:
    """Sample alerts across every rule and both classes (TP/FP), capped per
    rule so one noisy rule (R002 has 25 alerts) doesn't dominate the set."""
    rows = con.execute(
        """
        WITH ranked AS (
            SELECT *, row_number() OVER (
                PARTITION BY rule_code, gt_label ORDER BY alert_id
            ) AS rn
            FROM alerts
        )
        SELECT alert_id, rule_code, rule_name, gt_label, gt_typology,
               trigger_reason, tx_count, total_amount
        FROM ranked
        WHERE rn <= ?
        ORDER BY alert_id
        """,
        [max_per_rule],
    ).fetchdf()

    if rows.empty:
        raise ValueError("no alerts found — run the Day 1 pipeline first")

    cases = []
    for r in rows.itertuples(index=False):
        cases.append(
            {
                "alert_id": r.alert_id,
                "rule_code": r.rule_code,
                "gt_label": bool(r.gt_label),
                "gt_typology": r.gt_typology,
                "expectations": {
                    # A true positive should reach a passing narrative, OR a
                    # correct evidence-gap refusal — both count as success.
                    # It must never reach "ready_for_review" with prose that
                    # asserts suspicion while under-evidenced (that path is
                    # structurally impossible given the guardrail gate, but
                    # the eval checks it happened for the RIGHT reason).
                    "acceptable_status": (
                        ["ready_for_review", "refused"]
                        if r.gt_label
                        else ["closed", "ready_for_review", "refused"]
                    ),
                    "min_attribution_coverage_if_ready": 0.90,
                    "typology_hypothesis_should_be_specific": bool(r.gt_label),
                    "compliance_must_pass": True,  # nosec B105 - bool value, key name coincidentally contains "pass"
                },
            }
        )
    return cases


def save_golden_set(cases: list[dict], path: Path | None = None) -> Path:
    path = path or GOLDEN_SET_PATH
    path.write_text(json.dumps(cases, indent=2))
    return path


def load_golden_set(path: Path | None = None) -> list[dict]:
    path = path or GOLDEN_SET_PATH
    if not path.exists():
        raise FileNotFoundError(f"{path} not found — run scripts/build_golden_set.py first")
    return json.loads(path.read_text())
