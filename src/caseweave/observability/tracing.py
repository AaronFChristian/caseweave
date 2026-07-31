"""Tracing configuration.

Two backends, two different jobs, deliberately not merged into one:

    LangSmith  — the LangGraph orchestration layer and the LLM gateway call.
                 Needs zero explicit wiring: LangGraph's compiled graph is a
                 LangChain Runnable, and any Runnable.invoke() is traced
                 automatically once LANGCHAIN_TRACING_V2=true and
                 LANGCHAIN_API_KEY are in the environment — no code in this
                 module talks to LangSmith directly for that part. The one
                 exception is llm/gateway.py's call(), which is a plain
                 function (not a Runnable, since it calls litellm directly
                 rather than a LangChain chat model) and is explicitly
                 decorated with @traceable there so the LLM call itself
                 shows prompt/response/token detail rather than appearing
                 as an opaque node.

    Logfire    — everything else: tool calls (DuckDB/Neo4j/pgvector),
                 all three guardrails, and the FastAPI layer. Configured
                 explicitly via configure_logfire(), called once at process
                 start by every entry point (api/main.py, scripts/run_case.py,
                 scripts/pipeline.py) via `import caseweave.observability`.

Both are safe with no credentials present — this has to stay true, since
the 51-test offline suite and CI's mock-mode eval gate run with neither
LANGCHAIN_API_KEY nor LOGFIRE_TOKEN set, and must not fail or slow down
because of it.
"""

from __future__ import annotations

import logging

import logfire

from caseweave import config as cfg

logger = logging.getLogger(__name__)

_configured = False


def configure_logfire() -> None:
    """Idempotent — safe to call from every entry point without risk of
    double-configuring. send_to_logfire='if-token-present' is Logfire's own
    built-in safe-by-default: with no LOGFIRE_TOKEN, spans are created
    locally (near-zero overhead) and never sent anywhere, which is exactly
    the behaviour the offline test suite depends on.
    """
    global _configured
    if _configured:
        return

    try:
        logfire.configure(
            token=cfg.LOGFIRE_TOKEN,
            service_name="caseweave",
            send_to_logfire="if-token-present",
            console=False,
        )
        # record='failure' (not the default 'all') is deliberate: 'all'
        # creates a full trace span for EVERY successful Pydantic
        # validation across the entire dependency tree — not just our own
        # models, but litellm's internal request/response models and
        # LangSmith's RunTree too. Verified live: a single guardrail check
        # produced 141 child spans this way, burying the one log line that
        # actually mattered under noise. 'failure' emits lightweight
        # metrics on success and a full span only when a validation
        # genuinely fails — silent when things are fine, loud when they're
        # not, which is the correct default for a trace meant to be read
        # by a human.
        logfire.instrument_pydantic(record="failure")
        _configured = True
    except Exception:  # tracing must never be able to take the app down, any failure here is caught (BLE001 suppressed file-wide, see pyproject.toml)
        # Tracing must never be able to take the application down. A
        # misconfigured token or a network hiccup during configure() is a
        # degraded-observability problem, not an application-down problem.
        logger.warning("Logfire configuration failed; continuing without tracing", exc_info=True)
