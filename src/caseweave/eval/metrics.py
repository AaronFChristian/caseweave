"""Eval metrics. Each function takes a case's result and returns a bool plus
a short reason — no metric silently averages away why a case failed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MetricResult:
    name: str
    passed: bool
    detail: str


def status_acceptable(case: dict, status: str) -> MetricResult:
    ok = status in case["expectations"]["acceptable_status"]
    return MetricResult(
        "status_acceptable",
        ok,
        f"got {status!r}, expected one of {case['expectations']['acceptable_status']}",
    )


def attribution_coverage_ok(case: dict, status: str, coverage: float | None) -> MetricResult:
    if status != "ready_for_review":
        # a refusal or closure has no coverage requirement — the gate already
        # enforces coverage for anything that DOES reach ready_for_review
        return MetricResult("attribution_coverage_ok", True, "n/a (not ready_for_review)")
    threshold = case["expectations"]["min_attribution_coverage_if_ready"]
    ok = coverage is not None and coverage >= threshold
    return MetricResult(
        "attribution_coverage_ok", ok, f"coverage={coverage}, threshold={threshold}"
    )


def compliance_ok(case: dict, compliance_passed: bool | None) -> MetricResult:
    required = case["expectations"]["compliance_must_pass"]
    ok = (not required) or bool(compliance_passed)
    return MetricResult("compliance_ok", ok, f"compliance_passed={compliance_passed}")


def triage_recognised_signal(case: dict, typology_hypothesis: str | None) -> MetricResult:
    """For true positives, triage should hypothesise SOMETHING — not
    necessarily the exact ground-truth label (that would over-constrain a
    reasonable model to guess the internal label verbatim), just a
    non-trivial hypothesis rather than 'none' or empty."""
    if not case["expectations"]["typology_hypothesis_should_be_specific"]:
        return MetricResult("triage_recognised_signal", True, "n/a (ground-truth negative)")
    ok = typology_hypothesis is not None and typology_hypothesis.lower() not in (
        "none",
        "unknown",
        "",
    )
    return MetricResult(
        "triage_recognised_signal", ok, f"typology_hypothesis={typology_hypothesis!r}"
    )


def no_false_clearance(case: dict, status: str, disposition: str | None) -> MetricResult:
    """The one metric with zero tolerance: a true-positive alert must never
    be silently closed with no narrative and no evidence-gap report. That is
    the one failure mode worse than an over-cautious refusal — a missed
    filing, not just an incomplete one."""
    if not case["gt_label"]:
        return MetricResult("no_false_clearance", True, "n/a (ground-truth negative)")
    ok = not (status == "closed" and disposition == "closed_no_narrative")
    return MetricResult("no_false_clearance", ok, f"status={status}, disposition={disposition}")


ALL_METRICS = [
    "status_acceptable",
    "attribution_coverage_ok",
    "compliance_ok",
    "triage_recognised_signal",
    "no_false_clearance",
]
