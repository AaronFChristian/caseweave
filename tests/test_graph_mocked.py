"""Full LangGraph wiring, exercised with a mocked gateway so this runs in CI
and on every dev machine without an ANTHROPIC_API_KEY or a token spend.
Live behaviour is checked separately in scripts/smoke_live.py, which a
person runs by hand against the real API."""

import random
from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

from caseweave import config as cfg
from caseweave.db import duck
from caseweave.generator.entities import build_population
from caseweave.generator.stream import generate
from caseweave.ingest.resolution import resolve
from caseweave.scoring.online import score_stream
from caseweave.scoring.rules import run_rules

TODAY = date(2026, 7, 29)


def _fake_call(task, system, messages, *, max_tokens=2048, ledger=None, use_cache=True):
    from caseweave.llm.gateway import CallResult

    user_text = messages[-1]["content"]

    if task == "triage":
        text = (
            '{"risk_score": 0.8, "typology_hypothesis": "structuring", '
            '"recommended_route": "investigate", "rationale": "test rationale"}'
        )
    elif task == "narrative":
        # extract every fact id actually present in the evidence block and
        # cite the first few, so the mock produces a genuinely well-attributed
        # narrative rather than a hand-typed one
        import re

        ids = re.findall(r"\[F-\d{3}\]", user_text)
        cite = " ".join(sorted(set(ids))[:3]) or "[F-001]"
        text = f"The subject conducted activity consistent with structuring {cite}."
    elif task == "judge":
        text = "SUPPORTED"
    else:
        text = "ok"

    return CallResult(text=text, task=task, model="mock", input_tokens=10, output_tokens=10)


@pytest.fixture()
def seeded_duckdb(tmp_path):
    rng = random.Random(cfg.SEED)
    pop = build_population(rng, TODAY)
    txs = generate(pop, rng, TODAY)
    parties, links = resolve(pop.parties)

    con = duck.connect(tmp_path / "graph_test.duckdb")
    duck.replace_table(con, "addresses", pd.DataFrame([a.model_dump() for a in pop.addresses]))
    duck.replace_table(con, "parties", pd.DataFrame([p.model_dump() for p in parties]))
    duck.replace_table(con, "accounts", pd.DataFrame([a.model_dump() for a in pop.accounts]))
    duck.replace_table(con, "transactions", pd.DataFrame([t.model_dump() for t in txs]))
    duck.replace_table(
        con, "entity_links", pd.DataFrame([entity_link_dump(link) for link in links])
    )

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


def entity_link_dump(link):
    return link.model_dump()


@patch("caseweave.llm.gateway.call", side_effect=_fake_call)
@patch("caseweave.agents.triage.call", side_effect=_fake_call)
@patch("caseweave.agents.narrative.call", side_effect=_fake_call)
@patch("caseweave.guardrails.attribution.call", side_effect=_fake_call)
def test_full_case_runs_end_to_end(mock_a, mock_n, mock_t, mock_g, seeded_duckdb):
    from caseweave.agents.graph import run_case

    con = seeded_duckdb
    alert_id = con.execute(
        "SELECT alert_id FROM alerts WHERE gt_label ORDER BY alert_id LIMIT 1"
    ).fetchone()[0]

    result, ledger = run_case(con, alert_id)

    assert result.status in ("ready_for_review", "refused", "closed")
    assert ledger is not None
    assert len(ledger.facts) > 0


@patch("caseweave.llm.gateway.call", side_effect=_fake_call)
@patch("caseweave.agents.triage.call", side_effect=_fake_call)
@patch("caseweave.agents.narrative.call", side_effect=_fake_call)
@patch("caseweave.guardrails.attribution.call", side_effect=_fake_call)
def test_narrative_only_cites_facts_that_predate_drafting(
    mock_a, mock_n, mock_t, mock_g, seeded_duckdb
):
    """The core invariant: every cited fact_id existed in the ledger BEFORE
    the narrative was generated, because the ledger was frozen first."""
    from caseweave.agents.graph import run_case

    con = seeded_duckdb
    alert_id = con.execute(
        "SELECT alert_id FROM alerts WHERE gt_label ORDER BY alert_id LIMIT 1"
    ).fetchone()[0]

    result, ledger = run_case(con, alert_id)
    if result.narrative_cited_ids:
        for fid in result.narrative_cited_ids:
            assert fid in ledger, f"{fid} cited but not in ledger"


@patch("caseweave.llm.gateway.call", side_effect=_fake_call)
@patch("caseweave.agents.triage.call", side_effect=_fake_call)
@patch("caseweave.agents.narrative.call", side_effect=_fake_call)
@patch("caseweave.guardrails.attribution.call", side_effect=_fake_call)
def test_adversarial_memo_never_reaches_ledger_as_instruction(
    mock_a, mock_n, mock_t, mock_g, seeded_duckdb
):
    """One of the planted adversarial memos must land in the ledger as an
    inert quoted fact, and the ledger must record that it was flagged."""
    con = seeded_duckdb
    hostile = con.execute(
        "SELECT tx_id, src_account_id, dst_account_id FROM transactions "
        "WHERE lower(memo) LIKE '%ignore%instruction%' LIMIT 1"
    ).fetchone()
    assert hostile is not None, "test fixture assumption broken: no adversarial memo found"

    from caseweave.llm.ledger import EvidenceLedger

    ledger = EvidenceLedger("C-TEST")
    alert = {
        "subject_account_id": hostile[1] or hostile[2],
        "window_start": "2000-01-01",
        "window_end": "2030-01-01",
    }
    from caseweave.agents import tools

    facts = tools.load_subject_transactions(con, ledger, alert, limit=200)
    flagged_facts = [f for f in facts if f.payload.get("_memo_flagged")]
    assert flagged_facts, "the adversarial memo transaction should have been flagged"
    for f in flagged_facts:
        assert "ignore" in f.summary.lower()  # present as quoted text
        assert not f.summary.strip().lower().startswith("system:")  # not leading as an instruction
