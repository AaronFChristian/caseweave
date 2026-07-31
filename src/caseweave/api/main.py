"""FastAPI backend for the review console.

Read endpoints are thin wrappers around mcp_server.tool_logic — the same
tested functions the MCP server exposes, so "what the console shows" and
"what an MCP client sees" are guaranteed to be the same data, not two
independently-maintained read paths.

The one write endpoint (`POST /cases/{alert_id}/disposition`) is NOT exposed
via MCP — see the architecture note in mcp_server/server.py: MCP is
read-mostly, writes require a session-scoped console request. This file is
that boundary made concrete.

No authentication yet — this is explicitly a Day 3 portfolio gap, documented
in the README's known-limitations section. Do not expose this outside
localhost.
"""

from __future__ import annotations

from typing import Literal

import logfire
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from caseweave.db import duck
from caseweave.mcp_server import tool_logic as tl

app = FastAPI(title="CaseWeave Review Console API", version="0.1.0")
logfire.instrument_fastapi(app)  # every console request becomes a span, for free

# Wildcard CORS is a known production gap, called out here rather than
# silently shipped — fine for a local demo, not for anything public.
# Wildcard CORS is a known production gap; a regex scoped to localhost is
# the honest middle ground for a tool that's explicitly local-only. Both
# hostname forms are needed: browsers treat `localhost` and `127.0.0.1` as
# distinct origins even though they resolve to the same machine, and Vite's
# dev server (5173) and its `preview` build server (4173, or higher if that
# port is busy) are different origins from each other too.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)

_DISPOSITIONS: dict[str, dict] = {}  # in-memory; Day 3 scope, not persisted


def _con():
    return duck.connect(read_only=True)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/queue/summary")
def queue_summary() -> dict:
    con = _con()
    try:
        return tl.get_queue_summary(con)
    finally:
        con.close()


@app.get("/alerts")
def alerts(status: str | None = None, limit: int = 50) -> list[dict]:
    con = _con()
    try:
        rows = tl.list_alerts(con, status=status, limit=limit)
        for r in rows:
            r["disposition"] = _DISPOSITIONS.get(r["alert_id"], {}).get("decision")
        return rows
    finally:
        con.close()


@app.get("/alerts/{alert_id}")
def alert_detail(alert_id: str) -> dict:
    con = _con()
    try:
        result = tl.get_alert(con, alert_id)
        if result is None:
            raise HTTPException(404, f"unknown alert_id {alert_id!r}")
        result["disposition"] = _DISPOSITIONS.get(alert_id)
        return result
    finally:
        con.close()


@app.get("/alerts/{alert_id}/evidence")
def alert_evidence(alert_id: str) -> dict:
    """Read-only evidence assembly. Never calls the narrative model — see
    tool_logic.get_case_evidence's docstring and its test in
    tests/test_mcp_tools.py::test_get_case_evidence_does_not_call_narrative_model."""
    con = _con()
    try:
        result = tl.get_case_evidence(con, alert_id)
        if "error" in result:
            raise HTTPException(404, result["error"])
        return result
    finally:
        con.close()


@app.post("/alerts/{alert_id}/run-case")
def run_case_endpoint(alert_id: str) -> dict:
    """Runs the FULL pipeline including the narrative draft — this DOES
    call the LLM gateway and spends tokens. Kept as an explicit, separate
    endpoint from evidence-only reads so the console's default queue/detail
    views never trigger a cost; drafting is an opt-in action a reviewer
    clicks, not something that fires on page load."""
    from caseweave.agents.graph import cost_ledger_for, run_case
    from caseweave.llm.gateway import estimated_cost_usd

    con = duck.connect(read_only=True)  # read-only is fine; run_case doesn't write to DuckDB
    try:
        state, ledger = run_case(con, alert_id)
    except Exception as exc:
        raise HTTPException(500, f"case run failed: {exc}") from exc
    finally:
        con.close()

    cost_ledger = cost_ledger_for(state.case_id)
    return {
        "alert_id": alert_id,
        "case_id": state.case_id,
        "status": state.status,
        "disposition": state.disposition,
        "triage": state.triage.model_dump() if state.triage else None,
        "narrative_text": state.narrative_text,
        "narrative_cited_ids": state.narrative_cited_ids,
        "attribution_coverage": state.attribution_coverage,
        "compliance_passed": state.compliance_passed,
        "facts": [
            {"fact_id": f.fact_id, "kind": f.kind.value, "summary": f.summary} for f in ledger.facts
        ],
        "est_cost_usd": estimated_cost_usd(cost_ledger),
    }


class DispositionRequest(BaseModel):
    decision: Literal["approve", "edit", "reject"]
    reason_code: str
    reviewer: str
    edited_narrative: str | None = None


REASON_CODES = {
    "approve": ["fully_supported", "minor_edit_only"],
    "edit": ["fact_correction", "tone_adjustment", "added_context"],
    "reject": ["insufficient_evidence", "false_positive", "wrong_typology", "other"],
}


@app.post("/alerts/{alert_id}/disposition")
def set_disposition(alert_id: str, req: DispositionRequest) -> dict:
    """The one write endpoint. Reason code is required and validated against
    a controlled vocabulary — free-text reasons are not analysable, coded
    reasons become a dataset.

    Persisted twice, deliberately: the in-memory dict gives the console
    instant read-back for the current process's UI; the DuckDB
    `dispositions` table is the durable, queryable long-term memory that
    `load_prior_dispositions()` reads on every future case for the same
    subject. A real deployment additionally writes this to the
    hash-chained audit log described in the architecture — that piece
    remains undone; this endpoint closes the "does a prior review on this
    subject actually get remembered" gap, not the full audit-trail story.
    """
    if req.reason_code not in REASON_CODES.get(req.decision, []):
        raise HTTPException(
            400,
            f"invalid reason_code {req.reason_code!r} for decision {req.decision!r}; "
            f"expected one of {REASON_CODES.get(req.decision, [])}",
        )
    import uuid
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    record = {
        "alert_id": alert_id,
        "decision": req.decision,
        "reason_code": req.reason_code,
        "reviewer": req.reviewer,
        "edited_narrative": req.edited_narrative,
        "decided_at": now.isoformat(),
    }
    _DISPOSITIONS[alert_id] = record

    con = duck.connect(read_only=False)
    try:
        alert = tl.get_alert(con, alert_id)
        subject_party_id = alert["subject_party_id"] if alert else None
        duck.insert_disposition(
            con,
            str(uuid.uuid4()),
            alert_id,
            subject_party_id,
            req.decision,
            req.reason_code,
            req.reviewer,
            now,
        )
    except Exception as exc:  # noqa: BLE001 - the console's UI feedback must still succeed
        logfire.warn(
            "disposition not persisted to long-term memory", alert_id=alert_id, error=str(exc)
        )
    finally:
        con.close()

    return record


@app.get("/reason-codes")
def reason_codes() -> dict:
    return REASON_CODES
