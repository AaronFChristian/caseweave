"""Tests for the eval harness itself. Offline, mock mode only."""

import random
from datetime import date

import pandas as pd
import pytest

from caseweave import config as cfg
from caseweave.db import duck
from caseweave.eval import metrics
from caseweave.eval.golden_set import build_golden_set
from caseweave.generator.entities import build_population
from caseweave.generator.stream import generate
from caseweave.ingest.resolution import resolve
from caseweave.scoring.online import score_stream
from caseweave.scoring.rules import run_rules

TODAY = date(2026, 7, 29)


@pytest.fixture()
def seeded_duckdb(tmp_path):
    rng = random.Random(cfg.SEED)
    pop = build_population(rng, TODAY)
    txs = generate(pop, rng, TODAY)
    parties, links = resolve(pop.parties)
    con = duck.connect(tmp_path / "eval_test.duckdb")
    duck.replace_table(con, "addresses", pd.DataFrame([a.model_dump() for a in pop.addresses]))
    duck.replace_table(con, "parties", pd.DataFrame([p.model_dump() for p in parties]))
    duck.replace_table(con, "accounts", pd.DataFrame([a.model_dump() for a in pop.accounts]))
    duck.replace_table(con, "transactions", pd.DataFrame([t.model_dump() for t in txs]))
    duck.replace_table(con, "entity_links", pd.DataFrame([lk.model_dump() for lk in links]))
    tx_df = con.execute("SELECT * FROM transactions ORDER BY ts").fetchdf()
    tx_scores, acc_scores = score_stream(tx_df)
    duck.replace_table(
        con,
        "tx_scores",
        pd.DataFrame({"tx_id": list(tx_scores), "anomaly_score": list(tx_scores.values())}),
    )
    alerts = run_rules(con, acc_scores, use_graph=False)
    duck.replace_table(con, "alerts", pd.DataFrame([a.model_dump() for a in alerts]))
    yield con
    con.close()


def test_golden_set_covers_both_classes(seeded_duckdb):
    cases = build_golden_set(seeded_duckdb)
    assert any(c["gt_label"] for c in cases)
    assert any(not c["gt_label"] for c in cases)


def test_golden_set_caps_per_rule(seeded_duckdb):
    cases = build_golden_set(seeded_duckdb, max_per_rule=3)
    from collections import Counter

    counts = Counter((c["rule_code"], c["gt_label"]) for c in cases)
    assert all(v <= 3 for v in counts.values())


# --- metric unit tests: prove the gate can actually FAIL, not just pass ---


def test_metric_fails_on_missing_status():
    case = {"gt_label": True, "expectations": {"acceptable_status": ["ready_for_review"]}}
    r = metrics.status_acceptable(case, "closed")
    assert not r.passed


def test_metric_fails_on_low_coverage():
    case = {"expectations": {"min_attribution_coverage_if_ready": 0.90}}
    r = metrics.attribution_coverage_ok(case, "ready_for_review", 0.5)
    assert not r.passed


def test_metric_no_false_clearance_catches_missed_filing():
    """The zero-tolerance metric: a true positive silently closed with no
    narrative and no evidence-gap report must fail, unconditionally."""
    case = {"gt_label": True}
    r = metrics.no_false_clearance(case, "closed", "closed_no_narrative")
    assert not r.passed


def test_metric_no_false_clearance_allows_negative_closure():
    case = {"gt_label": False}
    r = metrics.no_false_clearance(case, "closed", "closed_no_narrative")
    assert r.passed
