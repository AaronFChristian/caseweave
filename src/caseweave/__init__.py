"""CaseWeave — agentic AML investigation copilot."""

__version__ = "0.1.0"

# Configured once, here, rather than separately in every entry point
# (api/main.py, scripts/*.py, tests). With no LOGFIRE_TOKEN present this is
# still required to suppress LogfireNotConfiguredWarning and to make
# send_to_logfire='if-token-present' take effect — configure() itself is
# safe and local-only without a token, it just doesn't ship anything.
from caseweave.observability.tracing import configure_logfire

configure_logfire()
