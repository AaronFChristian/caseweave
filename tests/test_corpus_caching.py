"""Regression test for the model-reload bug caught in the Day 3 backtest:
30 golden-set cases each printed a fresh 'Loading weights', pushing mean
latency to 3.2s/case with every LLM call mocked and instant. The fix caches
SentenceTransformer at module level; this test proves it stays fixed."""

import sys
import types

import pytest


@pytest.fixture()
def fake_sentence_transformers(monkeypatch):
    load_count = {"n": 0}

    class FakeSentenceTransformer:
        def __init__(self, name):
            load_count["n"] += 1
            self.name = name

        def encode(self, texts, **kw):
            return [[0.1, 0.2]] * len(texts)

    fake_mod = types.ModuleType("sentence_transformers")
    fake_mod.SentenceTransformer = FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_mod)
    yield load_count


def test_embed_model_loaded_exactly_once_across_many_calls(fake_sentence_transformers):
    from caseweave.corpus.loader import _MODEL_CACHE, embed

    _MODEL_CACHE.clear()  # isolate from any prior test/run state
    for i in range(10):
        embed([f"query {i}"])

    assert fake_sentence_transformers["n"] == 1, (
        f"SentenceTransformer was instantiated {fake_sentence_transformers['n']} times "
        "across 10 embed() calls — the module-level cache regressed"
    )
