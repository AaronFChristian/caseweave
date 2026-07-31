"""Input guardrail.

Wire memos are free text a customer or counterparty controls. They are the
one place in this pipeline an adversary can inject text. This module runs on
every memo before it is written into a Fact summary or passed to a model.

Two separate protections, deliberately not merged into one:
  1. Detection — flag memos that look like an injection attempt, for logging
     and for the case reviewer's attention.
  2. Neutralisation — the memo is ALWAYS wrapped as inert quoted data in the
     Fact summary (see tools.py), never concatenated into an instruction
     context. Detection improves the audit trail; neutralisation is what
     actually stops the attack, and it applies unconditionally, whether or
     not detection fires.

This is deliberately pattern-based rather than a learned classifier — cheap,
fast, no model call, and every flagged phrase is explainable to a reviewer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import logfire

_INJECTION_PATTERNS = [
    r"\bignore\s+(?:the\s+)?(?:prior|previous|all)\s+instructions?\b",
    r"\bnew\s+instructions?\b",
    r"\bdisregard\s+(?:the\s+)?(?:evidence|ledger|prior|above)\b",
    r"\bsystem\s*:\s*",
    r"</?(?:system|context|instructions?)>",
    r"\byou\s+are\s+now\b",
    r"\bassistant\s*,\s*(?:disregard|ignore|do not)\b",
    r"\bdo\s+not\s+report\b",
    r"\bmark\s+this\s+(?:subject|case|alert)\s+as\s+cleared\b",
    r"\bstate\s+no\s+suspicion\s+was\s+found\b",
]
_COMPILED = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]

MAX_MEMO_LEN = 200


@dataclass(frozen=True)
class ScanResult:
    flagged: bool
    matched_patterns: list[str]
    clean_text: str


def scan_memo(memo: str | None) -> ScanResult:
    """Detect likely injection attempts. Always returns a length-capped,
    control-character-stripped version of the text for downstream use,
    regardless of whether anything matched."""
    raw = memo or ""
    stripped = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", raw)
    truncated = stripped[:MAX_MEMO_LEN]

    hits = [p.pattern for p in _COMPILED if p.search(truncated)]
    return ScanResult(flagged=bool(hits), matched_patterns=hits, clean_text=truncated)


def sanitize_for_prompt(memo: str | None) -> tuple[str, bool]:
    """Return (safe_text, was_flagged). The returned text is ALWAYS the only
    form of the memo that may reach a prompt, and it is always wrapped in an
    explicit data delimiter by the caller — see narrative.py's DATA_BLOCK_TAG.
    Flagging does not change what is returned; it only changes what gets
    logged. A flagged memo is still quoted-and-inert, never dropped, because
    a dropped memo is missing evidence, which is worse than inert evidence."""
    result = scan_memo(memo)
    if result.flagged:
        logfire.warn(
            "injection guardrail: memo flagged",
            matched_patterns=result.matched_patterns,
            # The neutralised text, not the raw original — this log line
            # is itself downstream of the same sanitisation it's reporting
            # on, so it never becomes a second place an attack could land.
            neutralised_text=result.clean_text,
        )
    return result.clean_text, result.flagged
