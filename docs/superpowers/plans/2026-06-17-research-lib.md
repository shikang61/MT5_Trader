# Research Primitives Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate the duplicated/scattered event-study helpers into one tested module `src/mt5_trader/research.py`, then refactor `research_hf/sweep/volume.py` to import it (deleting their copies).

**Architecture:** A single module of pure functions (plus one thin IO wrapper `load_1s`). Behavior is preserved byte-for-byte from the current script copies so the refactor is safe; unit tests pin the primitives and each refactored script is smoke-run.

**Tech Stack:** Python 3.11, numpy, polars; pytest. Reads `data/processed` (Dukascopy) for script smoke-runs.

---

## Task 1: Create `research.py` + unit tests

**Files:**
- Create: `src/mt5_trader/research.py`
- Test: `tests/test_research.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_research.py
import numpy as np
import polars as pl
import pytest

from mt5_trader.research import (
    SLIP_BP, aligned_fwd, cost_bp, dedupe, fwd_returns, report, sweep_events,
    verdict, _grid_1s,
)


def test_cost_bp_bar_columns():
    df = pl.DataFrame({"spread_mean": [0.2, 0.4], "close": [100.0, 100.0]})
    # mean(spread/mid)=0.003 -> 30 bp + 0.5 slip
    assert abs(cost_bp(df) - (30.0 + SLIP_BP)) < 1e-9


def test_cost_bp_grid_columns():
    g = pl.DataFrame({"spread": [0.2, 0.4], "mid": [100.0, 100.0]})
    assert abs(cost_bp(g, spread_col="spread", mid_col="mid") - (30.0 + SLIP_BP)) < 1e-9


def test_fwd_returns_value_and_nan_tail():
    close = np.array([100.0, 110.0, 121.0, 133.1])
    fwd = fwd_returns(close, horizon=1)
    # entry at next bar (index 1), exit one bar later (index 2): log(121/110)
    assert abs(fwd[0] - np.log(121.0 / 110.0)) < 1e-9
    assert np.isnan(fwd[-1]) and np.isnan(fwd[-2])


def test_dedupe_min_gap():
    out = dedupe(np.array([0, 2, 3, 10, 11, 20]), min_gap=5)
    assert out.tolist() == [0, 10, 20]


def test_aligned_fwd_mean_hit_n():
    sig = np.array([1.0, -1.0, 1.0])
    fwd = np.array([0.0010, -0.0020, np.nan])   # bp: +10, +20 (short*neg), nan
    mean_bp, hit, n = aligned_fwd(sig, fwd, np.array([0, 1, 2]))
    assert n == 2
    assert abs(mean_bp - 15.0) < 1e-9            # (+10 + +20)/2
    assert hit == 1.0


def test_aligned_fwd_no_valid_events():
    assert aligned_fwd(np.array([1.0]), np.array([np.nan]), np.array([0])) == (0.0, 0.0, 0)


def test_sweep_events_high_reclaim():
    # level=100 on every bar; quiet small; cross up at bar 3, reclaim 1 bar later
    bars = pl.DataFrame({
        "high":  [99.0, 99.0, 99.0, 101.0, 100.5],
        "low":   [98.0, 98.0, 98.0, 99.0, 99.0],
        "close": [98.5, 98.5, 98.5, 100.6, 99.5],
    })
    levels = np.full(5, 100.0)
    idx, reclaim = sweep_events(bars, levels, side=1, quiet=2, confirm=1)
    assert idx.tolist() == [3]
    assert reclaim.tolist() == [True]            # close[4]=99.5 < 100 -> reclaimed


def test_grid_1s_ffill_shape():
    from datetime import datetime, timezone
    t0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    ticks = pl.DataFrame({
        "ts": [t0.replace(second=0), t0.replace(second=0, microsecond=500000),
               t0.replace(second=2)],          # gap at second 1
        "bid": [100.0, 100.2, 101.0],
        "ask": [100.2, 100.4, 101.2],
    })
    g = _grid_1s(ticks)
    assert g["ts"].dt.second().to_list() == [0, 1, 2]   # second 1 filled
    assert abs(g["mid"][1] - g["mid"][0]) < 1e-9        # ffill held second-0 mid


def test_report_pass_fail(capsys):
    report("x", np.array([5.0, 7.0]), hurdle=1.0)
    assert "PASS" in capsys.readouterr().out
    report("y", np.array([-5.0]), hurdle=1.0)
    assert "FAIL" in capsys.readouterr().out


def test_verdict_pass_fail(capsys):
    verdict("a", 5.0, 1.0, 10, 0.6)
    assert "PASS" in capsys.readouterr().out
    verdict("b", 0.1, 1.0, 10, 0.4)
    assert "FAIL" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_research.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mt5_trader.research'`

- [ ] **Step 3: Write the module**

```python
# src/mt5_trader/research.py
"""Reusable research / event-study primitives.

Consolidates helpers that were duplicated across scripts/research_*.py so
notebooks and scripts share one tested implementation. All pure functions
except load_1s (IO via load_ticks).
"""
from __future__ import annotations

import numpy as np
import polars as pl

from .data.ingest import load_ticks

SLIP_BP = 0.5


def cost_bp(df: pl.DataFrame, spread_col: str = "spread_mean",
            mid_col: str = "close", slip_bp: float = SLIP_BP) -> float:
    """Round-trip cost in bp: mean(spread/mid)*1e4 + slip. Bars use the defaults
    (spread_mean/close); 1s grids pass spread_col='spread', mid_col='mid'."""
    return float((df[spread_col] / df[mid_col]).mean() * 1e4 + slip_bp)


def fwd_returns(close: np.ndarray, horizon: int) -> np.ndarray:
    """Forward log return over `horizon` bars, entry at the next bar (no
    lookahead). NaN in the final horizon+1 slots."""
    fwd = np.full(len(close), np.nan)
    fwd[: len(close) - horizon - 1] = (
        np.log(close[horizon + 1:]) - np.log(close[1: len(close) - horizon]))
    return fwd


def dedupe(idx: np.ndarray, min_gap: int) -> np.ndarray:
    """Greedily keep indices at least `min_gap` apart."""
    keep, last = [], -10**12
    for i in idx:
        if i - last >= min_gap:
            keep.append(i)
            last = i
    return np.array(keep, dtype=int)


def aligned_fwd(sig: np.ndarray, fwd: np.ndarray,
                events: np.ndarray) -> tuple[float, float, int]:
    """(mean signed forward bp, hit rate, n) over event indices. Returns
    (0.0, 0.0, 0) if no event has a finite forward return."""
    s = np.sign(sig[events])
    f = fwd[events]
    ok = ~np.isnan(f)
    if ok.sum() == 0:
        return 0.0, 0.0, 0
    pnl = s[ok] * f[ok] * 1e4
    return float(pnl.mean()), float((pnl > 0).mean()), int(ok.sum())


def sweep_events(bars: pl.DataFrame, levels: np.ndarray, side: int,
                 quiet: int = 60, confirm: int = 5) -> tuple[np.ndarray, np.ndarray]:
    """First bar where price crosses per-bar `levels` after sitting on one side
    for `quiet` bars. side=+1 up through a high-side level (buy stops above),
    side=-1 down through a low-side level. Returns (event bar indices, reclaim
    flags at `confirm` bars later)."""
    high, low = bars["high"].to_numpy(), bars["low"].to_numpy()
    close = bars["close"].to_numpy()
    beyond = high > levels if side > 0 else low < levels
    idx, reclaim = [], []
    last = -10**9
    for t in np.flatnonzero(beyond):
        if np.isnan(levels[t]) or t - last < quiet or t + confirm >= len(close):
            continue
        s = max(0, t - quiet)
        prior = beyond[s:t] & (levels[s:t] == levels[t])
        if prior.any():
            continue
        idx.append(t)
        c = close[t + confirm]
        reclaim.append(c < levels[t] if side > 0 else c > levels[t])
        last = t
    return np.array(idx, dtype=int), np.array(reclaim, dtype=bool)


def _grid_1s(ticks: pl.DataFrame) -> pl.DataFrame:
    """1s grid of last mid + mean spread, forward-filled inside gaps."""
    return (
        ticks.with_columns(mid=(pl.col("bid") + pl.col("ask")) / 2,
                           spread=pl.col("ask") - pl.col("bid"))
        .group_by_dynamic("ts", every="1s")
        .agg(mid=pl.col("mid").last(), spread=pl.col("spread").mean())
        .upsample(time_column="ts", every="1s")
        .with_columns(pl.col("mid", "spread").forward_fill())
        .drop_nulls()
    )


def load_1s(symbol: str, sources=("data/processed",)) -> pl.DataFrame:
    """1s mid/spread grid for `symbol` from one or more tick source dirs."""
    return _grid_1s(load_ticks(list(sources), symbol))


def report(label: str, signed_fwd_bp: np.ndarray, hurdle: float) -> None:
    """Print a PASS/FAIL event-study line (research_sweep format)."""
    ok = ~np.isnan(signed_fwd_bp)
    if ok.sum() == 0:
        print(f"  {label}: no events")
        return
    e = signed_fwd_bp[ok]
    status = "PASS" if e.mean() > hurdle else "FAIL"
    print(f"  {label}: {status} edge {e.mean():+.2f} bp (cost {hurdle:.2f}), "
          f"n={ok.sum()}, hit {np.mean(e > 0):.1%}")


def verdict(name: str, edge_bp: float, hurdle_bp: float, n: int, hit: float) -> None:
    """Print a PASS/FAIL verdict line (research_hf format)."""
    status = "PASS" if edge_bp > hurdle_bp else "FAIL"
    print(f"  -> {status}: edge {edge_bp:+.2f} bp/trade vs cost {hurdle_bp:.2f} bp "
          f"({n} events, hit {hit:.1%})\n")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_research.py -q`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add src/mt5_trader/research.py tests/test_research.py
git commit -m "feat(research): tested library of event-study primitives"
```

---

## Task 2: Refactor `research_hf.py` to use the library

**Files:**
- Modify: `scripts/research_hf.py`

Uses from the library: `load_1s`, `cost_bp` (1s-grid columns), `dedupe`, `aligned_fwd`, `verdict`.

- [ ] **Step 1: Replace imports + delete the private copies**

In `scripts/research_hf.py`, change the import block. Replace:

```python
from pathlib import Path

import numpy as np
import polars as pl

from mt5_trader.data.ingest import load_ticks

DATA = Path("data/processed")
SLIP_BP = 0.5
```

with:

```python
import numpy as np
import polars as pl

from mt5_trader.research import aligned_fwd, cost_bp, dedupe, load_1s, verdict
```

Then DELETE the now-duplicated function definitions in the file: `load_1s`,
`cost_bp`, `_aligned_fwd`, `_dedupe`, `_verdict` (keep `study_leadlag`,
`study_momentum`, `study_spike`, `study_triangle`, `main`).

- [ ] **Step 2: Fix the call sites**

In the study functions, rename calls:
- `_dedupe(` → `dedupe(`
- `_aligned_fwd(` → `aligned_fwd(`
- `_verdict(` → `verdict(`

And the two grid `cost_bp` calls take grid columns. Replace `cost_bp(b)` with
`cost_bp(b, spread_col="spread", mid_col="mid")` and `cost_bp(df)` with
`cost_bp(df, spread_col="spread", mid_col="mid")`. `load_1s(...)` calls are
unchanged (default `sources=("data/processed",)` matches the old `DATA`).

- [ ] **Step 3: Smoke-run two studies**

Run: `uv run scripts/research_hf.py momentum`
Expected: prints the autocorr table + a `-> PASS/FAIL: edge ...` line, no traceback.

Run: `uv run scripts/research_hf.py leadlag`
Expected: prints the corr table + a verdict line, no traceback.

- [ ] **Step 4: Commit**

```bash
git add scripts/research_hf.py
git commit -m "refactor(research_hf): use mt5_trader.research primitives"
```

---

## Task 3: Refactor `research_sweep.py` to use the library

**Files:**
- Modify: `scripts/research_sweep.py`

Uses from the library: `cost_bp` (bar defaults), `fwd_returns`, `sweep_events`, `report`.
Keeps its own `build`, `_study`, `study_*`, and constants `QUIET_BARS`, `CONFIRM`,
`ROUND_GRID`, `HORIZONS`.

- [ ] **Step 1: Replace imports + delete the private copies**

Replace:

```python
from mt5_trader.data.bars import time_bars
from mt5_trader.data.ingest import load_ticks

DATA = Path("data/processed")
SLIP_BP = 0.5
```

with:

```python
from mt5_trader.data.bars import time_bars
from mt5_trader.data.ingest import load_ticks
from mt5_trader.research import cost_bp, fwd_returns, report, sweep_events

DATA = Path("data/processed")
```

(`Path` is still used by `DATA`; keep `from pathlib import Path`.) Then DELETE the
file's own `cost_bp`, `fwd_returns`, `sweep_events`, `_report` definitions and the
`SLIP_BP = 0.5` line. Keep `QUIET_BARS`, `CONFIRM`, `HORIZONS`, `ROUND_GRID`.

- [ ] **Step 2: Fix the call sites**

Rename `_report(` → `report(`. `cost_bp(bars)` calls are unchanged (bar columns are
the defaults). `sweep_events(...)` calls are unchanged — the library defaults
(`quiet=60, confirm=5`) equal the old `QUIET_BARS`/`CONFIRM` used by the deleted
signature, and explicit `sweep_events(bars, levels, side, quiet, confirm)` calls
still pass through.

- [ ] **Step 3: Smoke-run**

Run: `uv run scripts/research_sweep.py pdhl --symbol XAUUSD`
Expected: prints the PDH/PDL study with `PASS/FAIL ... n=...` lines, no traceback.

Run: `uv run scripts/research_sweep.py wiggle --symbol XAUUSD`
Expected: prints the wiggle grid, no traceback.

- [ ] **Step 4: Commit**

```bash
git add scripts/research_sweep.py
git commit -m "refactor(research_sweep): use mt5_trader.research primitives"
```

---

## Task 4: Refactor `research_volume.py` + full suite

**Files:**
- Modify: `scripts/research_volume.py`

Uses from the library: `cost_bp` (bar defaults), `fwd_returns`, `report`. Keeps
its own `build`, `study_*`.

- [ ] **Step 1: Replace imports + delete the private copies**

Replace:

```python
from mt5_trader.data.bars import time_bars
from mt5_trader.data.ingest import load_ticks
from mt5_trader.features.microstructure import with_micro_features

DATA = Path("data/processed")
SLIP_BP = 0.5
```

with:

```python
from mt5_trader.data.bars import time_bars
from mt5_trader.data.ingest import load_ticks
from mt5_trader.features.microstructure import with_micro_features
from mt5_trader.research import cost_bp, fwd_returns, report

DATA = Path("data/processed")
```

Then DELETE the file's own `cost_bp`, `fwd_returns`, `_report` defs and `SLIP_BP`.

- [ ] **Step 2: Fix the call sites**

Rename `_report(` → `report(`. `cost_bp(bars)` calls unchanged (bar defaults).

- [ ] **Step 3: Smoke-run**

Run: `uv run scripts/research_volume.py vwap --symbol XAUUSD`
Expected: prints the VWAP-reversion study with `PASS/FAIL` lines, no traceback.

- [ ] **Step 4: Full test suite (nothing else broke)**

Run: `uv run pytest -q`
Expected: PASS (all green; +10 from test_research.py).

- [ ] **Step 5: Commit**

```bash
git add scripts/research_volume.py
git commit -m "refactor(research_volume): use mt5_trader.research primitives"
```

---

## Self-review notes

- **Spec coverage:** module + all 8 primitives + `_grid_1s` (Task 1); tests per
  primitive (Task 1); 3-script refactor + smoke-run (Tasks 2–4); notebooks just
  import the module (no code task — documented in spec). All covered.
- **Behavior preservation:** `cost_bp` defaults to bar columns; research_hf's grid
  calls pass `spread`/`mid` (the one non-mechanical edit — do not miss it).
  `sweep_events` library defaults (60/5) equal research_sweep's `QUIET_BARS`/`CONFIRM`.
  Print formats copied verbatim (`report`/`verdict`).
- **Orphan check:** removing `load_1s` from research_hf orphans its `load_ticks` and
  `Path` imports → both removed in Task 2. research_sweep/volume keep `load_ticks`
  (their `build` uses it) and `Path` (their `DATA`).
- **Name shadow:** `report`'s internal PASS/FAIL var is `status` (not `verdict`) so
  it doesn't shadow the module's `verdict` function.
