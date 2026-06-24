# CUSUM Regime Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a causal CUSUM change-point regime detector and test, head-to-head vs the efficiency-ratio (ER) gate, whether it gates the trend books better.

**Architecture:** `cusum_regime()` lives beside `regime_series()` in `features/regime.py`, returns the same `{+1,0,-1}` per-bar array (causal, coarsened) so books gate identically (`regime != RANGE`). A research harness compares none / ER / CUSUM on the raw book signals via the backtest engine; deploy is opt-in and conditional on the gate.

**Tech Stack:** Python 3.11, polars, numpy, pytest, `uv run`.

## Global Constraints

- TDD: failing test first. Suite: `uv run pytest`.
- `cusum_regime` is causal (online recursion; a past bar's regime is independent of future bars) and coarsened (anchored to the series START) — same discipline as `regime_series`. No lookahead.
- Output semantics identical to `regime_series`: `{TREND_UP=+1, RANGE=0, TREND_DOWN=-1}`; books gate on `regime != RANGE`.
- Degenerate/disabled params (`threshold_h <= 0`) → all-`+1` (gate is a no-op passthrough).
- Must beat ER on Sharpe AND maxDD on a plateau, not just the ungated baseline. Default gate stays ER.
- Real broker feed (`build_bars`) for backtests. Phase 1 (Task 3) only after the Task 2 gate + sign-off.

---

### Task 1: `cusum_regime` detector

**Files:**
- Modify: `src/mt5_trader/features/regime.py` (add `cusum_regime`)
- Test: `tests/test_cusum_regime.py`

**Interfaces:**
- Consumes: `TREND_UP=1, RANGE=0, TREND_DOWN=-1` (already in `regime.py`).
- Produces: `cusum_regime(bars: pl.DataFrame, drift_k=0.5, threshold_h=5.0, vol_window=60, hold=30, coarsen=15) -> np.ndarray`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cusum_regime.py
import numpy as np
import polars as pl

from mt5_trader.features.regime import cusum_regime


def _bars(closes):
    return pl.DataFrame({"close": closes})


def test_persistent_up_drift_flags_trend_up():
    # steady rise + tiny alternating noise (nonzero std) -> consistent +z -> TREND_UP
    closes = [100.0 + i + (0.1 if i % 2 else -0.1) for i in range(120)]
    r = cusum_regime(_bars(closes), drift_k=0.5, threshold_h=3.0, vol_window=5,
                     hold=30, coarsen=1)
    assert (r[20:] == 1).mean() > 0.8


def test_zero_drift_chop_flags_range():
    # +1/-1 oscillation, no net drift -> accumulators bleed -> RANGE
    closes = [100.0 + (1.0 if i % 2 else 0.0) for i in range(120)]
    r = cusum_regime(_bars(closes), drift_k=0.5, threshold_h=3.0, vol_window=5,
                     hold=30, coarsen=1)
    assert (r[20:] == 0).mean() > 0.8


def test_persistent_down_drift_flags_trend_down():
    closes = [100.0 - i - (0.1 if i % 2 else -0.1) for i in range(120)]
    r = cusum_regime(_bars(closes), drift_k=0.5, threshold_h=3.0, vol_window=5,
                     hold=30, coarsen=1)
    assert (r[20:] == -1).mean() > 0.8


def test_causal_prefix_stable():
    # regime at past bars must not change when more bars are appended
    closes = [100.0 + i + (0.1 if i % 2 else -0.1) for i in range(120)]
    full = cusum_regime(_bars(closes), 0.5, 3.0, 5, 30, 1)
    prefix = cusum_regime(_bars(closes[:80]), 0.5, 3.0, 5, 30, 1)
    assert np.array_equal(full[:80], prefix)


def test_disabled_threshold_is_passthrough():
    closes = [100.0 + (1.0 if i % 2 else 0.0) for i in range(50)]
    r = cusum_regime(_bars(closes), threshold_h=0.0)
    assert np.all(r == 1)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_cusum_regime.py -q`
Expected: FAIL — `ImportError: cannot import name 'cusum_regime'`.

- [ ] **Step 3: Implement `cusum_regime`**

Append to `src/mt5_trader/features/regime.py`:

```python
def cusum_regime(bars: pl.DataFrame, drift_k: float = 0.5, threshold_h: float = 5.0,
                 vol_window: int = 60, hold: int = 30, coarsen: int = 15) -> np.ndarray:
    """Per-bar regime {+1 trend up, 0 range, -1 trend down} from a causal two-sided
    CUSUM on volatility-standardized coarsened returns. Same shape/semantics as
    regime_series so a book can gate on `regime != RANGE` with either method.
    threshold_h <= 0 disables -> all TREND_UP (gate is a no-op)."""
    n = len(bars)
    if n == 0:
        return np.zeros(0)
    if threshold_h <= 0:
        return np.full(n, TREND_UP, dtype=float)
    close = bars["close"].to_numpy().astype(float)
    anchors = np.arange(0, n, coarsen)              # causal grid anchored to start
    c = close[anchors]
    ret = np.diff(c, prepend=c[0])                  # ret[0] = 0
    sd = pl.Series(ret).rolling_std(vol_window).to_numpy()

    states_a = np.zeros(len(c))
    state = RANGE
    gp = gn = 0.0
    since = 0
    for t in range(len(c)):
        s = sd[t]
        if s is None or s != s or s <= 0:           # undefined / zero vol -> hold state
            states_a[t] = state
            continue
        z = ret[t] / s
        gp = max(0.0, gp + z - drift_k)
        gn = max(0.0, gn - z - drift_k)
        if gp > threshold_h:
            state, gp, gn, since = TREND_UP, 0.0, 0.0, 0
        elif gn > threshold_h:
            state, gp, gn, since = TREND_DOWN, 0.0, 0.0, 0
        else:
            since += 1
            if hold > 0 and since >= hold:
                state = RANGE
        states_a[t] = state

    states = np.zeros(n)
    for i, a in enumerate(anchors):
        end = anchors[i + 1] if i + 1 < len(anchors) else n
        states[a:end] = states_a[i]
    return states
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_cusum_regime.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/mt5_trader/features/regime.py tests/test_cusum_regime.py
git commit -m "feat(regime): causal CUSUM change-point regime detector"
```

---

### Task 2: Head-to-head harness `research_cusum_regime.py` — GATE

**Files:**
- Create: `scripts/research_cusum_regime.py`

Research script; run-and-report. **STOP at the gate.**

- [ ] **Step 1: Write the harness**

```python
#!/usr/bin/env python
"""RESEARCH: does the CUSUM regime gate beat the efficiency-ratio (ER) gate on the
trend books? For each book, take the RAW (ungated) signal and compare three gates
- none / ER / CUSUM (swept) - on the real feed. Keep CUSUM only if it beats ER on
Sharpe AND maxDD on a plateau.

    uv run scripts/research_cusum_regime.py
"""
from __future__ import annotations

import numpy as np
import yaml

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.features.regime import RANGE, cusum_regime, regime_series
from mt5_trader.pipeline.data import bars_per_year, build_bars
from mt5_trader.strategies import REGISTRY

TF_EVERY = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}
SL_ATR_MULT, ATR_WINDOW, SLIPPAGE_BPS = 8.0, 60, 0.5
DRIFT_K = [0.25, 0.5, 1.0]
THRESH_H = [3.0, 5.0, 8.0]
TREND_BOOKS = {"donchian", "vwap_trend", "london_orb"}


def metrics(bars, sig, bpy):
    cfg = BTConfig(sl_atr_mult=SL_ATR_MULT, atr_window=ATR_WINDOW,
                   slippage_bps=SLIPPAGE_BPS, bars_per_year=bpy)
    m = run(bars, np.asarray(sig, dtype=float), cfg).metrics
    return m["total_return"] * 100, m["sharpe"], m["max_drawdown"] * 100


def main():
    with open("config/portfolio.yaml") as f:
        books = [b for b in yaml.safe_load(f)["books"] if b["strategy"] in TREND_BOOKS]
    for book in books:
        name = book["strategy"]
        every = TF_EVERY[book.get("timeframe", "M1")]
        bars = build_bars(book["symbol"], every).sort("ts")
        bpy = bars_per_year(every)
        raw = np.asarray(REGISTRY[name]().signal(bars), dtype=float)  # ungated
        coarsen = int((book.get("params", {}) or {}).get("regime_coarsen", 15))
        print(f"\n=== {name}@{book['symbol']} {book.get('timeframe','M1')} ===")
        r, s, dd = metrics(bars, raw, bpy)
        print(f"{'none':>20}: ret {r:>7.2f}  Sh {s:>6.2f}  maxDD {dd:>7.2f}")
        er = regime_series(bars, coarsen=coarsen)
        r, s, dd = metrics(bars, np.where(er != RANGE, raw, 0.0), bpy)
        print(f"{'ER':>20}: ret {r:>7.2f}  Sh {s:>6.2f}  maxDD {dd:>7.2f}")
        for k in DRIFT_K:
            for h in THRESH_H:
                cu = cusum_regime(bars, drift_k=k, threshold_h=h, coarsen=coarsen)
                r, s, dd = metrics(bars, np.where(cu != RANGE, raw, 0.0), bpy)
                print(f"{'CUSUM k%.2f h%.1f'%(k,h):>20}: ret {r:>7.2f}  Sh {s:>6.2f}  maxDD {dd:>7.2f}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `uv run scripts/research_cusum_regime.py`
Expected: per book, a none/ER/CUSUM(grid) comparison of ret/Sharpe/maxDD.

- [ ] **Step 3: Commit**

```bash
git add scripts/research_cusum_regime.py
git commit -m "feat(research): CUSUM-vs-ER regime gate head-to-head"
```

- [ ] **Step 4: GATE — report + wait**

Per book: does any CUSUM `(k,h)` beat ER on Sharpe AND maxDD on a plateau? Recommend the winning `(k,h)` per book, or "none — drop CUSUM, ER stays." **Wait for sign-off before Task 3.**

---

### Task 3: Deploy CUSUM as an opt-in regime method — POST-GATE

**Files:**
- Modify: `src/mt5_trader/strategies/vwap_trend.py` (add `regime_method` switch) — and any other book the gate selected
- Modify: `config/portfolio.yaml` (set `regime_method: cusum` + params on the winning book[s])
- Test: `tests/test_cusum_regime.py` (append)

**Interfaces:**
- Consumes: `cusum_regime`, `regime_series`.
- Produces: book param `regime_method: str = "er"` (`"er" | "cusum"`) + CUSUM params (`cusum_k`, `cusum_h`).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_cusum_regime.py
from mt5_trader.strategies.vwap_trend import VwapTrend


def test_vwap_regime_method_cusum_selects_cusum_gate():
    # a choppy series -> CUSUM gate flattens the position (RANGE) where ER might not;
    # assert the cusum path produces an all-or-mostly-flat book vs an ungated one.
    closes = [100.0 + (1.0 if i % 2 else 0.0) for i in range(400)]
    bars = pl.DataFrame({
        "ts": [f"2026-06-01T00:{i:02d}:00Z" for i in range(400)],
        "open": closes, "high": [c + 0.2 for c in closes],
        "low": [c - 0.2 for c in closes], "close": closes,
        "spread_mean": [0.1] * 400,
    })
    gated = VwapTrend(regime_filter=True, regime_method="cusum",
                     cusum_k=0.5, cusum_h=3.0).signal(bars)
    assert np.abs(gated).sum() == 0.0   # chop -> CUSUM RANGE -> fully gated flat
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_cusum_regime.py::test_vwap_regime_method_cusum_selects_cusum_gate -v`
Expected: FAIL — `VwapTrend` has no `regime_method` field.

- [ ] **Step 3: Add the switch to the book**

In `vwap_trend.py`, add fields to the dataclass:

```python
    regime_method: str = "er"     # "er" (efficiency ratio) | "cusum" (change-point)
    cusum_k: float = 0.5
    cusum_h: float = 5.0
```

Add the import: `from ..features.regime import RANGE, cusum_regime, regime_series`. Replace the `regime_filter` block:

```python
        if self.regime_filter:
            if self.regime_method == "cusum":
                regime = cusum_regime(bars, drift_k=self.cusum_k, threshold_h=self.cusum_h,
                                      coarsen=self.regime_coarsen)
            else:
                regime = regime_series(bars, coarsen=self.regime_coarsen)
            pos = np.where(regime != RANGE, pos, 0.0)
        return pos
```

(Apply the same switch to any other book the gate selected.)

- [ ] **Step 4: Run test + full suite**

Run: `uv run pytest tests/test_cusum_regime.py -q && uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Set the winning config**

In `config/portfolio.yaml`, on the winning book's `params`, add `regime_method: cusum`, `cusum_k: <k>`, `cusum_h: <h>` with a comment citing the head-to-head result. Leave others on ER.

- [ ] **Step 6: Commit**

```bash
git add src/mt5_trader/strategies/vwap_trend.py config/portfolio.yaml tests/test_cusum_regime.py
git commit -m "feat(strategy): opt-in CUSUM regime gate on books that beat ER"
```

Deploy (after merge): VPS `git pull` + restart `MT5Portfolio` via `Stop+Start-ScheduledTask`.

---

## Self-Review

**Spec coverage:**
- CUSUM detector, same interface as `regime_series`, causal/coarsened, disabled→passthrough → Task 1. ✓
- Head-to-head none/ER/CUSUM, gate criterion (beat ER on Sharpe AND maxDD) → Task 2. ✓
- Opt-in deploy `regime_method`, default ER → Task 3. ✓
- Prior/skepticism (may drop at gate) → Task 2 gate. ✓

**Placeholder scan:** Task 3 says "any other book the gate selected" — intentional, since which book(s) win is unknown until Task 2; the concrete edit is shown for `vwap_trend` (the book that already uses `regime_filter`), and the same pattern applies verbatim to another book if selected. No code placeholders.

**Type consistency:** `cusum_regime(bars, drift_k, threshold_h, vol_window, hold, coarsen)` identical in Task 1, Task 2 harness, and Task 3 book switch. Returns `{+1,0,-1}` like `regime_series`; gated via `regime != RANGE` everywhere. Book params `regime_method/cusum_k/cusum_h` consistent between Task 3 code and portfolio.yaml.
