"""Typed case state. Every LangGraph node reads and returns this shape —
no dict-passing between nodes, which is the most common source of silent
multi-agent bugs.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class TriageVerdict(BaseModel):
    risk_score: float
    typology_hypothesis: str
    recommended_route: str  # "close" | "investigate"
    rationale: str


class CaseState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    case_id: str
    alert_id: str
    alert: dict[str, Any] | None = None
    subject_party_id: str | None = None

    # Autonomy ladder (Layer 12). Resolved once, right after load_alert, from
    # config.AUTONOMY_LADDER keyed by rule_code — see graph.py's routing.
    autonomy_level: str = "L2"
    # Set on the close-branch at L1/L2/L3 (agent recommends closing, but a
    # human must confirm) or on the investigate-branch at L3 (agent's triage
    # rationale surfaced as a suggestion). None at L4 auto-close, since there
    # the agent's recommendation and the final disposition are the same act.
    disposition_suggested: str | None = None

    triage: TriageVerdict | None = None

    # The live EvidenceLedger/CostLedger are NOT stored here — they aren't
    # msgpack-serializable and the checkpointer serializes state after every
    # node. They live in agents.graph's process-local side-store, keyed by
    # case_id. fact_count is the serializable signal that state carries.
    fact_count: int = 0

    narrative_text: str | None = None
    narrative_cited_ids: list[str] = []

    attribution_coverage: float | None = None
    attribution_passed: bool | None = None
    compliance_passed: bool | None = None

    status: str = "pending"  # pending -> triaged -> evidenced -> drafted -> reviewed
    disposition: str | None = None  # None until a human sets it
    review_reason_code: str | None = None

    error: str | None = None
