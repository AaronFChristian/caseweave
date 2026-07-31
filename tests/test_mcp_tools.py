"""Tests for the MCP tool logic — no MCP SDK dependency, pure DuckDB tests."""

import random
from datetime import date

import pandas as pd
import pytest

from caseweave import config as cfg
from caseweave.db import duck
from caseweave.generator.entities import build_population
from caseweave.generator.stream import generate
from caseweave.ingest.resolution import resolve
from caseweave.mcp_server import tool_logic as tl
from caseweave.scoring.online import score_stream
from caseweave.scoring.rules import run_rules

TODAY = date(2026, 7, 29)


@pytest.fixture()
def seeded_duckdb(tmp_path):
    rng = random.Random(cfg.SEED)
    pop = build_population(rng, TODAY)
    txs = generate(pop, rng, TODAY)
    parties, links = resolve(pop.parties)
    con = duck.connect(tmp_path / "mcp_test.duckdb")
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


def test_list_alerts_returns_data(seeded_duckdb):
    alerts = tl.list_alerts(seeded_duckdb, limit=5)
    assert len(alerts) == 5
    assert all("alert_id" in a for a in alerts)


def test_list_alerts_respects_limit(seeded_duckdb):
    assert len(tl.list_alerts(seeded_duckdb, limit=2)) == 2


def test_get_alert_returns_full_record(seeded_duckdb):
    first = tl.list_alerts(seeded_duckdb, limit=1)[0]
    detail = tl.get_alert(seeded_duckdb, first["alert_id"])
    assert detail["alert_id"] == first["alert_id"]
    assert "trigger_reason" in detail


def test_get_alert_unknown_id_returns_none(seeded_duckdb):
    assert tl.get_alert(seeded_duckdb, "AL99999") is None


def test_get_case_evidence_does_not_call_narrative_model(seeded_duckdb):
    """The critical MCP-safety property: this must never trigger a Sonnet
    call. Verified by patching the narrative gateway call to raise if it's
    ever invoked."""
    from unittest.mock import patch

    first = tl.list_alerts(seeded_duckdb, limit=1)[0]
    with patch("caseweave.llm.gateway.call", side_effect=AssertionError("must not be called")):
        result = tl.get_case_evidence(seeded_duckdb, first["alert_id"])
    assert result["fact_count"] > 0


def test_get_case_evidence_unknown_alert(seeded_duckdb):
    result = tl.get_case_evidence(seeded_duckdb, "AL99999")
    assert "error" in result


def test_get_queue_summary_totals_match(seeded_duckdb):
    summary = tl.get_queue_summary(seeded_duckdb)
    assert summary["total_alerts"] == len(tl.list_alerts(seeded_duckdb, limit=1000))
