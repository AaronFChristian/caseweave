"""Tracing configuration. Import this module once, before anything else
that needs to be traced, and both backends are ready."""

from caseweave.observability.tracing import configure_logfire  # noqa: F401
