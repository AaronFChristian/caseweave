"""Typed domain contracts.

Everything that crosses a module boundary is a Pydantic model. Dict-passing
between agents is the single most common source of silent multi-agent bugs,
so the discipline starts on Day 1 in the data plane.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class PartyType(StrEnum):
    INDIVIDUAL = "individual"
    BUSINESS = "business"
    MERCHANT = "merchant"


class KycRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Channel(StrEnum):
    CASH_DEPOSIT = "cash_deposit"
    CASH_WITHDRAWAL = "cash_withdrawal"
    ACH = "ach"
    WIRE = "wire"
    CARD = "card"
    P2P = "p2p"
    CHECK = "check"


class Typology(StrEnum):
    """Ground-truth labels. Synthetic data only — never populated in production."""

    NONE = "none"
    STRUCTURING = "structuring"
    LAYERING_CYCLE = "layering_cycle"
    MULE_FAN_IN = "mule_fan_in"
    DORMANT_REACTIVATION = "dormant_reactivation"
    HIGH_RISK_CORRIDOR = "high_risk_corridor"


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Address(Base):
    address_id: str
    line1: str
    city: str
    region: str
    postcode: str
    country: str


class Party(Base):
    party_id: str
    canonical_id: str  # set by entity resolution; == party_id until resolved
    name: str
    first_name: str | None = None
    last_name: str | None = None
    dob: date | None = None
    party_type: PartyType
    country: str
    address_id: str
    device_id: str | None = None
    email: str | None = None
    phone: str | None = None
    kyc_risk: KycRisk
    industry: str | None = None
    onboarded_at: date


class Account(Base):
    account_id: str
    party_id: str
    account_type: str
    currency: str = "USD"
    opened_at: date
    status: str = "active"


class Transaction(Base):
    tx_id: str
    ts: datetime
    src_account_id: str | None  # None => cash deposit (no originating account)
    dst_account_id: str | None  # None => cash withdrawal
    src_party_id: str | None
    dst_party_id: str | None
    amount: float = Field(gt=0)
    currency: str = "USD"
    channel: Channel
    src_country: str
    dst_country: str
    memo: str = ""
    is_cash: bool = False
    is_cross_border: bool = False
    # Ground truth, synthetic only. Stripped before anything reaches a model.
    gt_typology: Typology = Typology.NONE
    gt_subject_party_id: str | None = None


class EntityLink(Base):
    link_id: str
    party_id_a: str
    party_id_b: str
    method: str  # dob_name | address | device
    score: float


class Alert(Base):
    alert_id: str
    created_at: datetime
    subject_party_id: str
    subject_account_id: str
    rule_code: str
    rule_name: str
    trigger_reason: str
    anomaly_score: float
    window_start: datetime
    window_end: datetime
    tx_count: int
    total_amount: float
    status: str = "open"
    gt_label: bool = False
    gt_typology: Typology = Typology.NONE
