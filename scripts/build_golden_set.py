#!/usr/bin/env python3
"""Build data/golden_set.json from the alerts currently in DuckDB.

uv run python scripts/build_golden_set.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from caseweave.db import duck
from caseweave.eval.golden_set import build_golden_set, save_golden_set

if __name__ == "__main__":
    con = duck.connect(read_only=True)
    cases = build_golden_set(con)
    path = save_golden_set(cases)
    tp = sum(c["gt_label"] for c in cases)
    print(f"{len(cases)} cases ({tp} true positives, {len(cases) - tp} false positives) -> {path}")
