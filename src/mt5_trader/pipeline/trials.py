"""Append-only trial log for multiple-testing accounting (WORKFLOW stage 1).

Every diagnose/validate run appends one line. The count feeds the
deflated-Sharpe haircut in validate.py: the more strategies / parameter families
you try, the higher the bar a "winner" must clear before you believe it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path("research/trials.jsonl")


def log_trial(idea: str, params: dict | None = None, verdict: str | None = None,
              path: Path | str = DEFAULT_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "idea": idea,
           "params": params or {}, "verdict": verdict}
    with open(path, "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def count(path: Path | str = DEFAULT_PATH) -> int:
    path = Path(path)
    if not path.exists():
        return 0
    return sum(1 for _ in open(path))
