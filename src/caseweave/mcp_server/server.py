"""FastMCP server exposing CaseWeave's read-only investigation tools.

This module is intentionally thin: every tool below is a one-line call into
tool_logic.py, which is tested independently with plain pytest (see
tests/test_mcp_tools.py). This file only needs to be right about the MCP
protocol wiring — decorators, docstrings-as-tool-descriptions, connection
lifecycle — not about the business logic.

Run standalone for local testing against Claude Desktop or any MCP client:

    uv run python -m caseweave.mcp_server.server

Add to Claude Desktop's config (claude_desktop_config.json):

    {
      "mcpServers": {
        "caseweave": {
          "command": "uv",
          "args": ["run", "--directory", "/path/to/caseweave",
                    "python", "-m", "caseweave.mcp_server.server"]
        }
      }
    }

NOTE ON VERSIONING: this file targets the mcp SDK's 1.x `FastMCP` API
(`mcp.server.fastmcp.FastMCP`), pinned in pyproject.toml as `mcp>=1.2,<2`.
The 2.x line renamed this to `MCPServer` with a different decorator surface
— if upgrading, this file needs a rewrite, not a patch.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from caseweave.db import duck
from caseweave.mcp_server import tool_logic as tl

mcp = FastMCP("caseweave")


def _con():
    # Read-only connection per call. DuckDB's global write lock means a
    # long-lived shared connection across an MCP server's request lifecycle
    # is the wrong pattern anyway — open, query, close.
    return duck.connect(read_only=True)


@mcp.tool()
def list_alerts(status: str | None = None, limit: int = 20) -> list[dict]:
    """List AML alerts from the investigation queue, optionally filtered by
    status (e.g. 'open', 'closed'). Returns rule code, trigger reason,
    anomaly score, and amount for each alert, ordered by anomaly score."""
    con = _con()
    try:
        return tl.list_alerts(con, status=status, limit=limit)
    finally:
        con.close()


@mcp.tool()
def get_alert(alert_id: str) -> dict:
    """Get full detail for one alert by ID (e.g. 'AL00001')."""
    con = _con()
    try:
        result = tl.get_alert(con, alert_id)
        return result if result is not None else {"error": f"unknown alert_id {alert_id!r}"}
    finally:
        con.close()


@mcp.tool()
def get_case_evidence(alert_id: str) -> dict:
    """Assemble and return the evidence ledger for an alert — subject KYC,
    transaction history, and network context. This does NOT draft a
    narrative and does NOT call the narrative model; it is read-only
    evidence assembly, safe to call repeatedly without cost."""
    con = _con()
    try:
        return tl.get_case_evidence(con, alert_id)
    finally:
        con.close()


@mcp.tool()
def search_typology(query: str, k: int = 3) -> list[dict]:
    """Search the AML typology and regulatory-guidance corpus for passages
    relevant to a query (e.g. 'structuring cash deposits')."""
    return tl.search_typology(query, k=k)


@mcp.tool()
def get_queue_summary() -> dict:
    """Get high-level statistics on the alert queue: total count, breakdown
    by detection rule, breakdown by status."""
    con = _con()
    try:
        return tl.get_queue_summary(con)
    finally:
        con.close()


if __name__ == "__main__":
    mcp.run()
