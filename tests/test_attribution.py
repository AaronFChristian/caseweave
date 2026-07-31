from caseweave.llm.ledger import EvidenceLedger, FactKind


def _ledger():
    ledger = EvidenceLedger("C1")
    ledger.add(FactKind.TRANSACTION, "src", "Cash deposit of $9,200 on 2026-06-01")
    ledger.add(FactKind.TRANSACTION, "src", "Cash deposit of $9,400 on 2026-06-03")
    ledger.freeze()
    return ledger


def test_uncited_sentence_fails_structurally():
    from caseweave.guardrails.attribution import validate

    ledger = _ledger()
    text = "The subject made two large cash deposits."  # no citation at all
    result = validate(text, ledger, run_entailment=False)
    assert result.coverage == 0.0
    assert not result.passed


def test_valid_citation_passes_structural_check():
    from caseweave.guardrails.attribution import validate

    ledger = _ledger()
    text = "The subject deposited $9,200 in cash on 2026-06-01 [F-001]."
    result = validate(text, ledger, run_entailment=False)
    assert result.coverage == 1.0
    assert result.passed


def test_nonexistent_fact_id_fails():
    from caseweave.guardrails.attribution import validate

    ledger = _ledger()
    text = "The subject deposited funds [F-999]."
    result = validate(text, ledger, run_entailment=False)
    assert result.coverage == 0.0
    assert not result.passed


def test_refusal_message_lists_unsupported_sentences():
    from caseweave.guardrails.attribution import build_refusal, validate

    ledger = _ledger()
    text = "The subject is a known criminal with a long history."
    result = validate(text, ledger, run_entailment=False)
    refusal = build_refusal("CASE-1", result, ledger)
    assert "evidence-gap" in refusal.lower() or "Evidence-gap" in refusal
    assert "known criminal" in refusal
