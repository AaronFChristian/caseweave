"""Central configuration for CaseWeave.

Every tunable lives here. Nothing downstream should hardcode a threshold —
if a reviewer asks "why 7500?", the answer must be one grep away.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
REGULATORY_DIR = DATA_DIR / "regulatory"
DUCKDB_PATH = DATA_DIR / "caseweave.duckdb"

for _d in (DATA_DIR, RAW_DIR, REGULATORY_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# python-dotenv was a declared dependency from Day 1 but nothing ever called
# load_dotenv() — .env sat on disk, unread, and every _env() lookup below
# silently fell through to its default. Loaded explicitly by path (not by
# relying on cwd-based auto-discovery) so this works the same whether a
# script is run from the repo root or from a subdirectory. override=False
# is load_dotenv's default: a value already exported in the real shell
# environment always wins over .env, which is the correct precedence.
load_dotenv(ROOT / ".env")


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


# --------------------------------------------------------------------------
# Infrastructure
# --------------------------------------------------------------------------
NEO4J_URI = _env("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = _env("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = _env("NEO4J_PASSWORD", "caseweave_dev")

POSTGRES_DSN = _env("POSTGRES_DSN", "postgresql://caseweave:caseweave_dev@localhost:5432/caseweave")

REDPANDA_BOOTSTRAP = _env("REDPANDA_BOOTSTRAP", "localhost:19092")
TOPIC_TRANSACTIONS = "caseweave.transactions"
TOPIC_DLQ = "caseweave.transactions.dlq"

# --------------------------------------------------------------------------
# Models. Verified against Anthropic's model-ID docs — dateless IDs are
# canonical for the 4.6 generation and later; never append a date suffix.
# Not used on Day 1; pinned here so Day 2 has a single source of truth.
# --------------------------------------------------------------------------
MODEL_TRIAGE = _env("MODEL_TRIAGE", "claude-haiku-4-5")
MODEL_EXTRACT = _env("MODEL_EXTRACT", "claude-haiku-4-5")
MODEL_NARRATIVE = _env("MODEL_NARRATIVE", "claude-sonnet-5")
MODEL_TYPOLOGY = _env("MODEL_TYPOLOGY", "claude-sonnet-5")
MODEL_JUDGE = _env("MODEL_JUDGE", "claude-sonnet-5")
EMBEDDING_MODEL = _env("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
EMBEDDING_DIM = 384

# claude-sonnet-5 rejects the `temperature` parameter outright. Pass None.
SONNET_TEMPERATURE = None

MAX_TOKENS_PER_CASE = int(_env("MAX_TOKENS_PER_CASE", "120000"))

# --------------------------------------------------------------------------
# Synthetic data generation
# --------------------------------------------------------------------------
SEED = int(_env("CASEWEAVE_SEED", "20260729"))
N_INDIVIDUALS = 170
N_BUSINESSES = 30
N_MERCHANTS = 40
N_DUPLICATE_IDENTITIES = 12  # same human, different party record -> must resolve
SIM_DAYS = 60

HIGH_RISK_COUNTRIES = {"IR", "KP", "MM", "SY", "AF", "YE"}
DOMESTIC_COUNTRY = "US"

# Adversarial memo strings. These are the prompt-injection test fixtures the
# Day 2 input guardrail must neutralise. Free-text memo fields are
# attacker-controlled in the real world; treat them as hostile input.
INJECT_ADVERSARIAL_MEMOS = True
N_ADVERSARIAL_MEMOS = 3

# --------------------------------------------------------------------------
# Detection rules. Every constant is a policy decision, not a magic number.
# --------------------------------------------------------------------------
CTR_THRESHOLD = 10_000.0  # US currency transaction report filing threshold

RULE_STRUCTURING_FLOOR = 7_500.0
RULE_STRUCTURING_MIN_COUNT = 3
RULE_STRUCTURING_WINDOW_DAYS = 7

RULE_PASSTHROUGH_RATIO = 0.70
RULE_PASSTHROUGH_HOURS = 48
RULE_PASSTHROUGH_MIN_COUNT = 3
RULE_PASSTHROUGH_WINDOW_DAYS = 14

RULE_FANIN_MIN_SENDERS = 8
RULE_FANIN_WINDOW_DAYS = 7

RULE_DORMANT_QUIET_DAYS = 30
RULE_DORMANT_MULTIPLE = 5.0
RULE_DORMANT_MIN_AMOUNT = 10_000.0

RULE_CORRIDOR_MIN_COUNT = 2
RULE_CORRIDOR_MIN_TOTAL = 25_000.0
RULE_CORRIDOR_WINDOW_DAYS = 30

# --------------------------------------------------------------------------
# Online anomaly scoring (River HalfSpaceTrees)
# --------------------------------------------------------------------------
HST_N_TREES = 25
HST_HEIGHT = 8
HST_WINDOW_SIZE = 250

# --------------------------------------------------------------------------
# Observability. LangSmith traces the LangGraph orchestration layer and the
# LLM gateway call itself; Logfire traces everything else — tool calls
# (DuckDB/Neo4j/pgvector), all three guardrails, and the FastAPI layer.
# Both are read directly from the environment by their own SDKs at the
# point of use (LANGCHAIN_API_KEY by langsmith, LOGFIRE_TOKEN by logfire);
# these are named here so config.py stays the single documented list of
# every environment variable the project reads. Absence of either is a
# supported, safe state — both SDKs no-op cleanly with no key present,
# which is what keeps the 51-test offline suite and CI green without any
# tracing account at all.
# --------------------------------------------------------------------------
LANGCHAIN_TRACING_V2 = _env("LANGCHAIN_TRACING_V2", "false")
LANGCHAIN_PROJECT = _env("LANGCHAIN_PROJECT", "caseweave")
LOGFIRE_TOKEN = os.environ.get("LOGFIRE_TOKEN")  # None, not "", when absent

# --------------------------------------------------------------------------
# Autonomy ladder. L0-L4, per the architecture's human-in-the-loop layer.
# Default is L2 for every rule — every alert investigated gets a drafted
# narrative but a human approves it, which matches today's real, honest
# posture: no rule yet has enough live eval history to justify anything
# higher. AUTONOMY_LADDER is read by graph.py once per alert, keyed by
# rule_code; an unlisted rule falls back to DEFAULT_AUTONOMY_LEVEL.
#
#   L0  agent disabled entirely — alert sits untouched for manual work
#   L1  agent assembles evidence only, never drafts a narrative
#   L2  agent drafts, human approves every case (default, today's reality)
#   L3  same as L2, plus the agent's triage rationale is surfaced as a
#       suggested disposition — human still decides
#   L4  agent may close WITHOUT a human, but ONLY if is_eligible_for_
#       autonomous_close() confirms the rule has real golden-set evidence
#       backing it (see eval/autonomy.py) — setting a rule to "L4" here is
#       necessary but never sufficient on its own.
# --------------------------------------------------------------------------
DEFAULT_AUTONOMY_LEVEL = "L2"
AUTONOMY_LADDER: dict[str, str] = {
    "R001": "L2",
    "R002": "L2",
    "R003": "L2",
    "R004": "L2",
    "R005": "L2",
    "R006": "L2",
}
AUTONOMY_L4_MIN_SAMPLES = 5
AUTONOMY_L4_MIN_PASS_RATE = 0.95

# --------------------------------------------------------------------------
# Day 1 acceptance gates. verify_day1 fails loudly if these are not met.
# --------------------------------------------------------------------------
GATE_MIN_ALERTS = 35
GATE_MAX_ALERTS = 80
GATE_MIN_TYPOLOGY_RECALL = 1.0  # all five typologies must surface
GATE_MIN_SUBJECT_RECALL = 0.85  # 1.0 requires Neo4j: the ring originator is graph-only
GATE_MIN_FP_RATE = 0.70  # realistic AML noise floor
GATE_MAX_FP_RATE = 0.96  # above this the queue is pure noise, retune
GATE_MIN_CORPUS_CHUNKS = 40
