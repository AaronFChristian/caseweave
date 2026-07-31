from caseweave.guardrails.compliance import check


def test_legal_conclusion_blocked():
    r = check("The subject committed structuring across four deposits [F-001].")
    assert not r.passed
    assert r.violations


def test_consistent_with_language_allowed():
    r = check("The pattern is consistent with structuring [F-001, F-002].")
    assert r.passed


def test_guilty_of_blocked():
    r = check("The customer is guilty of money laundering [F-001].")
    assert not r.passed
