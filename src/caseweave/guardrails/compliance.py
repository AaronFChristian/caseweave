"""Compliance filter.

Independent of attribution: a sentence can be perfectly evidenced and still
be a legal conclusion the institution has no authority to make. "Consistent
with structuring" is filing language; "committed structuring" is not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import logfire

_FORBIDDEN = [
    r"\bcommitted\s+(?:a\s+)?(?:crime|fraud|money laundering|structuring|felony)\b",
    r"\bis\s+guilty\s+of\b",
    r"\bengaged\s+in\s+criminal\b",
    r"\bviolat(?:ed|es|ing)\s+(?:the\s+)?law\b",
    r"\bproven?\s+to\s+(?:have|be)\b",
    r"\bconvicted\b",
]
_COMPILED = [re.compile(p, re.IGNORECASE) for p in _FORBIDDEN]


@dataclass
class ComplianceResult:
    passed: bool
    violations: list[str]


def check(narrative: str) -> ComplianceResult:
    with logfire.span("guardrail.compliance") as span:
        hits = [p.pattern for p in _COMPILED if p.search(narrative)]
        span.set_attribute("passed", not hits)
        span.set_attribute("n_violations", len(hits))
        if hits:
            logfire.warn(
                "compliance filter blocked narrative", n_violations=len(hits), patterns=hits
            )
        return ComplianceResult(passed=not hits, violations=hits)
