"""Evidence-gathering tools.

Every function here takes an alert/party/account id, queries a store, and
writes typed Facts into the ledger. This is the boundary where raw rows
become citable evidence. Nothing downstream of this module ever sees a raw
DuckDB row or a raw Cypher result again.

Cypher usage is template-only — see caseweave.db.graph.CYPHER_TEMPLATES. No
function here builds a Cypher string.

Every function is wrapped in a Logfire span. Spans carry only scalar,
meaningful attributes (ids, counts, query names) — never a raw connection
object or driver, which isn't meaningfully serializable and isn't useful in
a trace anyway. This is the direct answer to "what did the system actually
search and what did it get back": open the span for a case in Logfire and
see the Cypher template name, the pgvector query text, and the fact count
each call produced, rather than only the final ledger contents.
"""

from __future__ import annotations

import hashlib
import logging

import duckdb
import logfire

from caseweave.guardrails.injection import sanitize_for_prompt
from caseweave.llm.ledger import EvidenceLedger, FactKind

logger = logging.getLogger(__name__)


def _qhash(sql: str) -> str:
    return hashlib.sha256(sql.encode()).hexdigest()[:12]


def load_alert(con: duckdb.DuckDBPyConnection, ledger: EvidenceLedger, alert_id: str):
    with logfire.span("tool.load_alert", alert_id=alert_id) as span:
        sql = "SELECT * FROM alerts WHERE alert_id = ?"
        row = con.execute(sql, [alert_id]).fetchone()
        if row is None:
            span.set_attribute("found", False)
            raise ValueError(f"unknown alert_id {alert_id!r}")
        cols = [d[0] for d in con.description]
        rec = dict(zip(cols, row, strict=True))
        ledger.add(
            FactKind.ALERT,
            "duckdb.alerts",
            f"Alert {rec['alert_id']} ({rec['rule_code']}, {rec['rule_name']}): "
            f"{rec['trigger_reason']}",
            payload=rec,
            query_hash=_qhash(sql),
        )
        span.set_attribute("found", True)
        span.set_attribute("rule_code", rec["rule_code"])
        return rec


def load_subject_kyc(con: duckdb.DuckDBPyConnection, ledger: EvidenceLedger, party_id: str):
    with logfire.span("tool.load_subject_kyc", party_id=party_id) as span:
        sql = "SELECT * FROM parties WHERE party_id = ?"
        row = con.execute(sql, [party_id]).fetchone()
        span.set_attribute("found", row is not None)
        if row is None:
            return None
        cols = [d[0] for d in con.description]
        rec = dict(zip(cols, row, strict=True))
        desc = (
            f"Subject {rec['name']} ({rec['party_type']}), country {rec['country']}, "
            f"KYC risk {rec['kyc_risk']}"
            + (f", industry {rec['industry']}" if rec.get("industry") else "")
            + f", onboarded {rec['onboarded_at']}"
        )
        ledger.add(FactKind.PARTY, "duckdb.parties", desc, payload=rec, query_hash=_qhash(sql))
        span.set_attribute("kyc_risk", rec["kyc_risk"])
        return rec


def load_subject_transactions(
    con: duckdb.DuckDBPyConnection, ledger: EvidenceLedger, alert: dict, limit: int = 25
):
    with logfire.span(
        "tool.load_subject_transactions", account_id=alert["subject_account_id"], limit=limit
    ) as span:
        sql = """
            SELECT tx_id, ts, amount, channel, is_cash, is_cross_border, memo,
                   src_account_id, dst_account_id
            FROM transactions
            WHERE (src_account_id = ? OR dst_account_id = ?)
              AND ts BETWEEN ? AND ?
            ORDER BY ts LIMIT ?
        """
        args = [
            alert["subject_account_id"],
            alert["subject_account_id"],
            alert["window_start"],
            alert["window_end"],
            limit,
        ]
        rows = con.execute(sql, args).fetchall()
        cols = [d[0] for d in con.execute(sql, args).description]
        out = []
        n_flagged = 0
        for r in rows:
            rec = dict(zip(cols, r, strict=True))
            direction = (
                "credit" if rec["dst_account_id"] == alert["subject_account_id"] else "debit"
            )
            # memo is attacker-controlled free text. It goes through the input
            # guardrail and is embedded as a quoted literal, never as anything
            # resembling an instruction context.
            memo, flagged = sanitize_for_prompt(rec["memo"])
            if flagged:
                rec = {**rec, "_memo_flagged": True}
                n_flagged += 1
            desc = (
                f"{direction.capitalize()} of ${rec['amount']:,.2f} via {rec['channel']} "
                f"on {rec['ts']}" + (f', memo: "{memo}"' if memo else "")
            )
            f = ledger.add(
                FactKind.TRANSACTION,
                "duckdb.transactions",
                desc,
                payload=rec,
                query_hash=_qhash(sql),
            )
            out.append(f)
        span.set_attribute("n_transactions", len(out))
        if n_flagged:
            # Surfaced at the tool layer, not just the guardrail layer — a
            # case reviewer scanning tool spans should see this without
            # having to separately open the injection guardrail's own logs.
            span.set_attribute("n_memos_flagged", n_flagged)
            logfire.warn(
                "adversarial memo pattern detected in transaction history",
                account_id=alert["subject_account_id"],
                n_flagged=n_flagged,
            )
        return out


def load_graph_pattern(
    ledger: EvidenceLedger, pattern_name: str, description: str, payload: dict, query_hash: str
):
    """Register a fact already computed by the rule pack (e.g. the R006 cycle
    that produced this alert). Kept separate from live Cypher calls below so
    a fact that justified the ALERT itself is captured even if the live graph
    query later returns something slightly different (e.g. new transactions
    since the alert fired)."""
    with logfire.span("tool.load_graph_pattern", pattern_name=pattern_name):
        return ledger.add(
            FactKind.GRAPH_PATTERN,
            "neo4j.rule_pack",
            description,
            payload=payload,
            query_hash=query_hash,
        )


def load_network_context(ledger: EvidenceLedger, account_id: str, limit: int = 8):
    """Live k-hop counterparty and fan-in queries via the template registry.
    Degrades gracefully — a missing graph means less evidence, not a crash;
    the coverage gate downstream will catch a case that ends up under-evidenced."""
    with logfire.span(
        "tool.load_network_context", account_id=account_id, cypher_template="khop_counterparties"
    ) as span:
        try:
            from caseweave.db.graph import run_template
        except Exception:  # noqa: BLE001 - degrade gracefully, any store outage means less evidence not a crash
            logger.warning("neo4j driver unavailable, skipping network context for %s", account_id)
            span.set_attribute("graph_available", False)
            return []

        out = []
        try:
            for row in run_template("khop_counterparties", account_id=account_id, limit=limit):
                out.append(
                    ledger.add(
                        FactKind.GRAPH_PATTERN,
                        "neo4j.khop_counterparties",
                        f"Account {account_id} is {row['hops']} hop(s) from counterparty "
                        f"{row['counterparty']}",
                        payload=row,
                        query_hash="khop_counterparties",
                    )
                )
            span.set_attribute("graph_available", True)
            span.set_attribute("n_counterparties", len(out))
        except Exception:
            logger.warning(
                "graph query failed for account %s, evidence is incomplete",
                account_id,
                exc_info=True,
            )
            span.set_attribute("graph_available", True)
            span.set_attribute("query_error", True)
        return out


def load_prior_dispositions(
    con: duckdb.DuckDBPyConnection, ledger: EvidenceLedger, party_id: str, exclude_alert_id: str
):
    """Long-term memory. If this subject has been reviewed before — on a
    DIFFERENT alert — surface that history as evidence. A repeat filing on
    the same subject should reference the prior disposition, and an
    investigator should know if this exact entity was already cleared or
    already flagged, before drafting a fresh narrative as if from nothing.

    This is the feature Layer 4 of the architecture promised and Day 2/3
    never actually built — `FactKind.PRIOR_DISPOSITION` existed as an enum
    value with nothing behind it. This closes that gap.
    """
    with logfire.span(
        "tool.load_prior_dispositions", party_id=party_id, exclude_alert_id=exclude_alert_id
    ) as span:
        try:
            rows = con.execute(
                """SELECT alert_id, decision, reason_code, decided_at
                   FROM dispositions
                   WHERE subject_party_id = ? AND alert_id != ?
                   ORDER BY decided_at DESC LIMIT 5""",
                [party_id, exclude_alert_id],
            ).fetchall()
        except Exception:
            logger.warning(
                "dispositions table unavailable, no prior-disposition memory", exc_info=True
            )
            span.set_attribute("available", False)
            return []

        out = []
        for alert_id, decision, reason_code, decided_at in rows:
            desc = (
                f"Prior review of this subject: alert {alert_id} was {decision} "
                f"(reason: {reason_code}) on {decided_at}"
            )
            out.append(
                ledger.add(
                    FactKind.PRIOR_DISPOSITION,
                    "duckdb.dispositions",
                    desc,
                    payload={
                        "alert_id": alert_id,
                        "decision": decision,
                        "reason_code": reason_code,
                    },
                    query_hash="load_prior_dispositions",
                )
            )
        span.set_attribute("available", True)
        span.set_attribute("n_prior_dispositions", len(out))
        return out


def load_typology_matches(ledger: EvidenceLedger, query: str, k: int = 3):
    """Hybrid-retrieval placeholder for Day 2: vector search over the
    regulatory corpus. Day 3 adds BM25 + rerank on top of this."""
    with logfire.span("tool.load_typology_matches", query=query, k=k) as span:
        try:
            from caseweave.corpus.loader import search
        except Exception:  # noqa: BLE001 - degrade gracefully, any store outage means less evidence not a crash
            logger.warning("pgvector unavailable, skipping typology retrieval for query %r", query)
            span.set_attribute("corpus_available", False)
            return []

        out = []
        try:
            results = search(query, k=k)
            # This is the direct answer to "what was actually searched and
            # retrieved" — each hit logged with its chunk id, source
            # document, and similarity score, not just folded silently into
            # a Fact summary.
            for r in results:
                logfire.info(
                    "corpus retrieval hit",
                    query=query,
                    chunk_id=r["chunk_id"],
                    doc_title=r["doc_title"],
                    section=r["section"],
                    score=r["score"],
                )
                out.append(
                    ledger.add(
                        FactKind.REGULATORY_CITATION,
                        "pgvector.reg_chunks",
                        f"{r['doc_title']} — {r['section']}: "
                        + r["content"][:220].replace("\n", " ")
                        + ("..." if len(r["content"]) > 220 else ""),
                        payload={"chunk_id": r["chunk_id"], "score": r["score"]},
                        query_hash=r["chunk_id"],
                    )
                )
            span.set_attribute("corpus_available", True)
            span.set_attribute("n_chunks_retrieved", len(out))
        except Exception:  # noqa: BLE001 - degrade gracefully, any store outage means less evidence not a crash
            logger.warning("corpus search failed for query %r, evidence is incomplete", query)
            span.set_attribute("corpus_available", True)
            span.set_attribute("query_error", True)
        return out
