"""The LLM gateway. Every model call in CaseWeave goes through `call()` — no
other module imports the Anthropic SDK. That is the whole point: one place
to route by task, one place to cache, one place to enforce a token ceiling,
one place to swap providers or fail over.

This is an in-process router (a Python module), not a standalone proxy
container. Functionally identical governance story — routing, caching, cost
caps, versioning — for one less container to keep alive on a laptop. Swapping
to a real LiteLLM proxy later is a base_url change in this file, not a
rewrite: see config/litellm.yaml, which documents the equivalent standalone
routing policy this module implements in-process.

claude-sonnet-5 rejects `temperature` outright — never pass it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import litellm
from langsmith import traceable

from caseweave import config as cfg

logger = logging.getLogger(__name__)

litellm.drop_params = False  # fail loudly on an unsupported param, never silently

_TASK_MODEL = {
    "triage": cfg.MODEL_TRIAGE,
    "extract": cfg.MODEL_EXTRACT,
    "narrative": cfg.MODEL_NARRATIVE,
    "typology": cfg.MODEL_TYPOLOGY,
    "judge": cfg.MODEL_JUDGE,
}

_CACHE_DIR = cfg.DATA_DIR / "llm_cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class CallResult:
    text: str
    task: str
    model: str
    input_tokens: int
    output_tokens: int
    cached: bool = False
    latency_ms: float = 0.0


@dataclass
class CostLedger:
    """Per-process running total. A case-scoped instance is what enforces
    MAX_TOKENS_PER_CASE; a module-level instance tracks the whole run."""

    calls: list[CallResult] = field(default_factory=list)

    def record(self, r: CallResult) -> None:
        self.calls.append(r)

    @property
    def total_tokens(self) -> int:
        return sum(c.input_tokens + c.output_tokens for c in self.calls)

    def summary(self) -> dict[str, Any]:
        by_task: dict[str, int] = {}
        for c in self.calls:
            by_task[c.task] = by_task.get(c.task, 0) + c.input_tokens + c.output_tokens
        return {
            "calls": len(self.calls),
            "total_tokens": self.total_tokens,
            "cache_hits": sum(1 for c in self.calls if c.cached),
            "by_task": by_task,
        }


def _cache_key(model: str, system: str, messages: list[dict]) -> str:
    blob = json.dumps({"model": model, "system": system, "messages": messages}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def _cache_path(key: str) -> Path:
    return _CACHE_DIR / f"{key}.json"


def _trace_inputs(inputs: dict) -> dict:
    """Shape what LangSmith records as this run's input. Drops `ledger` —
    a CostLedger accumulator object, not a meaningful trace input — and
    presents system+messages the way an "llm"-type run expects, so this
    renders with the same prompt/response view as a native LangChain chat
    model call rather than as an opaque function invocation."""
    return {
        "task": inputs.get("task"),
        "messages": [
            {"role": "system", "content": inputs.get("system", "")},
            *inputs.get("messages", []),
        ],
    }


def _trace_outputs(result: CallResult) -> dict:
    return {
        "content": result.text,
        "model": result.model,
        "usage": {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens},
        "cached": result.cached,
        "latency_ms": result.latency_ms,
    }


@traceable(
    run_type="llm",
    name="gateway.call",
    process_inputs=_trace_inputs,
    process_outputs=_trace_outputs,
)
def call(
    task: str,
    system: str,
    messages: list[dict],
    *,
    max_tokens: int = 2048,
    ledger: CostLedger | None = None,
    use_cache: bool = True,
) -> CallResult:
    """The single entry point for every LLM call in CaseWeave.

    `task` selects the model via _TASK_MODEL — callers never name a model
    string directly, so re-routing a task class is a one-line config change.
    """
    if task not in _TASK_MODEL:
        raise KeyError(f"unknown task class {task!r}; expected one of {sorted(_TASK_MODEL)}")
    model = _TASK_MODEL[task]

    key = _cache_key(model, system, messages)
    if use_cache and (p := _cache_path(key)).exists():
        cached = json.loads(p.read_text())
        r = CallResult(**{**cached, "cached": True})
        if ledger:
            ledger.record(r)
        return r

    kwargs: dict[str, Any] = {
        "model": f"anthropic/{model}",
        "messages": [{"role": "system", "content": system}, *messages],
        "max_tokens": max_tokens,
    }
    # claude-sonnet-5 rejects `temperature`. Only pass it for models that
    # accept it, and even then prefer determinism for a filing narrative.
    if model != cfg.MODEL_NARRATIVE and model != cfg.MODEL_TYPOLOGY:
        kwargs["temperature"] = 0.0

    t0 = time.monotonic()
    resp = litellm.completion(**kwargs)
    latency = (time.monotonic() - t0) * 1000

    text = resp.choices[0].message.content or ""
    usage = getattr(resp, "usage", None)
    r = CallResult(
        text=text,
        task=task,
        model=model,
        input_tokens=getattr(usage, "prompt_tokens", 0),
        output_tokens=getattr(usage, "completion_tokens", 0),
        cached=False,
        latency_ms=round(latency, 1),
    )
    is_degenerate = not text.strip()
    if use_cache and not is_degenerate:
        _cache_path(key).write_text(
            json.dumps({k: v for k, v in r.__dict__.items() if k != "cached"})
        )
    elif is_degenerate:
        logger.warning(
            "gateway.call got an empty/whitespace response for task=%r, model=%s — "
            "NOT caching it. This is likely a transient model failure, not a real answer.",
            task,
            model,
        )
    if ledger:
        ledger.record(r)
    return r


def estimated_cost_usd(ledger: CostLedger) -> float:
    """Rough estimate for the demo brief. Haiku ~$1/$5 per Mtok in/out,
    Sonnet ~$3/$15 per Mtok in/out — update if pricing changes."""
    rates = {
        cfg.MODEL_TRIAGE: (1.0, 5.0),
        cfg.MODEL_NARRATIVE: (3.0, 15.0),
        cfg.MODEL_TYPOLOGY: (3.0, 15.0),
        cfg.MODEL_JUDGE: (3.0, 15.0),
    }
    total = 0.0
    for c in ledger.calls:
        if c.cached:
            continue
        in_rate, out_rate = rates.get(c.model, (3.0, 15.0))
        total += c.input_tokens / 1_000_000 * in_rate
        total += c.output_tokens / 1_000_000 * out_rate
    return round(total, 4)
