from datetime import datetime, timedelta

import pandas as pd

from caseweave import config as cfg
from caseweave.db import duck
from caseweave.scoring.rules import run_rules

BASE = datetime(2026, 6, 1, 10, 0)


def _seed(con, txs):
    duck.replace_table(
        con,
        "parties",
        pd.DataFrame(
            [
                {
                    "party_id": "P0001",
                    "canonical_id": "P0001",
                    "name": "Test Subject",
                    "first_name": "Test",
                    "last_name": "Subject",
                    "dob": None,
                    "party_type": "individual",
                    "country": "US",
                    "address_id": "AD0001",
                    "device_id": None,
                    "email": None,
                    "phone": None,
                    "kyc_risk": "low",
                    "industry": None,
                    "onboarded_at": None,
                }
            ]
        ),
    )
    duck.replace_table(
        con,
        "accounts",
        pd.DataFrame(
            [
                {
                    "account_id": "A00001",
                    "party_id": "P0001",
                    "account_type": "checking",
                    "currency": "USD",
                    "opened_at": None,
                    "status": "active",
                }
            ]
        ),
    )
    duck.replace_table(con, "transactions", pd.DataFrame(txs))


def _cash(i, amount, day):
    return {
        "tx_id": f"T{i:07d}",
        "ts": BASE + timedelta(days=day),
        "src_account_id": None,
        "dst_account_id": "A00001",
        "src_party_id": None,
        "dst_party_id": "P0001",
        "amount": amount,
        "currency": "USD",
        "channel": "cash_deposit",
        "src_country": "US",
        "dst_country": "US",
        "memo": "",
        "is_cash": True,
        "is_cross_border": False,
        "gt_typology": "none",
        "gt_subject_party_id": None,
    }


def test_structuring_fires_above_threshold(tmp_path):
    con = duck.connect(tmp_path / "r.duckdb")
    _seed(con, [_cash(i, 9_200.0, i) for i in range(1, 5)])
    alerts = run_rules(con, {}, use_graph=False)
    assert any(a.rule_code == "R001" for a in alerts)
    con.close()


def test_structuring_silent_below_min_count(tmp_path):
    con = duck.connect(tmp_path / "r.duckdb")
    _seed(con, [_cash(i, 9_200.0, i * 10) for i in range(1, 4)])  # spread past the window
    alerts = run_rules(con, {}, use_graph=False)
    assert not any(a.rule_code == "R001" for a in alerts)
    con.close()


def test_structuring_ignores_deposits_over_the_ctr_threshold(tmp_path):
    """Deposits above the reporting threshold are reported, not structured.
    Flagging them as structuring is a false accusation."""
    con = duck.connect(tmp_path / "r.duckdb")
    _seed(con, [_cash(i, cfg.CTR_THRESHOLD + 500, i) for i in range(1, 6)])
    alerts = run_rules(con, {}, use_graph=False)
    assert not any(a.rule_code == "R001" for a in alerts)
    con.close()


def test_every_alert_has_a_human_readable_reason(tmp_path):
    con = duck.connect(tmp_path / "r.duckdb")
    _seed(con, [_cash(i, 9_200.0, i) for i in range(1, 5)])
    for a in run_rules(con, {}, use_graph=False):
        assert len(a.trigger_reason) > 30
        assert "{" not in a.trigger_reason, "unformatted template leaked into the reason"
    con.close()
