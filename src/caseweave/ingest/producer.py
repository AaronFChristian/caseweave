"""Redpanda producer.

Replays the generated parquet into the transactions topic. Idempotency key is
the tx_id, set as the message key so a re-run partitions identically and a
compacted topic converges rather than duplicating.
"""

from __future__ import annotations

import json

import pandas as pd
from confluent_kafka import Producer

from caseweave import config as cfg


def _producer() -> Producer:
    return Producer(
        {
            "bootstrap.servers": cfg.REDPANDA_BOOTSTRAP,
            "enable.idempotence": True,
            "acks": "all",
            "linger.ms": 20,
            "compression.type": "snappy",
        }
    )


def replay_to_topic(df: pd.DataFrame, topic: str | None = None) -> int:
    if df.empty:
        raise ValueError("refusing to produce an empty batch")
    topic = topic or cfg.TOPIC_TRANSACTIONS
    p = _producer()
    failures: list[str] = []

    def _cb(err, msg):
        if err is not None:
            failures.append(f"{msg.key()}: {err}")

    for rec in df.to_dict("records"):
        rec["ts"] = str(rec["ts"])
        p.produce(
            topic,
            key=rec["tx_id"].encode(),
            value=json.dumps(rec, default=str).encode(),
            on_delivery=_cb,
        )
        p.poll(0)
    p.flush(30)

    if failures:
        raise RuntimeError(f"{len(failures)} deliveries failed, first: {failures[0]}")
    return len(df)
