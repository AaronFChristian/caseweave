"""Deterministic rule pack.

Five detectors, each mapping to a documented BSA/FinCEN typology. These are
deliberately NOT model-driven: an alert must be explainable to an examiner in
one sentence, and a rule that fires on "3 cash deposits between $7,500 and
$10,000 within 7 days" is defensible in a way that an embedding distance is not.

The LLM's job starts after this point, not before it.
"""

from __future__ import annotations

from datetime import datetime

import duckdb
import pandas as pd

from caseweave import config as cfg
from caseweave.models import Alert, Typology

RULES = {
    "R001": "Structuring / CTR avoidance",
    "R002": "Rapid pass-through of funds",
    "R003": "Fan-in from multiple unrelated senders",
    "R004": "Dormant account reactivation",
    "R005": "High-risk jurisdiction corridor",
    "R006": "Circular fund flow (layering ring)",
}

_SQL_STRUCTURING = f"""
WITH cash AS (
    SELECT dst_account_id AS acc, ts, amount
    FROM transactions
    WHERE channel = 'cash_deposit'
      AND amount >= {cfg.RULE_STRUCTURING_FLOOR}
      AND amount < {cfg.CTR_THRESHOLD}
      AND dst_account_id IS NOT NULL
),
win AS (
    SELECT a.acc, min(b.ts) AS ws, max(b.ts) AS we,
           count(*) AS n, sum(b.amount) AS total
    FROM cash a
    JOIN cash b ON a.acc = b.acc
                AND b.ts >= a.ts
                AND b.ts < a.ts + INTERVAL {cfg.RULE_STRUCTURING_WINDOW_DAYS} DAY
    GROUP BY a.acc, a.ts
)
SELECT acc, min(ws) AS ws, max(we) AS we, max(n) AS n, max(total) AS total
FROM win WHERE n >= {cfg.RULE_STRUCTURING_MIN_COUNT}
GROUP BY acc
"""

_SQL_PASSTHROUGH = f"""
WITH inb AS (
    SELECT dst_account_id AS acc, ts, amount, tx_id
    FROM transactions WHERE dst_account_id IS NOT NULL AND NOT is_cash
),
outb AS (
    SELECT src_account_id AS acc, ts, amount
    FROM transactions WHERE src_account_id IS NOT NULL AND NOT is_cash
),
paired AS (
    SELECT i.acc, i.ts AS ws, o.ts AS we, i.amount + o.amount AS total
    FROM inb i JOIN outb o
      ON i.acc = o.acc
     AND o.ts > i.ts
     AND o.ts <= i.ts + INTERVAL {cfg.RULE_PASSTHROUGH_HOURS} HOUR
     AND o.amount >= i.amount * {cfg.RULE_PASSTHROUGH_RATIO}
     AND o.amount <= i.amount * 1.05
     AND i.amount >= 1000
)
SELECT acc, min(ws) AS ws, max(we) AS we, count(*) AS n, sum(total) AS total
FROM paired GROUP BY acc
HAVING count(*) >= {cfg.RULE_PASSTHROUGH_MIN_COUNT}
"""

_SQL_FANIN = f"""
-- Scoped to PERSONAL accounts receiving from INDIVIDUALS. A business account
-- with 30 distinct payers is a business, not a mule. Removing this scope
-- inflates the queue roughly 10x with pure noise.
WITH inb AS (
    SELECT t.dst_account_id AS acc, t.src_party_id AS sender, t.ts, t.amount
    FROM transactions t
    JOIN accounts a ON a.account_id = t.dst_account_id
    JOIN parties sp ON sp.party_id = t.src_party_id
    WHERE t.dst_account_id IS NOT NULL
      AND t.src_party_id IS NOT NULL
      AND a.account_type IN ('checking', 'savings')
      AND sp.party_type = 'individual'
),
win AS (
    SELECT a.acc, a.ts AS anchor,
           count(DISTINCT b.sender) AS senders,
           count(*) AS n, sum(b.amount) AS total,
           min(b.ts) AS ws, max(b.ts) AS we
    FROM inb a JOIN inb b
      ON a.acc = b.acc AND b.ts >= a.ts
     AND b.ts < a.ts + INTERVAL {cfg.RULE_FANIN_WINDOW_DAYS} DAY
    GROUP BY a.acc, a.ts
)
SELECT acc, min(ws) AS ws, max(we) AS we, max(n) AS n, max(total) AS total,
       max(senders) AS senders
FROM win WHERE senders >= {cfg.RULE_FANIN_MIN_SENDERS}
GROUP BY acc
"""

_SQL_DORMANT = f"""
WITH mv AS (
    SELECT acc, ts, amount FROM (
        SELECT src_account_id AS acc, ts, amount FROM transactions WHERE src_account_id IS NOT NULL
        UNION ALL
        SELECT dst_account_id AS acc, ts, amount FROM transactions WHERE dst_account_id IS NOT NULL
    )
),
lagged AS (
    SELECT acc, ts, amount,
           lag(ts) OVER (PARTITION BY acc ORDER BY ts) AS prev_ts,
           avg(amount) OVER (PARTITION BY acc ORDER BY ts
                             ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prior_avg
    FROM mv
)
SELECT acc, min(prev_ts) AS ws, max(ts) AS we, count(*) AS n, sum(amount) AS total
FROM lagged
WHERE prev_ts IS NOT NULL
  AND date_diff('day', prev_ts, ts) >= {cfg.RULE_DORMANT_QUIET_DAYS}
  AND amount >= {cfg.RULE_DORMANT_MIN_AMOUNT}
  AND prior_avg IS NOT NULL
  AND amount >= prior_avg * {cfg.RULE_DORMANT_MULTIPLE}
GROUP BY acc
"""

_SQL_CORRIDOR = f"""
SELECT src_account_id AS acc, min(ts) AS ws, max(ts) AS we,
       count(*) AS n, sum(amount) AS total
FROM transactions
WHERE is_cross_border
  AND src_account_id IS NOT NULL
  AND dst_country IN ({",".join(f"'{c}'" for c in sorted(cfg.HIGH_RISK_COUNTRIES))})
GROUP BY src_account_id
HAVING count(*) >= {cfg.RULE_CORRIDOR_MIN_COUNT}
   AND sum(amount) >= {cfg.RULE_CORRIDOR_MIN_TOTAL}
   AND date_diff('day', min(ts), max(ts)) <= {cfg.RULE_CORRIDOR_WINDOW_DAYS}
"""

_REASON = {
    "R001": "{n} cash deposits between ${floor:,.0f} and ${ctr:,.0f} totalling ${total:,.2f} within {days} days, consistent with CTR-threshold avoidance",
    "R002": "{n} inbound/outbound pairs where {pct:.0f}%+ of a credit left the account within {hrs} hours, totalling ${total:,.2f}",
    "R003": "{senders} distinct senders credited the account within {days} days, {n} transactions totalling ${total:,.2f}",
    "R004": "Account inactive for {quiet}+ days then moved ${total:,.2f} across {n} transactions at {mult:.0f}x its prior average",
    "R005": "{n} cross-border transfers to a high-risk jurisdiction totalling ${total:,.2f}",
    "R006": "Funds returned to the originating account across {hops} hops totalling ${total:,.2f}, with no economic purpose evident",
}


def _to_alerts(
    con: duckdb.DuckDBPyConnection,
    df: pd.DataFrame,
    code: str,
    reason_fn,
    scores: dict[str, float],
    start_idx: int,
) -> list[Alert]:
    if df.empty:
        return []
    acc_party = dict(con.execute("SELECT account_id, party_id FROM accounts").fetchall())
    gt = con.execute(
        """
        SELECT coalesce(src_account_id, dst_account_id) AS acc,
               gt_typology, gt_subject_party_id
        FROM transactions WHERE gt_typology <> 'none'
        """
    ).fetchdf()
    gt_by_party: dict[str, str] = dict(
        zip(gt["gt_subject_party_id"], gt["gt_typology"], strict=False)
    )

    out: list[Alert] = []
    for i, row in enumerate(df.itertuples(index=False), start=start_idx):
        acc = row.acc
        party = acc_party.get(acc)
        if party is None:
            continue
        typ = gt_by_party.get(party, Typology.NONE.value)
        out.append(
            Alert(
                alert_id=f"AL{i:05d}",
                created_at=datetime.now(),
                subject_party_id=party,
                subject_account_id=acc,
                rule_code=code,
                rule_name=RULES[code],
                trigger_reason=reason_fn(row),
                anomaly_score=round(scores.get(acc, 0.0), 4),
                window_start=pd.Timestamp(row.ws).to_pydatetime(),
                window_end=pd.Timestamp(row.we).to_pydatetime(),
                tx_count=int(row.n),
                total_amount=float(row.total),
                gt_label=typ != Typology.NONE.value,
                gt_typology=Typology(typ),
            )
        )
    return out


def run_rules(
    con: duckdb.DuckDBPyConnection, account_scores: dict[str, float], use_graph: bool = True
) -> list[Alert]:
    alerts: list[Alert] = []
    idx = 1

    specs = [
        (
            "R001",
            _SQL_STRUCTURING,
            lambda r: _REASON["R001"].format(
                n=int(r.n),
                floor=cfg.RULE_STRUCTURING_FLOOR,
                ctr=cfg.CTR_THRESHOLD,
                total=r.total,
                days=cfg.RULE_STRUCTURING_WINDOW_DAYS,
            ),
        ),
        (
            "R002",
            _SQL_PASSTHROUGH,
            lambda r: _REASON["R002"].format(
                n=int(r.n),
                pct=cfg.RULE_PASSTHROUGH_RATIO * 100,
                hrs=cfg.RULE_PASSTHROUGH_HOURS,
                total=r.total,
            ),
        ),
        (
            "R003",
            _SQL_FANIN,
            lambda r: _REASON["R003"].format(
                senders=int(r.senders), days=cfg.RULE_FANIN_WINDOW_DAYS, n=int(r.n), total=r.total
            ),
        ),
        (
            "R004",
            _SQL_DORMANT,
            lambda r: _REASON["R004"].format(
                quiet=cfg.RULE_DORMANT_QUIET_DAYS,
                total=r.total,
                n=int(r.n),
                mult=cfg.RULE_DORMANT_MULTIPLE,
            ),
        ),
        ("R005", _SQL_CORRIDOR, lambda r: _REASON["R005"].format(n=int(r.n), total=r.total)),
    ]

    for code, sql, reason_fn in specs:
        df = con.execute(sql).fetchdf()
        batch = _to_alerts(con, df, code, reason_fn, account_scores, idx)
        idx += len(batch)
        alerts.append(batch)

    # R006 runs against Neo4j, not DuckDB. Degrade loudly rather than silently:
    # if the graph is unavailable the queue is incomplete, and the operator
    # needs to know that rather than discover it during a backtest.
    if use_graph:
        try:
            from caseweave.db.graph import detect_cycles

            df = detect_cycles()
            batch = _to_alerts(
                con,
                df,
                "R006",
                lambda r: _REASON["R006"].format(hops=int(r.n), total=r.total),
                account_scores,
                idx,
            )
            alerts.append(batch)
        except Exception as exc:  # noqa: BLE001
            print(f"  WARNING  R006 skipped, graph unavailable: {exc}")
            print("           the alert queue is INCOMPLETE — layering rings will be missed")

    return [a for batch in alerts for a in batch]
