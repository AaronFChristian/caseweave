import random
from datetime import date

from caseweave import config as cfg
from caseweave.generator.entities import build_population
from caseweave.ingest.resolution import merge_stats, resolve

TODAY = date(2026, 7, 29)


def _pop():
    return build_population(random.Random(cfg.SEED), TODAY)


def test_planted_duplicates_all_merge():
    pop = _pop()
    resolved, _ = resolve(pop.parties)
    canon = {p.party_id: p.canonical_id for p in resolved}
    for a, b in pop.duplicate_pairs:
        assert canon[a] == canon[b], f"{a} and {b} should resolve to one entity"


def test_merge_count_matches_planted():
    pop = _pop()
    resolved, _ = resolve(pop.parties)
    stats = merge_stats(resolved)
    assert stats["merged_clusters"] == cfg.N_DUPLICATE_IDENTITIES


def test_weak_signals_link_but_do_not_merge():
    """Sharing an address is a lead, not an identity. Only dob+surname merges."""
    pop = _pop()
    resolved, links = resolve(pop.parties)
    canon = {p.party_id: p.canonical_id for p in resolved}
    addr_only = [
        lk
        for lk in links
        if lk.method == "address" and canon[lk.party_id_a] != canon[lk.party_id_b]
    ]
    assert addr_only, "expected address links that did not force a merge"


def test_resolution_is_idempotent():
    pop = _pop()
    once, _ = resolve(pop.parties)
    twice, _ = resolve(once)
    assert [p.canonical_id for p in once] == [p.canonical_id for p in twice]
