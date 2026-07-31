"""The case graph.

Deterministic edges everywhere except the triage-route branch and the
autonomy-level branches, per the architecture: LLM/config-driven routing is
reserved for decisions that genuinely need judgment or governance, and every
other transition is a plain conditional.

Autonomy ladder (Layer 12). Every alert resolves an autonomy_level right
after load_alert, from config.AUTONOMY_LADDER keyed by rule_code:

    L0  agent disabled — alert sits untouched, no LLM calls at all
    L1  agent assembles evidence only, never drafts a narrative
    L2  agent drafts, human approves every case (today's default reality)
    L3  same as L2, plus the agent's rationale is surfaced as a suggestion
    L4  agent may close WITHOUT a human — but ONLY if
        eval.autonomy.is_eligible_for_autonomous_close() confirms real
        golden-set evidence backs that rule. Re-checked at execution time,
        every run — a config value alone is necessary but not sufficient.

Design correction made here, worth stating plainly: earlier versions of this
graph sent every triage "close" recommendation straight to an unconditional
auto-close with zero human involvement, for every rule, regardless of
autonomy level — that was L4 behavior happening by default at L2, which
contradicts the architecture's own claim that "a human approves every case."
This rewrite fixes that: a close recommendation now goes to a human-review
step UNLESS the alert's rule is actually L4 and eval-eligible.

Day 2 uses LangGraph's in-memory checkpointer. Day 3 swaps in the Postgres
checkpointer so a case can pause for human review across process restarts —
that's a one-line change (`MemorySaver()` -> `PostgresSaver(...)`), not a
graph rewrite.

Design note: the checkpointer serializes CaseState to msgpack after every
node. EvidenceLedger and CostLedger are live Python objects with no
serialization contract (and shouldn't have one — a ledger is working state,
not conversation history). They live in a process-local side-store keyed by
case_id; CaseState carries only the case_id and a serializable fact count.
Day 3, moving to a real checkpointer backend, this side-store becomes a
Postgres table keyed the same way.
"""

from __future__ import annotations

import logging

import duckdb
import logfire
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from caseweave import config as cfg
from caseweave.agents import tools
from caseweave.agents.narrative import draft_narrative
from caseweave.agents.state import CaseState
from caseweave.agents.triage import triage
from caseweave.eval.autonomy import is_eligible_for_autonomous_close
from caseweave.guardrails import compliance
from caseweave.guardrails.attribution import build_refusal, validate
from caseweave.llm.gateway import CostLedger
from caseweave.llm.ledger import EvidenceLedger

logger = logging.getLogger(__name__)

# Process-local side-store. Not thread-safe beyond what a single dev process
# needs; a real deployment replaces this with a Postgres-backed store keyed
# the same way, alongside the checkpointer swap noted above.
_LEDGERS: dict[str, EvidenceLedger] = {}
_COST_LEDGERS: dict[str, CostLedger] = {}


def ledger_for(case_id: str) -> EvidenceLedger:
    return _LEDGERS[case_id]


def cost_ledger_for(case_id: str) -> CostLedger:
    return _COST_LEDGERS[case_id]


def node_load_alert(state: CaseState, con: duckdb.DuckDBPyConnection) -> CaseState:
    ledger = EvidenceLedger(case_id=state.case_id)
    cost_ledger = CostLedger()
    _LEDGERS[state.case_id] = ledger
    _COST_LEDGERS[state.case_id] = cost_ledger

    alert = tools.load_alert(con, ledger, state.alert_id)
    level = cfg.AUTONOMY_LADDER.get(alert["rule_code"], cfg.DEFAULT_AUTONOMY_LEVEL)
    return state.model_copy(
        update={
            "alert": alert,
            "subject_party_id": alert["subject_party_id"],
            "autonomy_level": level,
            "fact_count": len(ledger.facts),
            "status": "loaded",
        }
    )


def route_after_load(state: CaseState) -> str:
    if state.autonomy_level == "L0":
        return "manual_only"
    return "triage"


def node_manual_only(state: CaseState) -> CaseState:
    """L0: the agent does nothing further. No triage call, no evidence
    gathering, zero token spend — this queue is analyst-only by design."""
    logfire.info(
        "autonomy L0: agent disabled, routed to manual review",
        case_id=state.case_id,
        rule_code=state.alert["rule_code"] if state.alert else None,
    )
    return state.model_copy(update={"status": "manual_only", "disposition": "agent_disabled"})


def node_triage(
    state: CaseState, con: duckdb.DuckDBPyConnection, use_cache: bool = True
) -> CaseState:
    assert state.alert is not None, "triage reached without load_alert having run"
    assert state.subject_party_id is not None
    ledger = ledger_for(state.case_id)
    cost_ledger = cost_ledger_for(state.case_id)
    kyc = tools.load_subject_kyc(con, ledger, state.subject_party_id)
    verdict = triage(state.alert, kyc, cost_ledger, use_cache=use_cache)
    return state.model_copy(
        update={"triage": verdict, "fact_count": len(ledger.facts), "status": "triaged"}
    )


def route_after_triage(state: CaseState) -> str:
    assert state.alert is not None
    wants_close = bool(state.triage and state.triage.recommended_route == "close")
    level = state.autonomy_level

    if wants_close:
        if level == "L1":
            return "close_suggested"
        eligible, _reason = is_eligible_for_autonomous_close(state.alert["rule_code"], level)
        return "auto_close" if eligible else "close_suggested"

    return "evidence_only" if level == "L1" else "gather_evidence"


def node_close_suggested(state: CaseState) -> CaseState:
    """L1/L2/L3, triage recommends closing: the agent's recommendation is
    surfaced, but a human makes the final call via the review console's
    disposition endpoint. This replaces the old unconditional auto-close."""
    return state.model_copy(update={"status": "ready_for_review", "disposition_suggested": "close"})


def node_auto_close(state: CaseState) -> CaseState:
    """L4 only, and re-validated here (not just trusted from the routing
    decision) — defense in depth in case config or eval evidence changed
    between routing and execution."""
    assert state.alert is not None, "auto_close reached without load_alert having run"
    eligible, reason = is_eligible_for_autonomous_close(
        state.alert["rule_code"], state.autonomy_level
    )
    logfire.info(
        "autonomy L4 auto-close decision",
        case_id=state.case_id,
        rule_code=state.alert["rule_code"],
        eligible=eligible,
        reason=reason,
    )
    if not eligible:
        return state.model_copy(
            update={"status": "ready_for_review", "disposition_suggested": "close"}
        )
    return state.model_copy(update={"status": "closed", "disposition": "closed_auto"})


def node_evidence_only(state: CaseState, con: duckdb.DuckDBPyConnection) -> CaseState:
    """L1, triage recommends investigating: assemble evidence and stop.
    No narrative is ever drafted at L1 — that is the entire point of the
    level. A human drafts manually from the assembled ledger."""
    assert state.alert is not None
    assert state.triage is not None
    assert state.subject_party_id is not None
    ledger = ledger_for(state.case_id)
    tools.load_subject_transactions(con, ledger, state.alert)
    tools.load_network_context(ledger, state.alert["subject_account_id"])
    tools.load_typology_matches(
        ledger, state.alert["trigger_reason"] + " " + (state.triage.typology_hypothesis or "")
    )
    tools.load_prior_dispositions(con, ledger, state.subject_party_id, state.alert_id)
    ledger.freeze()
    return state.model_copy(update={"fact_count": len(ledger.facts), "status": "ready_for_review"})


def node_gather_evidence(state: CaseState, con: duckdb.DuckDBPyConnection) -> CaseState:
    assert state.alert is not None
    assert state.triage is not None
    assert state.subject_party_id is not None
    ledger = ledger_for(state.case_id)
    tools.load_subject_transactions(con, ledger, state.alert)
    tools.load_network_context(ledger, state.alert["subject_account_id"])
    tools.load_typology_matches(
        ledger, state.alert["trigger_reason"] + " " + (state.triage.typology_hypothesis or "")
    )
    tools.load_prior_dispositions(con, ledger, state.subject_party_id, state.alert_id)
    ledger.freeze()
    update = {"fact_count": len(ledger.facts), "status": "evidenced"}
    # L3: surface the agent's own rationale as a suggestion; the human still
    # makes every decision, but sees what the agent would have recommended.
    if state.autonomy_level == "L3" and state.triage:
        update["disposition_suggested"] = state.triage.recommended_route
    return state.model_copy(update=update)


def node_draft_narrative(state: CaseState, use_cache: bool = True) -> CaseState:
    ledger = ledger_for(state.case_id)
    cost_ledger = cost_ledger_for(state.case_id)
    draft = draft_narrative(state.case_id, ledger, cost_ledger, use_cache=use_cache)
    return state.model_copy(
        update={
            "narrative_text": draft.narrative,
            "narrative_cited_ids": draft.cited_fact_ids,
            "status": "drafted",
        }
    )


def node_guardrail_gate(state: CaseState, use_cache: bool = True) -> CaseState:
    assert state.narrative_text is not None, "guardrail_gate reached before draft_narrative ran"
    ledger = ledger_for(state.case_id)
    cost_ledger = cost_ledger_for(state.case_id)
    attr = validate(state.narrative_text, ledger, cost_ledger, use_cache=use_cache)
    comp = compliance.check(state.narrative_text)

    if not attr.passed:
        refusal = build_refusal(state.case_id, attr, ledger)
        return state.model_copy(
            update={
                "narrative_text": refusal,
                "attribution_coverage": attr.coverage,
                "attribution_passed": False,
                "compliance_passed": comp.passed,
                "status": "refused",
                "disposition": "evidence_gap",
            }
        )

    if not comp.passed:
        return state.model_copy(
            update={
                "attribution_coverage": attr.coverage,
                "attribution_passed": True,
                "compliance_passed": False,
                "status": "refused",
                "disposition": "compliance_violation",
                "error": f"compliance filter blocked: {comp.violations}",
            }
        )

    return state.model_copy(
        update={
            "attribution_coverage": attr.coverage,
            "attribution_passed": True,
            "compliance_passed": True,
            "status": "ready_for_review",
        }
    )


def build_graph(con: duckdb.DuckDBPyConnection, use_cache: bool = True):
    g = StateGraph(CaseState)
    g.add_node("load_alert", lambda s: node_load_alert(s, con))
    g.add_node("manual_only", node_manual_only)
    g.add_node("triage", lambda s: node_triage(s, con, use_cache))
    g.add_node("close_suggested", node_close_suggested)
    g.add_node("auto_close", node_auto_close)
    g.add_node("evidence_only", lambda s: node_evidence_only(s, con))
    g.add_node("gather_evidence", lambda s: node_gather_evidence(s, con))
    g.add_node("draft_narrative", lambda s: node_draft_narrative(s, use_cache))
    g.add_node("guardrail_gate", lambda s: node_guardrail_gate(s, use_cache))

    g.set_entry_point("load_alert")
    g.add_conditional_edges(
        "load_alert", route_after_load, {"manual_only": "manual_only", "triage": "triage"}
    )
    g.add_edge("manual_only", END)
    g.add_conditional_edges(
        "triage",
        route_after_triage,
        {
            "close_suggested": "close_suggested",
            "auto_close": "auto_close",
            "evidence_only": "evidence_only",
            "gather_evidence": "gather_evidence",
        },
    )
    g.add_edge("close_suggested", END)
    g.add_edge("auto_close", END)
    g.add_edge("evidence_only", END)
    g.add_edge("gather_evidence", "draft_narrative")
    g.add_edge("draft_narrative", "guardrail_gate")
    g.add_edge("guardrail_gate", END)

    return g.compile(checkpointer=MemorySaver())


def run_case(
    con: duckdb.DuckDBPyConnection, alert_id: str, use_cache: bool = True
) -> tuple[CaseState, EvidenceLedger]:
    """Returns (state, ledger). The ledger is not part of the checkpointed
    state — see the side-store note above — so it is returned separately
    for callers (CLI, tests, the eventual review console) that need to
    inspect or render the evidence behind a case.

    Tracing note: a compiled StateGraph is a LangChain Runnable, so
    app.invoke() is traced to LangSmith automatically once
    LANGCHAIN_TRACING_V2=true and LANGCHAIN_API_KEY are set — no explicit
    LangSmith code is needed here. `tags` and `run_name` below only make
    the resulting trace filterable and legible (by alert_id) in the
    LangSmith UI; they do not turn tracing on or off.
    """
    app = build_graph(con, use_cache=use_cache)
    init = CaseState(case_id=f"CASE-{alert_id}", alert_id=alert_id)
    config = {
        "configurable": {"thread_id": init.case_id},
        "tags": ["caseweave", f"alert:{alert_id}"],
        "metadata": {"alert_id": alert_id, "case_id": init.case_id},
        "run_name": f"CaseWeave investigation — {alert_id}",
    }
    result = app.invoke(init, config=config)
    state = CaseState.model_validate(result)
    return state, ledger_for(state.case_id)
