"""Autonomy ladder eligibility.

The architecture's actual governance promise is that raising a queue's
autonomy level is a change-controlled, evidence-based decision — not just a
config edit. This module is what makes that literal: L4 auto-close is only
reachable for a rule if the golden-set backtest has enough samples for that
rule AND a high enough pass rate. Setting a rule to L4 in config alone does
nothing; the eligibility check re-validates against the actual eval report
on every single case, so stale or missing evidence silently degrades back
to human review rather than silently trusting a config value.
"""

from __future__ import annotations

import json

from caseweave import config as cfg


def is_eligible_for_autonomous_close(rule_code: str, level: str) -> tuple[bool, str]:
    """Returns (eligible, reason). `reason` is always populated — this is
    logged on every autonomous-close decision so an examiner can see
    exactly why the system was or wasn't allowed to skip a human."""
    if level != "L4":
        return False, f"level is {level!r}, not L4"

    path = cfg.DATA_DIR / "eval_report.json"
    if not path.exists():
        return False, "no eval_report.json found — run `make evals` before granting L4"

    try:
        report = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return False, f"eval_report.json unreadable: {exc}"

    cases = [c for c in report.get("cases", []) if c.get("rule_code") == rule_code]
    if len(cases) < cfg.AUTONOMY_L4_MIN_SAMPLES:
        return False, (
            f"only {len(cases)} golden-set cases for {rule_code}, "
            f"need >= {cfg.AUTONOMY_L4_MIN_SAMPLES}"
        )

    pass_rate = sum(1 for c in cases if c.get("passed")) / len(cases)
    if pass_rate < cfg.AUTONOMY_L4_MIN_PASS_RATE:
        return False, (
            f"{rule_code} eval pass rate {pass_rate:.0%} < "
            f"{cfg.AUTONOMY_L4_MIN_PASS_RATE:.0%} required for L4"
        )

    return (
        True,
        f"{rule_code} eligible: {pass_rate:.0%} pass rate over {len(cases)} golden-set cases",
    )
