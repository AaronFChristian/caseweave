"""Triage. First LLM touch in the pipeline, deliberately the cheapest —
Haiku, small context, structured output. Its job is a routing decision,
not an investigation."""

from __future__ import annotations

import json

from caseweave.agents.state import TriageVerdict
from caseweave.llm.gateway import CostLedger, call

SYSTEM_PROMPT = """You triage anti-money-laundering alerts for a bank's BSA \
team. Given an alert summary and subject KYC profile, decide whether the \
activity is explained by the customer's known profile (close) or warrants \
investigation. Respond with ONLY a JSON object, no other text:
{"risk_score": <0.0-1.0>, "typology_hypothesis": "<short label or 'none'>", \
"recommended_route": "close" | "investigate", "rationale": "<one sentence>"}"""


def triage(
    alert: dict, kyc: dict | None, cost_ledger: CostLedger | None = None, use_cache: bool = True
) -> TriageVerdict:
    kyc_line = (
        f"Subject: {kyc['name']} ({kyc['party_type']}), KYC risk {kyc['kyc_risk']}, "
        f"onboarded {kyc['onboarded_at']}"
        if kyc
        else "Subject: no KYC record found"
    )
    user = (
        f"Alert {alert['alert_id']} ({alert['rule_code']} {alert['rule_name']}): "
        f"{alert['trigger_reason']}\n"
        f"{kyc_line}\n"
        f"Transaction count: {alert['tx_count']}, total amount: ${alert['total_amount']:,.2f}"
    )
    result = call(
        task="triage",
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
        max_tokens=300,
        ledger=cost_ledger,
        use_cache=use_cache,
    )
    try:
        data = json.loads(result.text.strip().removeprefix("```json").removesuffix("```").strip())
        return TriageVerdict(**data)
    except Exception:  # noqa: BLE001
        # Fail safe toward investigation, never toward silent closure.
        return TriageVerdict(
            risk_score=0.5,
            typology_hypothesis="unknown",
            recommended_route="investigate",
            rationale="triage response could not be parsed; routed to investigation as a precaution",
        )
