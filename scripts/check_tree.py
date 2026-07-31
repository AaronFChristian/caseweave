#!/usr/bin/env python3
"""Run this FIRST after extracting or cloning the repo, before `uv sync`.

Checks that every expected file exists and that no brace-expansion or
Finder-replace debris is present. This exists because both of those have
silently deleted or half-populated this tree more than once during Day 1/2
development — cheaper to check structurally than to debug an ImportError
three layers deep.

    python3 scripts/check_tree.py
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXPECTED = [
    "src/caseweave/__init__.py",
    "src/caseweave/config.py",
    "src/caseweave/models.py",
    "src/caseweave/generator/__init__.py",
    "src/caseweave/generator/entities.py",
    "src/caseweave/generator/stream.py",
    "src/caseweave/ingest/__init__.py",
    "src/caseweave/ingest/consumer.py",
    "src/caseweave/ingest/producer.py",
    "src/caseweave/ingest/resolution.py",
    "src/caseweave/scoring/__init__.py",
    "src/caseweave/scoring/online.py",
    "src/caseweave/scoring/rules.py",
    "src/caseweave/db/__init__.py",
    "src/caseweave/db/duck.py",
    "src/caseweave/db/graph.py",
    "src/caseweave/corpus/__init__.py",
    "src/caseweave/corpus/loader.py",
    "src/caseweave/llm/__init__.py",
    "src/caseweave/llm/gateway.py",
    "src/caseweave/llm/ledger.py",
    "src/caseweave/agents/__init__.py",
    "src/caseweave/agents/state.py",
    "src/caseweave/agents/tools.py",
    "src/caseweave/agents/triage.py",
    "src/caseweave/agents/narrative.py",
    "src/caseweave/agents/graph.py",
    "src/caseweave/guardrails/__init__.py",
    "src/caseweave/guardrails/injection.py",
    "src/caseweave/guardrails/attribution.py",
    "src/caseweave/guardrails/compliance.py",
    "src/caseweave/eval/__init__.py",
    "src/caseweave/eval/golden_set.py",
    "src/caseweave/eval/metrics.py",
    "src/caseweave/eval/harness.py",
    "src/caseweave/mcp_server/__init__.py",
    "src/caseweave/mcp_server/tool_logic.py",
    "src/caseweave/mcp_server/server.py",
    "src/caseweave/api/__init__.py",
    "src/caseweave/api/main.py",
    "scripts/pipeline.py",
    "scripts/build_golden_set.py",
    "scripts/run_evals.py",
    "scripts/verify_day1.py",
    "scripts/verify_day2.py",
    "scripts/run_case.py",
    "scripts/fetch_primary_sources.py",
    "pyproject.toml",
    "Makefile",
    "docker-compose.yml",
    "README.md",
]

TEST_FILES_MIN = 12  # tests/test_*.py count, loosely — exact count drifts as we add more
REGULATORY_DOCS_MIN = 10


def main() -> int:
    missing = [f for f in EXPECTED if not (ROOT / f).is_file()]

    debris = [p for p in ROOT.rglob("*") if "{" in p.name or "}" in p.name]
    debris = [p for p in debris if ".git" not in p.parts]

    test_files = list((ROOT / "tests").glob("test_*.py"))
    reg_docs = (
        list((ROOT / "data" / "regulatory").glob("*.md"))
        if (ROOT / "data" / "regulatory").is_dir()
        else []
    )

    print(f"expected files present: {len(EXPECTED) - len(missing)}/{len(EXPECTED)}")
    print(f"test files found:       {len(test_files)} (want >= {TEST_FILES_MIN})")
    print(f"regulatory docs found:  {len(reg_docs)} (want >= {REGULATORY_DOCS_MIN})")
    print(f"brace-expansion debris: {len(debris)}")

    ok = True
    if missing:
        ok = False
        print("\nMISSING FILES:")
        for f in missing:
            print(f"  - {f}")
    if debris:
        ok = False
        print("\nDEBRIS FOUND (delete these, they are extraction artifacts):")
        for d in debris:
            print(f"  - {d.relative_to(ROOT)}")
    if len(test_files) < TEST_FILES_MIN:
        ok = False
        print(f"\nToo few test files found ({len(test_files)}) — tests/ is incomplete.")
    if len(reg_docs) < REGULATORY_DOCS_MIN:
        ok = False
        print(
            f"\nToo few regulatory docs found ({len(reg_docs)}) — data/regulatory/ is incomplete."
        )

    if not ok:
        print("\nTree is NOT complete. Fix the above before running `uv sync`.")
        return 1

    print("\nTree is complete. Safe to run `uv sync`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
