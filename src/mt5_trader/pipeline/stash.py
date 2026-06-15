"""The strategy stash — a holding pen for strategies that backtested BELOW PAR
but are worth iterating on. One YAML file, one entry per stashed strategy,
recording which validation gate(s) fell short, the metrics, and a concrete
improvement idea, so the candidate gets reworked and re-validated instead of
forgotten.

The stash is research state, NOT the live config: promotion to live still goes
through config/portfolio.yaml (see pipeline.promote). Lifecycle: a `validate`
FAIL routes a candidate here; once improved and re-validated to PASS it is
removed and promoted. See docs/WORKFLOW.md stage 9.
"""
from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_PATH = Path("research/stash.yaml")


def _load(path: Path | str) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def _save(data: dict, path: Path | str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=True, default_flow_style=False))


def add(name: str, *, file: str = "", params: dict | None = None,
        failed_gates: list[str] | None = None, metrics: dict | None = None,
        improve: str = "", stashed: str = "", path: Path | str = DEFAULT_PATH) -> dict:
    """Add or replace a stash entry. `stashed` is an ISO date string supplied by
    the caller (kept a parameter so the function stays deterministic/testable)."""
    data = _load(path)
    data[name] = {
        "file": file,
        "params": params or {},
        "failed_gates": failed_gates or [],
        "metrics": metrics or {},
        "improve": improve,
        "stashed": stashed,
    }
    _save(data, path)
    return data[name]


def get(name: str, path: Path | str = DEFAULT_PATH) -> dict | None:
    return _load(path).get(name)


def list_(path: Path | str = DEFAULT_PATH) -> dict:
    return _load(path)


def remove(name: str, path: Path | str = DEFAULT_PATH) -> bool:
    """Drop an entry (e.g. once it's been improved and promoted). True if it existed."""
    data = _load(path)
    existed = data.pop(name, None) is not None
    if existed:
        _save(data, path)
    return existed
