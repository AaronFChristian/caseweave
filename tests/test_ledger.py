import pytest

from caseweave.llm.ledger import EvidenceLedger, FactKind, LedgerFrozenError


def test_fact_ids_sequential():
    ledger = EvidenceLedger("C1")
    a = ledger.add(FactKind.ALERT, "src", "alert summary")
    b = ledger.add(FactKind.PARTY, "src", "party summary")
    assert a.fact_id == "F-001"
    assert b.fact_id == "F-002"


def test_frozen_ledger_rejects_new_facts():
    ledger = EvidenceLedger("C1")
    ledger.add(FactKind.ALERT, "src", "x")
    ledger.freeze()
    with pytest.raises(LedgerFrozenError):
        ledger.add(FactKind.PARTY, "src", "y")


def test_contains_checks_fact_id():
    ledger = EvidenceLedger("C1")
    f = ledger.add(FactKind.ALERT, "src", "x")
    assert f.fact_id in ledger
    assert "F-999" not in ledger


def test_numbered_list_format():
    ledger = EvidenceLedger("C1")
    ledger.add(FactKind.ALERT, "src", "something happened")
    out = ledger.as_numbered_list()
    assert "[F-001]" in out
    assert "something happened" in out
