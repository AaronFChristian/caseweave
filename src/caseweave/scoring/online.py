"""Online anomaly scoring with River.

Why online and not batch: a production transaction-monitoring system sees a
stream, not a table. HalfSpaceTrees learns incrementally, so the model that
scores today's transaction was fitted on everything before it and nothing
after it. Batch-fitting on the full period would leak the future into the
score and inflate every metric you later report.

The score is a ranking aid on the alert queue. It never fires an alert on its
own — an unexplainable alert is not an alert, it's a complaint.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from datetime import datetime

import pandas as pd
from river import anomaly, preprocessing

from caseweave import config as cfg


def _features(rec: dict, hist: deque, prev_ts: datetime | None) -> dict[str, float]:
    amounts = list(hist)
    mean = sum(amounts) / len(amounts) if amounts else rec["amount"]
    return {
        "log_amount": math.log1p(rec["amount"]),
        "hour": float(rec["ts"].hour),
        "is_cash": 1.0 if rec["is_cash"] else 0.0,
        "is_cross_border": 1.0 if rec["is_cross_border"] else 0.0,
        "velocity_24h": float(len(amounts)),
        "amount_ratio": float(rec["amount"] / mean) if mean > 0 else 1.0,
        "gap_hours": float((rec["ts"] - prev_ts).total_seconds() / 3600.0) if prev_ts else 0.0,
    }


def score_stream(tx_df: pd.DataFrame) -> tuple[dict[str, float], dict[str, float]]:
    """Score every transaction in timestamp order.

    Returns (tx_id -> score, account_id -> max score).
    """
    model = preprocessing.MinMaxScaler() | anomaly.HalfSpaceTrees(
        n_trees=cfg.HST_N_TREES,
        height=cfg.HST_HEIGHT,
        window_size=cfg.HST_WINDOW_SIZE,
        seed=cfg.SEED,
    )

    tx_df = tx_df.sort_values("ts")
    hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=40))
    last_ts: dict[str, datetime] = {}

    tx_scores: dict[str, float] = {}
    acc_scores: dict[str, float] = defaultdict(float)

    for rec in tx_df.to_dict("records"):
        acc = rec["src_account_id"] or rec["dst_account_id"]
        if acc is None:
            continue
        window = hist[acc]
        x = _features(rec, window, last_ts.get(acc))

        score = float(model.score_one(x))
        model.learn_one(x)

        tx_scores[rec["tx_id"]] = score
        if score > acc_scores[acc]:
            acc_scores[acc] = score

        window.append(rec["amount"])
        last_ts[acc] = rec["ts"]

    return tx_scores, dict(acc_scores)
