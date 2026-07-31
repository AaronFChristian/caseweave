"""Regression test for the bug found live on AL00004: an empty/degenerate
LLM response was cached permanently, so every subsequent call replayed the
same broken result forever — and the --no-cache flag couldn't rescue it
because use_cache was never threaded through triage()/draft_narrative() in
the first place. Both halves of that bug are tested here."""

from unittest.mock import patch

from caseweave.llm.gateway import CallResult, call


def _fake_empty_response(**kwargs):
    return CallResult(text="", task=kwargs["task"], model="mock", input_tokens=5, output_tokens=0)


def _fake_real_response(**kwargs):
    return CallResult(
        text="a real answer", task=kwargs["task"], model="mock", input_tokens=5, output_tokens=5
    )


def test_empty_response_is_not_cached(tmp_path, monkeypatch):
    import caseweave.llm.gateway as gw

    monkeypatch.setattr(gw, "_CACHE_DIR", tmp_path)

    with patch("litellm.completion") as mock_completion:
        mock_completion.return_value.choices = [
            type("C", (), {"message": type("M", (), {"content": ""})()})()
        ]
        mock_completion.return_value.usage = type(
            "U", (), {"prompt_tokens": 5, "completion_tokens": 0}
        )()

        call(task="triage", system="sys", messages=[{"role": "user", "content": "x"}])

    cache_files = list(tmp_path.glob("*.json"))
    assert not cache_files, "an empty response must never be written to the cache"


def test_real_response_is_still_cached(tmp_path, monkeypatch):
    import caseweave.llm.gateway as gw

    monkeypatch.setattr(gw, "_CACHE_DIR", tmp_path)

    with patch("litellm.completion") as mock_completion:
        mock_completion.return_value.choices = [
            type("C", (), {"message": type("M", (), {"content": "a real answer"})()})()
        ]
        mock_completion.return_value.usage = type(
            "U", (), {"prompt_tokens": 5, "completion_tokens": 5}
        )()

        call(task="triage", system="sys", messages=[{"role": "user", "content": "x"}])

    cache_files = list(tmp_path.glob("*.json"))
    assert len(cache_files) == 1, "a real response should still be cached normally"


def test_use_cache_threads_through_triage():
    """The second half of the bug: use_cache must actually reach gateway.call,
    not just be accepted and dropped by triage()."""
    from caseweave.agents.triage import triage

    with patch("caseweave.agents.triage.call") as mock_call:
        mock_call.return_value = CallResult(
            text='{"risk_score":0.5,"typology_hypothesis":"none",'
            '"recommended_route":"close","rationale":"x"}',
            task="triage",
            model="mock",
            input_tokens=1,
            output_tokens=1,
        )
        triage(
            {
                "alert_id": "A1",
                "rule_code": "R1",
                "rule_name": "n",
                "trigger_reason": "r",
                "tx_count": 1,
                "total_amount": 1.0,
            },
            None,
            use_cache=False,
        )

        assert mock_call.call_args.kwargs["use_cache"] is False


def test_use_cache_threads_through_draft_narrative():
    from caseweave.agents.narrative import draft_narrative
    from caseweave.llm.ledger import EvidenceLedger, FactKind

    ledger = EvidenceLedger("C1")
    ledger.add(FactKind.ALERT, "test", "a fact")
    ledger.freeze()

    with patch("caseweave.agents.narrative.call") as mock_call:
        mock_call.return_value = CallResult(
            text="narrative text [F-001]",
            task="narrative",
            model="mock",
            input_tokens=1,
            output_tokens=1,
        )
        draft_narrative("C1", ledger, use_cache=False)
        assert mock_call.call_args.kwargs["use_cache"] is False
