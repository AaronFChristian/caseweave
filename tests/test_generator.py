import random
from datetime import date

from caseweave import config as cfg
from caseweave.generator.entities import build_population
from caseweave.generator.stream import generate
from caseweave.models import Typology

TODAY = date(2026, 7, 29)


def _run(seed: int):
    rng = random.Random(seed)
    pop = build_population(rng, TODAY)
    return pop, generate(pop, rng, TODAY)


def test_generation_is_deterministic():
    _, a = _run(cfg.SEED)
    _, b = _run(cfg.SEED)
    assert [t.tx_id for t in a] == [t.tx_id for t in b]
    assert [t.amount for t in a] == [t.amount for t in b]


def test_all_typologies_planted():
    _, txs = _run(cfg.SEED)
    found = {t.gt_typology for t in txs} - {Typology.NONE}
    assert found == set(Typology) - {Typology.NONE}


def test_amounts_and_ordering_valid():
    _, txs = _run(cfg.SEED)
    assert all(t.amount > 0 for t in txs)
    assert all(t.src_account_id or t.dst_account_id for t in txs)
    assert txs == sorted(txs, key=lambda t: t.ts)


def test_adversarial_memos_present():
    _, txs = _run(cfg.SEED)
    hostile = [t for t in txs if "instruction" in t.memo.lower() or "disregard" in t.memo.lower()]
    assert len(hostile) >= cfg.N_ADVERSARIAL_MEMOS


def test_ground_truth_never_leaks_into_prose_fields():
    """gt_* are synthetic labels. They must never appear in a memo, because the
    memo is one of the few free-text fields a model will eventually read."""
    _, txs = _run(cfg.SEED)
    for t in txs:
        assert "gt_" not in t.memo
        assert t.gt_typology.value not in t.memo
