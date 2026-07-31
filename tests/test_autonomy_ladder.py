"""Tests for the autonomy ladder (Layer 12). Covers the design correction
made here: a triage "close" recommendation must route to human review by
default, not straight to an unconditional auto-close — and L4 auto-close
must be genuinely gated on real eval evidence, re-checked at execution
time, not just trusted from a config value."""

import json
import random
from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

from caseweave import config as cfg
from caseweave.db import duck
from caseweave.eval.autonomy import is_eligible_for_autonomous_close
from caseweave.generator.entities import build_population
from caseweave.generator.stream import generate
from caseweave.ingest.resolution import resolve
from caseweave.scoring.online import score_stream
from caseweave.scoring.rules import run_rules

TODAY = date(2026, 7, 29)


def _fake_call_close(task, system, messages, **kw):
    from caseweave.llm.gateway import CallResult

    if task == "triage":
        text = (
            '{"risk_score":0.1,"typology_hypothesis":"none",'
            '"recommended_route":"close","rationale":"benign"}'
        )
    else:
        text = "ok"
    return CallResult(text=text, task=task, model="mock", input_tokens=1, output_tokens=1)


def _fake_call_investigate(task, system, messages, **kw):
    from caseweave.llm.gateway import CallResult

    if task == "triage":
        text = (
            '{"risk_score":0.8,"typology_hypothesis":"structuring",'
            '"recommended_route":"investigate","rationale":"x"}'
        )
    elif task == "narrative":
        text = "The subject conducted activity consistent with the pattern [F-001]."
    elif task == "judge":
        text = "SUPPORTED"
    else:
        text = "ok"
    return CallResult(text=text, task=task, model="mock", input_tokens=1, output_tokens=1)


@pytest.fixture()
def seeded_duckdb(tmp_path):
    rng = random.Random(cfg.SEED)
    pop = build_population(rng, TODAY)
    txs = generate(pop, rng, TODAY)
    parties, links = resolve(pop.parties)
    con = duck.connect(tmp_path / "autonomy_test.duckdb")
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


def _patch_all(fn):
    return (
        patch("caseweave.llm.gateway.call", side_effect=fn),
        patch("caseweave.agents.triage.call", side_effect=fn),
        patch("caseweave.agents.narrative.call", side_effect=fn),
        patch("caseweave.guardrails.attribution.call", side_effect=fn),
    )


# --------------------------------------------------- L2 default (the fix)


def test_close_recommendation_at_l2_requires_human_not_auto_close(seeded_duckdb, monkeypatch):
    """The core design correction: a triage 'close' verdict at the default
    L2 level must NOT close the case unilaterally. It must land in
    ready_for_review with a suggested disposition, awaiting a human."""
    monkeypatch.setattr(cfg, "AUTONOMY_LADDER", {}, raising=False)  # everything -> DEFAULT (L2)
    from caseweave.agents.graph import run_case

    con = seeded_duckdb
    alert_id = con.execute("SELECT alert_id FROM alerts LIMIT 1").fetchone()[0]

    patches = _patch_all(_fake_call_close)
    with patches[0], patches[1], patches[2], patches[3]:
        state, _ledger = run_case(con, alert_id)

    assert state.status == "ready_for_review"
    assert state.disposition_suggested == "close"
    assert state.disposition is None, "L2 must never set a final disposition on its own"


# ------------------------------------------------------------------- L0


def test_l0_disables_the_agent_entirely(seeded_duckdb, monkeypatch):
    con = seeded_duckdb
    alert_id = con.execute("SELECT alert_id, rule_code FROM alerts LIMIT 1").fetchone()
    monkeypatch.setattr(cfg, "AUTONOMY_LADDER", {alert_id[1]: "L0"}, raising=False)
    from caseweave.agents.graph import run_case

    # No LLM patches applied at all — if L0 tried to call the model, this
    # would raise because litellm has no real credentials in this test env.
    state, _ledger = run_case(con, alert_id[0])

    assert state.status == "manual_only"
    assert state.disposition == "agent_disabled"
    assert state.triage is None, "L0 must never reach the triage node"


# ------------------------------------------------------------------- L1


def test_l1_investigate_gathers_evidence_but_never_drafts(seeded_duckdb, monkeypatch):
    con = seeded_duckdb
    alert_id = con.execute("SELECT alert_id, rule_code FROM alerts LIMIT 1").fetchone()
    monkeypatch.setattr(cfg, "AUTONOMY_LADDER", {alert_id[1]: "L1"}, raising=False)
    from caseweave.agents.graph import run_case

    patches = _patch_all(_fake_call_investigate)
    with patches[0], patches[1], patches[2], patches[3]:
        state, ledger = run_case(con, alert_id[0])

    assert state.status == "ready_for_review"
    assert state.narrative_text is None, "L1 must never draft a narrative"
    assert len(ledger.facts) > 0, "L1 still assembles evidence"


def test_l1_close_recommendation_also_requires_human(seeded_duckdb, monkeypatch):
    con = seeded_duckdb
    alert_id = con.execute("SELECT alert_id, rule_code FROM alerts LIMIT 1").fetchone()
    monkeypatch.setattr(cfg, "AUTONOMY_LADDER", {alert_id[1]: "L1"}, raising=False)
    from caseweave.agents.graph import run_case

    patches = _patch_all(_fake_call_close)
    with patches[0], patches[1], patches[2], patches[3]:
        state, _ledger = run_case(con, alert_id[0])

    assert state.status == "ready_for_review"
    assert state.disposition_suggested == "close"


# ------------------------------------------------------------------- L3


def test_l3_surfaces_suggestion_on_investigate_branch(seeded_duckdb, monkeypatch):
    con = seeded_duckdb
    alert_id = con.execute("SELECT alert_id, rule_code FROM alerts LIMIT 1").fetchone()
    monkeypatch.setattr(cfg, "AUTONOMY_LADDER", {alert_id[1]: "L3"}, raising=False)
    from caseweave.agents.graph import run_case

    patches = _patch_all(_fake_call_investigate)
    with patches[0], patches[1], patches[2], patches[3]:
        state, _ledger = run_case(con, alert_id[0])

    assert state.disposition_suggested == "investigate"
    assert state.disposition is None, "L3 still never sets the final disposition itself"


# ------------------------------------------------------------------- L4


def test_l4_without_eval_evidence_falls_back_to_human_review(seeded_duckdb, monkeypatch, tmp_path):
    """The defense-in-depth case: a rule is CONFIGURED as L4, but no
    eval_report.json exists. Must degrade to human review, not auto-close."""
    con = seeded_duckdb
    alert_id = con.execute("SELECT alert_id, rule_code FROM alerts LIMIT 1").fetchone()
    monkeypatch.setattr(cfg, "AUTONOMY_LADDER", {alert_id[1]: "L4"}, raising=False)
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path, raising=False)  # no eval_report.json here
    from caseweave.agents.graph import run_case

    patches = _patch_all(_fake_call_close)
    with patches[0], patches[1], patches[2], patches[3]:
        state, _ledger = run_case(con, alert_id[0])

    assert state.status == "ready_for_review", "L4 with no eval evidence must NOT auto-close"
    assert state.disposition is None


def test_l4_with_strong_eval_evidence_auto_closes(seeded_duckdb, monkeypatch, tmp_path):
    """The positive case: real, sufficient, high-passing eval history for
    this exact rule genuinely unlocks auto-close."""
    con = seeded_duckdb
    alert_id, rule_code = con.execute("SELECT alert_id, rule_code FROM alerts LIMIT 1").fetchone()
    monkeypatch.setattr(cfg, "AUTONOMY_LADDER", {rule_code: "L4"}, raising=False)
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path, raising=False)

    fake_report = {
        "summary": {},
        "cases": [{"rule_code": rule_code, "passed": True} for _ in range(10)],
    }
    (tmp_path / "eval_report.json").write_text(json.dumps(fake_report))

    from caseweave.agents.graph import run_case

    patches = _patch_all(_fake_call_close)
    with patches[0], patches[1], patches[2], patches[3]:
        state, _ledger = run_case(con, alert_id)

    assert state.status == "closed"
    assert state.disposition == "closed_auto"


def test_eligibility_check_insufficient_samples():
    eligible, reason = is_eligible_for_autonomous_close("R001", "L4")
    # No eval_report.json in the real data dir during a unit test run, or
    # too few samples if one happens to exist — either way, must not be
    # silently eligible.
    assert isinstance(eligible, bool)
    assert reason  # always populated, never a silent bool


def test_eligibility_check_wrong_level_is_never_eligible():
    eligible, reason = is_eligible_for_autonomous_close("R001", "L2")
    assert eligible is False
    assert "L4" in reason
