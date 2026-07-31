"""The EvidenceLedger.

Everything upstream of narrative drafting — triage, entity resolution, graph
traversal, typology retrieval — writes typed Facts here. Everything downstream
of it — the narrative agent, the attribution validator — reads ONLY from here.
No tool result, no raw database row, no retrieved chunk ever reaches the
narrative prompt directly.

The ledger is frozen (`freeze()`) before drafting starts. A frozen ledger
cannot accept new facts. This is what makes "every sentence cites a fact_id
that existed before generation began" a checkable invariant rather than a
hope.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FactKind(StrEnum):
    ALERT = "alert"
    TRANSACTION = "transaction"
    PARTY = "party"
    ACCOUNT = "account"
    GRAPH_PATTERN = "graph_pattern"
    TYPOLOGY_MATCH = "typology_match"
    REGULATORY_CITATION = "regulatory_citation"
    PRIOR_DISPOSITION = "prior_disposition"


class Fact(BaseModel):
    model_config = ConfigDict(frozen=True)

    fact_id: str
    kind: FactKind
    source: str  # e.g. "duckdb.transactions", "neo4j.detect_cycles", "pgvector.reg_chunks"
    summary: str  # one-line, human-readable — this is what the narrative agent cites
    payload: dict[str, Any] = Field(default_factory=dict)
    query_hash: str | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class LedgerFrozenError(RuntimeError):
    pass


class EvidenceLedger:
    """Append-only until frozen. One instance per case."""

    def __init__(self, case_id: str) -> None:
        self.case_id = case_id
        self._facts: dict[str, Fact] = {}
        self._n = 0
        self._frozen = False

    def add(
        self,
        kind: FactKind,
        source: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        query_hash: str | None = None,
    ) -> Fact:
        if self._frozen:
            raise LedgerFrozenError(
                f"ledger for case {self.case_id} is frozen — no new facts after freeze()"
            )
        self._n += 1
        fact = Fact(
            fact_id=f"F-{self._n:03d}",
            kind=kind,
            source=source,
            summary=summary,
            payload=payload or {},
            query_hash=query_hash,
        )
        self._facts[fact.fact_id] = fact
        return fact

    def get(self, fact_id: str) -> Fact | None:
        return self._facts.get(fact_id)

    def __contains__(self, fact_id: str) -> bool:
        return fact_id in self._facts

    @property
    def facts(self) -> list[Fact]:
        return list(self._facts.values())

    def freeze(self) -> None:
        self._frozen = True

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def as_numbered_list(self) -> str:
        """The ONLY representation of evidence the narrative prompt ever sees."""
        lines = [f"[{f.fact_id}] ({f.kind.value}) {f.summary}" for f in self.facts]
        return "\n".join(lines)

    def coverage_by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.facts:
            out[f.kind.value] = out.get(f.kind.value, 0) + 1
        return out
