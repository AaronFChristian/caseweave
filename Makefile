.DEFAULT_GOAL := help
SHELL := /bin/bash
PY := uv run

help: ## show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n",$$1,$$2}'

setup: ## install deps and pre-commit hooks
	uv sync --all-extras
	$(PY) pre-commit install

up: ## start Day 1 infrastructure
	docker compose up -d
	@echo "waiting for health..."
	@until [ "$$(docker compose ps --format json | grep -c '"Health":"healthy"')" -ge 3 ]; do sleep 3; printf .; done
	@echo " ready"

up-full: ## start everything including LiteLLM and Langfuse
	docker compose --profile full up -d

down: ## stop containers, keep volumes
	docker compose down

topics: ## create the transaction topic and its DLQ
	docker compose exec -T redpanda rpk topic create caseweave.transactions -p 3 -r 1 || true
	docker compose exec -T redpanda rpk topic create caseweave.transactions.dlq -p 1 -r 1 || true

generate: ## synthetic entities and transactions -> parquet
	$(PY) python scripts/pipeline.py generate

ingest: ## parquet -> Redpanda -> DuckDB + Neo4j
	$(PY) python scripts/pipeline.py ingest

ingest-direct: ## parquet -> DuckDB + Neo4j, bypassing Redpanda
	$(PY) python scripts/pipeline.py ingest --direct

score: ## River scoring + rule pack -> alert queue
	$(PY) python scripts/pipeline.py score

corpus: ## chunk and embed the regulatory corpus into pgvector
	$(PY) python -c "from caseweave.corpus.loader import ingest; print(f'{ingest()} chunks embedded')"

day1: up topics generate ingest score corpus verify-day1 ## the whole of Day 1

verify-day1: ## acceptance gate — must be green before Day 2
	$(PY) python scripts/verify_day1.py

test: ## unit tests (offline, mocked LLM calls, no API spend)
	$(PY) pytest -q

verify-day2: ## Day 2 acceptance gate — offline, no API key needed
	$(PY) python scripts/verify_day2.py

run-case: ## run one case against the REAL Anthropic API — spends money
	$(PY) python scripts/run_case.py

golden-set: ## rebuild data/golden_set.json from current DuckDB alerts
	$(PY) python scripts/build_golden_set.py

evals: ## run the golden-set backtest — mock mode, free, this is what CI runs
	$(PY) python scripts/run_evals.py

evals-live: ## run the golden-set backtest against the REAL API — spends money
	$(PY) python scripts/run_evals.py --live

day3: golden-set evals ## the offline half of Day 3
	$(PY) python scripts/run_evals.py --fail-under 0.80

doctor: ## check every prerequisite before starting anything — run this first
	$(PY) python scripts/doctor.py

dev: ## one-shot: fix any missing prerequisites, then start the API (run `make console` in a second terminal)
	$(PY) python scripts/doctor.py --fix
	@echo ""
	@echo "  Starting API on :8123 — open a SECOND terminal and run: make console"
	@echo ""
	$(PY) uvicorn caseweave.api.main:app --reload --port 8123

api: ## run the review console's FastAPI backend on :8123
	$(PY) uvicorn caseweave.api.main:app --reload --port 8123

console: ## run the React console on :5173 (needs `make api` running separately)
	cd console && npm install && npm run dev

mcp: ## run the MCP server standalone (for Claude Desktop / MCP client testing)
	$(PY) python -m caseweave.mcp_server.server

lint: ## ruff + mypy
	$(PY) ruff check src scripts tests
	$(PY) ruff format --check src scripts tests
	$(PY) mypy src

security: ## SAST and dependency audit
	$(PY) bandit -q -r src -c pyproject.toml
	$(PY) pip-audit --strict || true

clean: ## drop generated data, keep containers
	rm -rf data/raw/*.parquet data/caseweave.duckdb

reset: down ## nuke everything including volumes
	docker compose down -v
	rm -rf data/raw/*.parquet data/caseweave.duckdb

.PHONY: help setup up up-full down topics generate ingest ingest-direct score corpus day1 verify-day1 test lint security clean reset verify-day2 run-case golden-set evals evals-live day3 api console mcp doctor dev
