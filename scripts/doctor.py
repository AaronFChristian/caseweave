#!/usr/bin/env python3
"""Pre-flight check. Run this before starting anything, or let `make dev`
run it for you automatically.

    python3 scripts/doctor.py          # check only, exit 1 if anything's wrong
    python3 scripts/doctor.py --fix     # also fix what's safely fixable

This exists because "the DuckDB file doesn't exist" surfaced tonight as a
500 Internal Server Error three layers deep in a FastAPI stack trace,
instead of as a one-line "run this command" at the top of a fresh checkout.
Every check below fails LOUD, at the top, before you've started three
terminals and a browser tab.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ROOT = Path(__file__).resolve().parents[1]
RESULTS: list[
    tuple[str, str, str, str | None, bool]
] = []  # status, name, detail, fix_cmd, required


def check(
    name: str, ok: bool, detail: str = "", fix_cmd: str | None = None, required: bool = True
) -> None:
    RESULTS.append(("ok" if ok else "FAIL", name, detail, fix_cmd, required))


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, **kw)


def main() -> int:
    fix = "--fix" in sys.argv

    from caseweave import config as cfg

    # ---------------------------------------------------------- DuckDB data
    db_ok = cfg.DUCKDB_PATH.exists()
    if not db_ok and fix:
        print("  fixing: regenerating Day 1 data from the seed...")
        for step in ("generate", "ingest", "score"):
            r = run(
                [sys.executable, "scripts/pipeline.py", step, "--direct"]
                if step != "generate"
                else [sys.executable, "scripts/pipeline.py", step]
            )
            if r.returncode != 0:
                print(r.stdout, r.stderr)
                check("DuckDB data present", False, f"pipeline step '{step}' failed")
                break
        else:
            db_ok = cfg.DUCKDB_PATH.exists()
    check(
        "DuckDB data present",
        db_ok,
        str(cfg.DUCKDB_PATH) if db_ok else "missing",
        "uv run python scripts/pipeline.py generate && "
        "uv run python scripts/pipeline.py ingest --direct && "
        "uv run python scripts/pipeline.py score",
    )

    # -------------------------------------------------------------- golden set
    gs_ok = (cfg.DATA_DIR / "golden_set.json").exists()
    check(
        "golden_set.json present",
        gs_ok,
        "" if gs_ok else "not built yet",
        "uv run python scripts/build_golden_set.py",
        required=False,
    )

    # ---------------------------------------------------------------- .env
    env_ok = (ROOT / ".env").exists()
    if not env_ok and fix and (ROOT / ".env.example").exists():
        print("  fixing: copying .env.example -> .env ...")
        (ROOT / ".env").write_text((ROOT / ".env.example").read_text())
        env_ok = (ROOT / ".env").exists()
    check(
        ".env present",
        env_ok,
        "" if env_ok else "missing — copy .env.example",
        "cp .env.example .env",
    )

    # ------------------------------------------------------- console deps
    node_modules_ok = (ROOT / "console" / "node_modules").exists()
    if not node_modules_ok and fix:
        print("  fixing: npm install in console/...")
        r = subprocess.run(["npm", "install"], cwd=ROOT / "console", capture_output=True, text=True)
        node_modules_ok = (ROOT / "console" / "node_modules").exists()
        if r.returncode != 0:
            print(r.stdout[-2000:], r.stderr[-2000:])
    check(
        "console node_modules installed",
        node_modules_ok,
        "" if node_modules_ok else "missing",
        "cd console && npm install",
    )

    # -------------------------------------------------------------- docker
    docker_up = False
    try:
        r = run(["docker", "compose", "ps", "--status", "running", "--format", "json"])
        docker_up = r.returncode == 0 and bool(r.stdout.strip())
    except FileNotFoundError:
        pass
    if not docker_up and fix:
        try:
            print("  fixing: docker compose up -d...")
            run(["docker", "compose", "up", "-d"])
            r = run(["docker", "compose", "ps", "--status", "running", "--format", "json"])
            docker_up = r.returncode == 0 and bool(r.stdout.strip())
        except FileNotFoundError:
            pass
    check(
        "Neo4j/Postgres/Redpanda running",
        docker_up,
        "not detected — some Day 1 gate checks and R006 will skip, this is OK for API/console dev",
        "make up",
        required=False,
    )

    # ------------------------------------------------------- tracing keys
    import os

    langsmith_on = os.environ.get("LANGCHAIN_TRACING_V2", "").lower() == "true" and bool(
        os.environ.get("LANGCHAIN_API_KEY")
    )
    logfire_on = bool(os.environ.get("LOGFIRE_TOKEN"))
    check(
        "LangSmith tracing configured",
        langsmith_on,
        "off is fine for normal dev" if not langsmith_on else "",
        required=False,
    )
    check(
        "Logfire tracing configured",
        logfire_on,
        "off is fine for normal dev" if not logfire_on else "",
        required=False,
    )

    # ------------------------------------------------------------- report
    width = max(len(n) for _, n, _, _, _ in RESULTS) + 2
    print("\n  CaseWeave — pre-flight check" + (" (--fix mode)" if fix else ""))
    print("  " + "-" * (width + 50))
    hard_fail = False
    for status, name, detail, fix_cmd, required in RESULTS:
        mark = "  ok  " if status == "ok" else (" fail " if not required else " FAIL ")
        print(f"  [{mark}] {name:<{width}} {detail}")
        if status == "FAIL" and fix_cmd:
            print(f"           fix: {fix_cmd}")
            if required:
                hard_fail = True
    print("  " + "-" * (width + 50))

    if hard_fail:
        print("\n  Not ready. Run with --fix to auto-fix what's fixable, or run the")
        print("  commands above manually.\n")
        return 1
    print("\n  Ready.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
