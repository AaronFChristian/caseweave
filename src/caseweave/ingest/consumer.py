"""Redpanda consumer.

Drains the transactions topic into a dataframe. Malformed payloads go to the
DLQ rather than killing the consumer — a poison message must never stall an
alert pipeline, because a stalled pipeline is an unfiled SAR.
"""

from __future__ import annotations

import json

import pandas as pd
from confluent_kafka import Consumer, KafkaException, Producer

from caseweave import config as cfg


def drain_topic(expected: int, timeout: float = 30.0) -> pd.DataFrame:
    c = Consumer(
        {
            "bootstrap.servers": cfg.REDPANDA_BOOTSTRAP,
            "group.id": "caseweave-day1-loader",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    c.subscribe([cfg.TOPIC_TRANSACTIONS])
    dlq = Producer({"bootstrap.servers": cfg.REDPANDA_BOOTSTRAP})

    rows: list[dict] = []
    seen: set[str] = set()
    idle = 0.0
    try:
        while len(rows) < expected and idle < timeout:
            msg = c.poll(1.0)
            if msg is None:
                idle += 1.0
                continue
            if msg.error():
                raise KafkaException(msg.error())
            idle = 0.0
            try:
                raw = msg.value()
                if raw is None:
                    raise ValueError("message has a null value (e.g. a tombstone record)")
                rec = json.loads(raw)
                if rec["tx_id"] in seen:  # idempotent replay
                    continue
                seen.add(rec["tx_id"])
                rows.append(rec)
            except Exception as exc:  # noqa: BLE001
                dlq.produce(
                    cfg.TOPIC_DLQ,
                    key=msg.key(),
                    value=msg.value(),
                    headers=[("error", str(exc).encode())],
                )
        c.commit(asynchronous=False)
    finally:
        c.close()
        dlq.flush(10)

    if not rows:
        raise RuntimeError("consumed 0 messages — check Redpanda is up and the topic exists")
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"])
    return df
