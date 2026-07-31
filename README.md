# CaseWeave

An agentic AML investigation copilot. It triages transaction-monitoring alerts,
assembles an evidence packet from a transaction knowledge graph, and drafts a
suspicious activity report narrative in which every assertion traces back to a
specific transaction, account record, or regulatory clause — with a configurable
human-in-the-loop autonomy ladder.

> **All data in this repository is synthetic.** No real customer transactions,
> no real borrower financials, no real filings. The generator produces the
> entire population from a fixed seed. This is a portfolio system for
> demonstrating architecture and evaluation design. It is **not** validated
> compliance software and must not be used to prepare an actual regulatory
> filing.

---

## The design thesis

A SAR narrative is a legal document submitted to a federal regulator. A
hallucinated sentence is not a bad user experience — it is a false statement to
FinCEN. So the architecture inverts the usual pattern:

> **Evidence is deterministic. Only the prose is probabilistic.**

The language model never touches a database. It receives a frozen
`EvidenceLedger` in which every fact carries an ID, and it may only write
sentences that cite those IDs. A validator checks each sentence for a valid
citation and runs an entailment check that the cited fact actually supports the
claim. Sentences that fail are suppressed and surfaced to the reviewer, never
silently passed through. Below a coverage threshold the system refuses to draft
at all and returns an evidence-gap report instead.

---

## Status

| Day | Scope | State |
|---|---|---|
| 1 | Data plane: synthetic stream, knowledge graph, entity resolution, rule pack, regulatory corpus | ✅ complete |
| 2 | Agent graph, evidence ledger, attributed narrative, guardrail pipeline | ✅ complete |
| 3 | Golden-set evals, CI gate, MCP server, review console, deploy config | ✅ complete |

---

## Quickstart

Requires Docker, and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
make setup          # deps + pre-commit hooks
make day1           # infra up, generate, ingest, score, corpus, verify
```

`make day1` runs the whole Day 1 pipeline and finishes on the acceptance gate.
Individual stages:

```bash
make up             # postgres+pgvector, neo4j, redpanda
make topics         # create the transaction topic and its DLQ
make generate       # synthetic entities + transactions -> parquet
make ingest         # parquet -> Redpanda -> DuckDB + Neo4j
make score          # River anomaly scoring + rule pack -> alert queue
make corpus         # chunk + embed the regulatory corpus into pgvector
make verify-day1    # acceptance gate
```

Iterating on the rules? Skip the slow parts:

```bash
make ingest-direct  # bypass Redpanda
make score
```

---

## Day 1 output

From seed `20260729`:

```
252 parties  ·  325 accounts  ·  8,645 transactions over 60 days
12/12 planted duplicate identities resolved
42 alerts  ·  11 true positives  ·  73.8% false-positive rate
5/5 typologies surfaced  ·  planted-subject recall 10/11 (11/11 with Neo4j)
43 regulatory chunks embedded
```

The false-positive rate is deliberate. Published analysis puts real
transaction-monitoring false positives in the ninety to ninety-five percent
range, so the generator plants benign near-misses — a restaurant banking real
cash, a landlord passing rent through to a mortgage, a rotating savings pool —
alongside the true typologies. A clean alert queue would make every downstream
precision number meaningless.

### Detection rules

| Code | Typology | Store |
|---|---|---|
| R001 | Structuring / CTR avoidance | DuckDB |
| R002 | Rapid pass-through of funds | DuckDB |
| R003 | Fan-in from unrelated senders | DuckDB |
| R004 | Dormant account reactivation | DuckDB |
| R005 | High-risk jurisdiction corridor | DuckDB |
| R006 | Circular fund flow (layering ring) | **Neo4j** |

R006 is the interesting one. The originator of a laundering ring sends on the
first hop and receives on the last, so it never presents the
inbound-then-outbound signature the pass-through rule looks for. No amount of
SQL tuning finds it — it is only visible as a closed path in the graph. That
asymmetry is the concrete justification for the graph layer, and
`planted-subject recall` drops from 11/11 to 10/11 when Neo4j is unavailable.

---

## Day 2

The agent graph: alert -> triage -> evidence gathering -> attribution-conditioned
narrative -> guardrail gate. Six modules, wired with LangGraph:

```
llm/gateway.py       single chokepoint for every model call — in-process
                      router (see "gateway architecture" below), task-based
                      routing, on-disk response cache, cost ledger
llm/ledger.py         the EvidenceLedger: frozen, ID-bearing fact store
agents/tools.py       the only code that writes Facts — DuckDB, Neo4j, pgvector
agents/triage.py      Haiku classification, structured verdict
agents/narrative.py   Sonnet drafting, ledger-only prompt, citation extraction
agents/graph.py       the LangGraph supervisor and all six nodes
guardrails/injection.py    pattern-based memo scanner, defense-in-depth wrapper
guardrails/attribution.py  sentence-level citation + LLM-judge entailment check
guardrails/compliance.py   legal-conclusion language filter
```

**The core invariant**, checked by `tests/test_graph_mocked.py`: every fact ID
cited in a narrative existed in the ledger *before* generation began, because
the ledger is frozen (`ledger.freeze()`) prior to the narrative call. A
narrative under `GATE_MIN_ATTRIBUTION_COVERAGE` (90%) is refused, not
softened — see `guardrails/attribution.build_refusal`.

**Gateway architecture.** `llm/gateway.py` is an in-process Python router,
not a standalone LiteLLM proxy container. Same governance story — one
chokepoint, task-based routing, caching, cost tracking, no direct SDK calls
elsewhere in the codebase — without an extra container on a laptop. The
equivalent standalone routing policy is documented in `config/litellm.yaml`
for reference; switching to it later is a `base_url` change in
`llm/gateway.py`, not an architecture change.

**Testing without spending API budget.** `tests/test_graph_mocked.py` patches
the gateway with canned responses and runs the full LangGraph wiring — this
is what CI runs, and what `make verify-day2` checks. `scripts/run_case.py`
is separate and deliberate: it requires `ANTHROPIC_API_KEY` explicitly and
is never invoked by CI or the gate. Run it by hand when you want to confirm
live model behaviour matches what the mocks assert.

```bash
make verify-day2                          # offline, free
export ANTHROPIC_API_KEY=sk-ant-...
uv run python scripts/run_case.py         # live, ~$0.02-0.05 per case
```

## Day 3

Golden-set evals, a CI gate that can actually fail, an MCP server, a review
console, and deploy configuration.

### Golden set and eval harness

`data/golden_set.json` is derived from the SAME ground-truth typology labels
the Day 1 generator plants — not hand-written reference narratives, which
for a portfolio project would take longer to write than everything else in
the repo combined. What's hand-designed is the **eval contract**: five
metrics per case (`src/caseweave/eval/metrics.py`), including one with zero
tolerance — `no_false_clearance` — which fails if a true-positive alert is
silently closed with no narrative and no evidence-gap report. A missed
filing is the one failure mode worse than an over-cautious refusal.

```bash
make golden-set     # rebuild data/golden_set.json from current DuckDB alerts
make evals           # backtest in mock mode — free, this is what CI runs
make evals-live       # backtest against the real API — spends money
```

**Honest caveat, printed by the tool itself every run**: mock-mode evals
prove the graph/ledger/guardrail *wiring* is correct — the mock model always
cooperates, so a 100% mock pass rate is a plumbing signal, not a quality
signal. `tests/test_eval_harness.py` proves each metric can genuinely fail
(deliberately feeding it bad state), so the gate has real teeth even though
tonight's full-corpus run happened to be clean:

```
30 cases, 30 passed (100.0%)
mean attribution coverage: 1.0
mean latency: 28.6 ms (mock)
```

### CI eval gate

`.github/workflows/ci.yml` runs `build_golden_set.py` + `run_evals.py
--fail-under 0.80` on every push/PR, in mock mode — a PR that breaks the
graph wiring or a guardrail can't merge. A commented-out `live-evals` job
sketches the honest path to a real quality gate: manually-triggered or
nightly, using a repo secret, separate from the PR-blocking mock gate. Live
model quality on every commit isn't something to spend money on
unconditionally.

### MCP server

```bash
make mcp
```

Five read-only tools (`list_alerts`, `get_alert`, `get_case_evidence`,
`search_typology`, `get_queue_summary`) exposed via `FastMCP`, addable to
Claude Desktop's config. Deliberately read-only — `get_case_evidence`
assembles the evidence ledger without ever calling the narrative model,
proven by `tests/test_mcp_tools.py::test_get_case_evidence_does_not_call_narrative_model`,
which patches the gateway to raise if it's ever invoked. Any write
(disposition, approval) requires a session-scoped console request, never an
MCP call — see `src/caseweave/api/main.py`'s module docstring for the
boundary.

Business logic (`mcp_server/tool_logic.py`) is dependency-free from the MCP
SDK and tested with plain pytest; the server file (`mcp_server/server.py`)
is a thin decorator wrapper only responsible for protocol wiring.

### Review console

```bash
make api        # FastAPI backend, :8123
make console     # React console, :5173 (separate terminal)
```

Queue pane, case detail, and a narrative view where citation markers
(`[F-001]`) are hoverable spans that highlight the corresponding evidence
fact — the attribution architecture made visible. "Run full case" is a
separate, explicit button, never triggered by page load or alert selection,
so browsing the queue never spends tokens. Approve/Edit/Reject are gated on
a reason code from a controlled vocabulary (`GET /reason-codes`) — no
free-text reasons.

Every API endpoint was tested live against real DuckDB data while building
this (health check, list/detail/evidence, valid and invalid disposition
submission, 404 on unknown alert); the frontend build was verified with a
real `vite build` (zero errors) and served with `vite preview`. Full
browser-rendered click-through has not been done by an automated agent —
worth doing yourself before a live demo.

### Deploy

`Dockerfile` (multi-stage, non-root, pinned deps) and `fly.toml` document
the deploy target. **Not deployed** — that requires a Fly.io account and
`fly auth login`, a manual one-time step. Once authenticated:

```bash
fly launch --no-deploy
fly secrets set ANTHROPIC_API_KEY=sk-ant-...
fly deploy
```

Point `NEO4J_URI` at Neo4j Aura's free tier and `POSTGRES_DSN` at Fly
Postgres or Supabase rather than running either in the demo container.

## Architecture

```
Users & channels    React console · FastAPI · FastMCP · webhook · Slack
Orchestration       LangGraph supervisor + 6 sub-agents, Postgres checkpointer
Tools               parameterised Cypher · DuckDB analytics · hybrid retrieval
Context & memory    Neo4j graph · pgvector corpus · prior dispositions
Guardrails          injection · PII · attribution validator · compliance filter
LLM gateway         LiteLLM proxy — routing, caching, quotas, versioning
Models              Haiku 4.5 (triage/extract) · Sonnet 5 (narrative/judge)
Observability       Langfuse · Prometheus · DeepEval + RAGAS golden set
Security            OIDC · RBAC/ABAC · per-agent credentials · hash-chained audit
Infrastructure      Docker Compose local · Fly.io demo · Terraform AWS target
Delivery            ruff · mypy · pytest · bandit · trivy · eval gate
Human in the loop   L0–L4 autonomy ladder, LangGraph interrupts, reason codes
```

Two design rules that everything else follows from:

**The model never authors a query.** Every Cypher statement lives in
`CYPHER_TEMPLATES` as a fixed parameterised string. The agent selects a template
by name and supplies parameters. That closes the injection path and makes every
query a reviewable artifact rather than a model output.

**No module imports the Anthropic SDK.** All model traffic goes through the
LiteLLM proxy, so cost control, version pinning, caching and the kill switch
live in exactly one place.

---

## Repository layout

```
src/caseweave/
  config.py            every tunable, every threshold, every model string
  models.py            Pydantic domain contracts
  generator/           synthetic population and transaction stream
  ingest/              Redpanda producer/consumer, entity resolution
  scoring/             River online anomaly scoring, deterministic rule pack
  db/                  DuckDB analytics store, Neo4j graph + Cypher templates
  corpus/              heading-aware chunker, pgvector loader
  llm/                 gateway (in-process router), EvidenceLedger
  agents/              triage, evidence tools, narrative, LangGraph supervisor
  guardrails/          injection scanner, attribution validator, compliance filter
  eval/                golden-set builder, metrics, backtest harness
  mcp_server/          FastMCP server + dependency-free tool logic
  api/                 FastAPI backend for the review console
scripts/
  pipeline.py          generate | ingest | score | all
  verify_day1.py       Day 1 acceptance gate
  verify_day2.py       Day 2 acceptance gate (offline)
  build_golden_set.py  derive data/golden_set.json from DuckDB
  run_evals.py         golden-set backtest, mock or --live
  run_case.py          run one case against the real API
  check_tree.py        structural integrity check — run after any extraction
data/regulatory/        typology and narrative-guidance corpus
console/                 React + Vite review console
config/litellm.yaml     reference standalone gateway routing policy
Dockerfile, fly.toml     deploy target (documented, not deployed)
```

---

## Known limitations

Stated here rather than discovered by a reviewer:

- **DuckDB takes a global write lock.** Single-writer by design. Correct for
  batch scoring and a demo, not a pattern to carry into a concurrent service.
- **The shipped corpus is original prose**, not primary source text, so the
  repository is self-contained and reproducible offline. Run
  `scripts/fetch_primary_sources.py` for the real advisories before any demo
  where citation fidelity matters.
- **Entity resolution is deterministic blocking**, not probabilistic matching.
  Will miss transliteration variants; the tradeoff is that "same DOB, same
  surname, same registered address" is a better answer to an examiner than
  a similarity score.
- **The attribution judge is Haiku, not a downloaded NLI model.** A
  deliberate resource tradeoff — no ~500MB download, a different task class
  from the generator so a systematic bias doesn't pass its own check.
  Swapping in a local cross-encoder is a drop-in replacement for
  `guardrails/attribution._entails()`.
- **Mock-mode evals test wiring, not model quality.** See the Day 3 section
  above — a 100% mock pass rate is a plumbing signal. Live evals are a
  separate, manually-triggered, money-spending step by design.
- **No authentication anywhere.** Neither the API nor the console has auth.
  Do not expose either outside localhost.
- **Dispositions are in-memory**, not persisted — the API's disposition
  store resets on restart. A real deployment writes to the hash-chained
  audit log the architecture describes, not a Python dict.
- **The console's full browser click-through hasn't been visually verified**
  by an automated agent — the API was tested live end-to-end and the
  frontend build is clean, but do a manual pass before a live demo.
- **Deploy is documented, not executed.** `fly deploy` requires your own
  Fly.io account.
- **Ground-truth labels (`gt_*`) exist only because the data is synthetic.**
  They are stripped before anything reaches a model and exist solely to
  build the golden set.

---

## Licence

MIT. Synthetic data only.
