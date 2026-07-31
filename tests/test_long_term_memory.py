"""Tests for Layer 4's actual implementation: prior dispositions on the
same subject are persisted durably and surfaced as evidence on a later
case — closing the gap where PRIOR_DISPOSITION existed only as an unused
enum value."""

from datetime import UTC, datetime

from caseweave.agents.tools import load_prior_dispositions
from caseweave.db import duck
from caseweave.llm.ledger import EvidenceLedger, FactKind


def test_insert_and_retrieve_prior_disposition(tmp_path):
    con = duck.connect(tmp_path / "ltm.duckdb")
    duck.insert_disposition(
        con, "D001", "AL00001", "P0042", "reject", "false_positive", "aaron", datetime.now(UTC)
    )

    ledger = EvidenceLedger("C-NEW")
    facts = load_prior_dispositions(con, ledger, "P0042", exclude_alert_id="AL00099")

    assert len(facts) == 1
    assert facts[0].kind == FactKind.PRIOR_DISPOSITION
    assert "AL00001" in facts[0].summary
    assert "reject" in facts[0].summary
    con.close()


def test_excludes_the_current_alert_itself(tmp_path):
    """A case must never cite its own not-yet-final disposition as 'prior'
    history — only genuinely earlier, different alerts count."""
    con = duck.connect(tmp_path / "ltm.duckdb")
    duck.insert_disposition(
        con, "D001", "AL00001", "P0042", "approve", "fully_supported", "aaron", datetime.now(UTC)
    )

    ledger = EvidenceLedger("C-SAME")
    facts = load_prior_dispositions(con, ledger, "P0042", exclude_alert_id="AL00001")

    assert facts == []
    con.close()


def test_no_prior_history_returns_no_facts(tmp_path):
    con = duck.connect(tmp_path / "ltm.duckdb")
    ledger = EvidenceLedger("C-FRESH")
    facts = load_prior_dispositions(con, ledger, "P9999", exclude_alert_id="AL00001")
    assert facts == []
    con.close()


def test_multiple_priors_ordered_most_recent_first(tmp_path):
    con = duck.connect(tmp_path / "ltm.duckdb")
    duck.insert_disposition(
        con,
        "D001",
        "AL00001",
        "P0042",
        "reject",
        "false_positive",
        "aaron",
        datetime(2026, 1, 1, tzinfo=UTC),
    )
    duck.insert_disposition(
        con,
        "D002",
        "AL00002",
        "P0042",
        "approve",
        "fully_supported",
        "aaron",
        datetime(2026, 6, 1, tzinfo=UTC),
    )

    ledger = EvidenceLedger("C-NEW2")
    facts = load_prior_dispositions(con, ledger, "P0042", exclude_alert_id="AL00099")

    assert len(facts) == 2
    assert "AL00002" in facts[0].summary, "most recent disposition should come first"
    con.close()


def test_insert_disposition_is_append_only_not_replace(tmp_path):
    """Confirms insert_disposition accumulates history rather than wiping
    it — the whole point of a long-term memory table."""
    con = duck.connect(tmp_path / "ltm.duckdb")
    duck.insert_disposition(con, "D001", "AL1", "P1", "approve", "x", "r", datetime.now(UTC))
    duck.insert_disposition(con, "D002", "AL2", "P1", "reject", "y", "r", datetime.now(UTC))
    assert duck.counts(con)["dispositions"] == 2
    con.close()
