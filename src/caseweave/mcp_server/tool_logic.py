"""The actual logic behind every MCP tool, kept dependency-free from the MCP
SDK on purpose. This module can be imported and tested with plain pytest;
mcp_server/server.py is a thin wrapper that only adds the @tool decorators.
That split exists so "does the tool return the right data" and "is the MCP
protocol wiring correct" are two separate, separately-testable questions.

Every function here is read-only. Nothing in this module writes to DuckDB,
Neo4j, or pgvector — see the system-prompt-level note in the architecture:
MCP is a read-mostly surface; any write (disposition, approval) requires a
session token minted in the review console, never from an MCP client.
"""

from __future__ import annotations

import duckdb


def list_alerts(
    con: duckdb.DuckDBPyConnection, status: str | None = None, limit: int = 20
) -> list[dict]:
    """List alerts from the queue, optionally filtered by status.

    gt_label / gt_typology are included ONLY because this dataset is
    synthetic — they are the generator's planted ground truth, used by the
    console's demo mode to reveal whether the system's judgment on a case
    was actually right. No production deployment has these columns, and
    nothing in the agent pipeline is permitted to read them.
    """
    cols = (
        "alert_id, rule_code, rule_name, trigger_reason, anomaly_score, "
        "total_amount, status, gt_label, gt_typology"
    )
    if status:
        rows = con.execute(
            f"SELECT {cols} FROM alerts WHERE status = ? "  # noqa: S608 - column list is a fixed literal
            "ORDER BY anomaly_score DESC LIMIT ?",
            [status, limit],
        ).fetchdf()
    else:
        rows = con.execute(
            f"SELECT {cols} FROM alerts ORDER BY anomaly_score DESC LIMIT ?",  # noqa: S608
            [limit],
        ).fetchdf()
    return rows.to_dict("records")


def get_alert(con: duckdb.DuckDBPyConnection, alert_id: str) -> dict | None:
    """Full detail for one alert."""
    row = con.execute("SELECT * FROM alerts WHERE alert_id = ?", [alert_id]).fetchdf()
    if row.empty:
        return None
    return row.iloc[0].to_dict()


def get_case_evidence(con: duckdb.DuckDBPyConnection, alert_id: str) -> dict:
    """Run the case through triage + evidence gathering ONLY — no narrative
    drafting, no LLM narrative call. This is deliberately the read-only half
    of run_case: an MCP client asking 'what evidence exists for this alert'
    should not trigger a Sonnet call and a cost every time it's queried."""
    alert = get_alert(con, alert_id)
    if alert is None:
        return {"error": f"unknown alert_id {alert_id!r}"}

    from caseweave.agents import tools
    from caseweave.llm.ledger import EvidenceLedger

    ledger = EvidenceLedger(case_id=f"MCP-{alert_id}")
    tools.load_alert(con, ledger, alert_id)
    tools.load_subject_kyc(con, ledger, alert["subject_party_id"])
    tools.load_subject_transactions(con, ledger, alert)
    tools.load_network_context(ledger, alert["subject_account_id"])

    return {
        "alert_id": alert_id,
        "fact_count": len(ledger.facts),
        "facts": [
            {"fact_id": f.fact_id, "kind": f.kind.value, "summary": f.summary} for f in ledger.facts
        ],
    }


def search_typology(query: str, k: int = 3) -> list[dict]:
    """Search the regulatory/typology corpus."""
    try:
        from caseweave.corpus.loader import search
    except Exception as exc:  # noqa: BLE001 - corpus store may be unavailable
        return [{"error": f"corpus search unavailable: {exc}"}]
    return search(query, k=k)


def get_queue_summary(con: duckdb.DuckDBPyConnection) -> dict:
    """High-level queue stats — the kind of question a BSA officer asks
    first thing in the morning."""
    total = con.execute("SELECT count(*) FROM alerts").fetchone()[0]
    by_rule = con.execute(
        "SELECT rule_code, rule_name, count(*) AS n FROM alerts GROUP BY 1, 2 ORDER BY 1"
    ).fetchdf()
    by_status = con.execute("SELECT status, count(*) AS n FROM alerts GROUP BY 1").fetchdf()
    tp = con.execute("SELECT count(*) FROM alerts WHERE gt_label").fetchone()[0]
    n_tx = con.execute("SELECT count(*) FROM transactions").fetchone()[0]
    n_parties = con.execute("SELECT count(*) FROM parties").fetchone()[0]
    return {
        "total_alerts": total,
        "by_rule": by_rule.to_dict("records"),
        "by_status": by_status.to_dict("records"),
        # Synthetic-data-only stats, used by the console header to frame the
        # signal-to-noise problem the system exists to solve.
        "true_positives": tp,
        "false_positive_rate": round(1 - tp / total, 3) if total else 0.0,
        "transactions_monitored": n_tx,
        "parties_monitored": n_parties,
    }
