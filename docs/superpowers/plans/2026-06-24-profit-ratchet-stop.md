# Profit-Ratchet Stop + Tick-Volume Reversal Gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **OUTCOME (2026-06-24): DROPPED at the gates.** Phase 0 ran (Task 1): giveback
> material, but tick volume does not separate reversal from resumption → Phase 2
> (volume gate) cancelled. Phase 1 built (Tasks 2–4) then the sweep showed no
> (T,R) cell beats baseline on any trend book → ratchet reverted (stops.py +
> engine wiring + tests removed); Tasks 6–7 cancelled. Research scripts kept. See
> the spec OUTCOME note for numbers.

**Goal:** Protect open profit on deep trend winners via a late-arming second stop (optionally tightened by a tick-volume reversal spike), without touching the entry-side disaster stop.

**Architecture:** A pure `ratchet_mult()` selects the chandelier multiplier — `sl_mult` until a position is `T`×ATR in profit, then a tighter `R` (or `R_tight` on a volume spike). The backtest engine (`_simulate_stops`) and the live runner (`chandelier_sl`) both call it, so research == live. Research-gated in phases; default-off everywhere.

**Tech Stack:** Python 3.11, polars, numpy, pytest, `uv run`. Spot gold has only tick-volume (`n_ticks` from `build_bars`).

## Global Constraints

- TDD: failing test first. Suite: `uv run pytest`.
- The ratchet **never tightens the entry-side stop** — pre-arm behavior is byte-identical to today (`ratchet_r <= 0` or `ratchet_trigger <= 0` ⇒ disabled). Default off.
- One shared `ratchet_mult()` in `src/mt5_trader/stops.py`; engine + runner both call it (parity). No circular imports (`stops.py` imports nothing internal).
- `armed` is derivable from `|extreme - entry|` (monotonic) — no extra trail state.
- Each phase ends at a **gate**: report numbers, get user sign-off before the next phase. Phase 2 only proceeds if Phase 0 shows `n_ticks` separates reversal from resumption.
- Real broker feed (`build_bars` → `data/real`) for all backtests.

---

### Task 1 (Phase 0): Giveback + volume diagnostic — GATE

**Files:**
- Create: `scripts/research_giveback.py`

**Interfaces:**
- Consumes: `build_bars`, `bars_per_year`, `REGISTRY`, `spread_bars`, `atr` (`features.technicals`).

Exploratory research script (run-and-report, no unit test — matches existing `scripts/research_*.py`). **STOP after running; report to the user. Do not start Task 2 until sign-off.**

- [ ] **Step 1: Write the diagnostic script**

```python
#!/usr/bin/env python
"""RESEARCH (not a live component): how much open profit do the trend books give
back on pullbacks, and does tick-volume (n_ticks) separate a reversal from a
resumption at favorable extremes? Decides whether a profit-ratchet (and a volume
gate) is worth building.

    uv run scripts/research_giveback.py
"""
from __future__ import annotations

import numpy as np
import polars as pl
import yaml

from mt5_trader.features.technicals import atr
from mt5_trader.pipeline.data import build_bars
from mt5_trader.strategies import REGISTRY
from mt5_trader.strategies.meanrev_pairs import spread_bars

TF_EVERY = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}
ATR_WINDOW = 60
FWD = 20          # bars ahead to classify reversal vs resumption after an extreme
VOL_WIN = 60      # lookback for the n_ticks z-score
TREND_BOOKS = {"donchian", "vwap_trend", "london_orb"}


def book_bars(book: dict):
    every = TF_EVERY[book.get("timeframe", "M1")]
    bars = build_bars(book["symbol"], every).sort("ts")
    if book.get("symbol2"):
        bars = spread_bars(bars, build_bars(book["symbol2"], every).sort("ts"),
                           float(book.get("beta", 1.0)))
    return bars


def main():
    with open("config/portfolio.yaml") as f:
        books = [b for b in yaml.safe_load(f)["books"] if b["strategy"] in TREND_BOOKS]
    for book in books:
        name = book["strategy"]
        bars = book_bars(book)
        sig = np.asarray(REGISTRY[name](**(book.get("params", {}) or {})).signal(bars), dtype=float)
        close = bars["close"].to_numpy().astype(float)
        atr_arr = bars.select(atr(ATR_WINDOW)).to_series().to_numpy().astype(float)
        nt = bars["n_ticks"].to_numpy().astype(float)
        # rolling z-score of n_ticks
        s = pl.Series(nt)
        ntz = ((s - s.rolling_mean(VOL_WIN)) / s.rolling_std(VOL_WIN)).to_numpy()

        # walk positions (one-bar-delayed signal); track favorable extreme + open profit
        givebacks, rev_z, res_z = [], [], []
        side = 0.0; entry = ext = ae = 0.0; ext_i = 0
        for t in range(1, len(close) - FWD):
            s_t = sig[t - 1]
            if s_t == 0 or (side != 0 and (s_t > 0) != (side > 0)):
                side = 0.0
            if s_t != 0 and side == 0:           # fresh entry
                side, entry, ext, ae, ext_i = s_t, close[t], close[t], atr_arr[t], t
            if side == 0 or not (ae > 0):
                continue
            d = 1.0 if side > 0 else -1.0
            new_ext = max(ext, close[t]) if side > 0 else min(ext, close[t])
            if new_ext != ext:                    # a new favorable extreme printed
                ext, ext_i = new_ext, t
                fav_atr = abs(ext - entry) / ae
                fwd = (close[t + FWD] - ext) * d   # >0 resumes (further favorable), <0 reverses
                z = ntz[t]
                if z == z:                        # not NaN
                    (res_z if fwd > 0 else rev_z).append(z)
                if fwd < 0 and fav_atr > 0:
                    givebacks.append(fav_atr)      # gave back from a profitable extreme
        gb = np.array(givebacks); rz = np.array(rev_z); sz = np.array(res_z)
        print(f"\n=== {name}@{book['symbol']} {book.get('timeframe','M1')} ===")
        if len(gb):
            print(f"giveback-from-profitable-extreme (ATRs): n={len(gb)} "
                  f"median={np.median(gb):.2f} p75={np.percentile(gb,75):.2f} p90={np.percentile(gb,90):.2f}")
        print(f"n_ticks z at REVERSAL extremes:   n={len(rz)} mean={rz.mean() if len(rz) else float('nan'):.2f}")
        print(f"n_ticks z at RESUMPTION extremes: n={len(sz)} mean={sz.mean() if len(sz) else float('nan'):.2f}")
        if len(rz) and len(sz):
            print(f"  separation (rev mean - res mean) = {rz.mean() - sz.mean():+.2f}  "
                  f"(>0 supports the volume idea)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `uv run scripts/research_giveback.py`
Expected: per trend-book — a giveback distribution and the mean `n_ticks` z-score at reversal vs resumption extremes, with the separation.

- [ ] **Step 3: Commit**

```bash
git add scripts/research_giveback.py
git commit -m "feat(research): giveback + tick-volume reversal diagnostic"
```

- [ ] **Step 4: GATE — report + wait**

Report: (a) is giveback material (median/p75 in ATRs)? (b) does `n_ticks` z separate reversal from resumption (separation > 0 with meaningful n)? Recommend: proceed to Phase 1 (price ratchet) regardless if giveback is material; include the volume gate (Phase 2) only if separation is positive and stable. **Wait for sign-off.**

---

### Task 2 (Phase 1): `ratchet_mult` pure helper

**Files:**
- Create: `src/mt5_trader/stops.py`
- Test: `tests/test_ratchet.py`

**Interfaces:**
- Produces: `ratchet_mult(open_profit_atr: float, sl_mult: float, trigger_T: float, ratchet_R: float, ratchet_R_tight: float = 0.0, volume_spike: bool = False) -> float`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ratchet.py
from mt5_trader.stops import ratchet_mult


def test_below_trigger_uses_disaster_mult():
    assert ratchet_mult(1.0, 8.0, trigger_T=2.0, ratchet_R=3.0) == 8.0


def test_at_or_above_trigger_uses_ratchet():
    assert ratchet_mult(2.0, 8.0, trigger_T=2.0, ratchet_R=3.0) == 3.0
    assert ratchet_mult(5.0, 8.0, trigger_T=2.0, ratchet_R=3.0) == 3.0


def test_disabled_when_ratchet_nonpositive():
    assert ratchet_mult(5.0, 8.0, trigger_T=2.0, ratchet_R=0.0) == 8.0


def test_disabled_when_trigger_nonpositive():
    assert ratchet_mult(5.0, 8.0, trigger_T=0.0, ratchet_R=3.0) == 8.0


def test_volume_spike_tightens_when_configured():
    assert ratchet_mult(5.0, 8.0, 2.0, 3.0, ratchet_R_tight=1.5, volume_spike=True) == 1.5


def test_volume_spike_ignored_when_tight_unset():
    assert ratchet_mult(5.0, 8.0, 2.0, 3.0, ratchet_R_tight=0.0, volume_spike=True) == 3.0


def test_armed_but_no_spike_uses_loose_ratchet():
    assert ratchet_mult(5.0, 8.0, 2.0, 3.0, ratchet_R_tight=1.5, volume_spike=False) == 3.0
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_ratchet.py -q`
Expected: FAIL — `ModuleNotFoundError: mt5_trader.stops`.

- [ ] **Step 3: Implement**

```python
# src/mt5_trader/stops.py
"""Stop-distance helpers shared by the backtester and the live runner (parity).
Pure: no MT5, no I/O."""
from __future__ import annotations


def ratchet_mult(open_profit_atr: float, sl_mult: float, trigger_T: float,
                 ratchet_R: float, ratchet_R_tight: float = 0.0,
                 volume_spike: bool = False) -> float:
    """Effective chandelier multiplier. Returns `sl_mult` (the wide disaster stop)
    until the position's favorable excursion reaches `trigger_T` ATRs, then the
    tighter `ratchet_R` — or `ratchet_R_tight` when `volume_spike` and
    `ratchet_R_tight > 0`. Disabled (returns `sl_mult`) when `ratchet_R <= 0` or
    `trigger_T <= 0`."""
    if ratchet_R <= 0 or trigger_T <= 0:
        return sl_mult
    if open_profit_atr < trigger_T:
        return sl_mult
    if volume_spike and ratchet_R_tight > 0:
        return ratchet_R_tight
    return ratchet_R
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_ratchet.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/mt5_trader/stops.py tests/test_ratchet.py
git commit -m "feat(stops): ratchet_mult — late-arming chandelier multiplier"
```

---

### Task 3 (Phase 1): Wire the price-only ratchet into the engine

**Files:**
- Modify: `src/mt5_trader/backtest/engine.py` (`BTConfig` fields; `_simulate_stops`)
- Test: `tests/test_ratchet.py` (append)

**Interfaces:**
- Consumes: `ratchet_mult` (Task 2).
- Produces: `BTConfig.ratchet_trigger: float = 0.0`, `BTConfig.ratchet_r: float = 0.0`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_ratchet.py
import numpy as np
import polars as pl

from mt5_trader.backtest.engine import BTConfig, run


def _ramp_then_pullback():
    # long signal; price ramps up (profit), then a sharp pullback that a tight
    # ratchet would exit but the wide disaster stop would ride through.
    closes = [100, 101, 103, 106, 110, 115, 121, 128, 120, 119, 118]
    highs =  [c + 0.5 for c in closes]
    lows =   [c - 0.5 for c in closes]
    n = len(closes)
    bars = pl.DataFrame({
        "ts": [f"2026-06-01T00:{i:02d}:00Z" for i in range(n)],
        "open": closes, "high": highs, "low": lows, "close": closes,
        "spread_mean": [0.1] * n, "n_ticks": [10.0] * n,
    })
    sig = np.ones(n)
    return bars, sig


def test_engine_ratchet_disabled_matches_default():
    bars, sig = _ramp_then_pullback()
    cfg = dict(sl_atr_mult=8.0, atr_window=3)
    a = run(bars, sig, BTConfig(ratchet_r=0.0, **cfg))
    b = run(bars, sig, BTConfig(**cfg))
    assert np.array_equal(a.bars["pnl"].to_numpy(), b.bars["pnl"].to_numpy())


def test_engine_ratchet_exits_deep_winner_earlier():
    bars, sig = _ramp_then_pullback()
    base = run(bars, sig, BTConfig(sl_atr_mult=8.0, atr_window=3))
    rat = run(bars, sig, BTConfig(sl_atr_mult=8.0, atr_window=3,
                                  ratchet_trigger=2.0, ratchet_r=1.0))
    # the ratchet adds a stop round-trip (exit) the wide baseline does not take
    assert rat.metrics["total_turnover"] > base.metrics["total_turnover"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_ratchet.py::test_engine_ratchet_exits_deep_winner_earlier -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'ratchet_trigger'`.

- [ ] **Step 3: Add `BTConfig` fields + import**

In `engine.py`, add the import beside the others:

```python
from ..stops import ratchet_mult
```

In `BTConfig`, after `atr_window`:

```python
    atr_window: int = 60       # ATR lookback for the stop distance
    ratchet_trigger: float = 0.0  # arm the profit-ratchet at this many ATRs of open profit; 0 = off
    ratchet_r: float = 0.0        # tighter chandelier mult once armed (< sl_atr_mult); 0 = off
```

- [ ] **Step 4: Apply the ratchet in `_simulate_stops`**

In `_simulate_stops`, replace the `sl = (...)` assignment with:

```python
        if cfg.sl_atr_mult > 0 and valid:
            opa = abs(extreme - entry) / atr_entry
            mult = ratchet_mult(opa, cfg.sl_atr_mult, cfg.ratchet_trigger, cfg.ratchet_r)
            sl = extreme - direction * mult * atr_entry
        else:
            sl = None
```

- [ ] **Step 5: Run to verify pass + full suite**

Run: `uv run pytest tests/test_ratchet.py -q && uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/mt5_trader/backtest/engine.py tests/test_ratchet.py
git commit -m "feat(engine): apply profit-ratchet to the chandelier stop sim"
```

---

### Task 4 (Phase 1): Ratchet sweep `research_ratchet.py` — GATE

**Files:**
- Create: `scripts/research_ratchet.py`

Research script; run-and-report. **STOP at the gate.**

- [ ] **Step 1: Write the sweep**

```python
#!/usr/bin/env python
"""RESEARCH: sweep the profit-ratchet (trigger T, ratchet R) per trend book on the
real feed. Deploy a book's ratchet only if it keeps/raises Sharpe on a plateau
vs the no-ratchet baseline.

    uv run scripts/research_ratchet.py
"""
from __future__ import annotations

import yaml

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.pipeline.data import bars_per_year, build_bars
from mt5_trader.strategies import REGISTRY
from mt5_trader.strategies.meanrev_pairs import spread_bars

TF_EVERY = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}
SL_ATR_MULT, ATR_WINDOW, SLIPPAGE_BPS = 8.0, 60, 0.5
TRIGGERS = [0.0, 1.0, 2.0, 3.0, 4.0]   # 0 = baseline (no ratchet)
RATCHETS = [2.0, 3.0, 4.0]
TREND_BOOKS = {"donchian", "vwap_trend", "london_orb"}


def book_bars(book: dict):
    every = TF_EVERY[book.get("timeframe", "M1")]
    bars = build_bars(book["symbol"], every).sort("ts")
    if book.get("symbol2"):
        bars = spread_bars(bars, build_bars(book["symbol2"], every).sort("ts"),
                           float(book.get("beta", 1.0)))
    return bars, bars_per_year(every)


def main():
    with open("config/portfolio.yaml") as f:
        books = [b for b in yaml.safe_load(f)["books"] if b["strategy"] in TREND_BOOKS]
    for book in books:
        name = book["strategy"]
        bars, bpy = book_bars(book)
        sig = REGISTRY[name](**(book.get("params", {}) or {})).signal(bars)
        print(f"\n=== {name}@{book['symbol']} {book.get('timeframe','M1')} ===")
        print(f"{'T':>4}{'R':>5}{'return%':>10}{'sharpe':>9}{'maxDD%':>9}{'turnover':>10}")
        for T in TRIGGERS:
            for R in ([0.0] if T == 0 else RATCHETS):
                cfg = BTConfig(sl_atr_mult=SL_ATR_MULT, atr_window=ATR_WINDOW,
                               slippage_bps=SLIPPAGE_BPS, bars_per_year=bpy,
                               ratchet_trigger=T, ratchet_r=R)
                m = run(bars, sig, cfg).metrics
                print(f"{T:>4.0f}{R:>5.1f}{m['total_return']*100:>10.2f}"
                      f"{m['sharpe']:>9.2f}{m['max_drawdown']*100:>9.2f}"
                      f"{m['total_turnover']:>10.2f}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `uv run scripts/research_ratchet.py`
Expected: per book, a (T, R) grid vs the T=0 baseline.

- [ ] **Step 3: Commit**

```bash
git add scripts/research_ratchet.py
git commit -m "feat(research): profit-ratchet (T,R) sweep per trend book"
```

- [ ] **Step 4: GATE — report + wait**

Per book: does any (T, R) keep/raise Sharpe vs T=0 on a plateau (neighbors agree)? Recommend the winning (T, R) per book, or "none — drop the ratchet." **Wait for sign-off before Phase 2/3.**

---

### Task 5 (Phase 2): Tick-volume gate — engine — GATE

**Only if Phase 0 showed `n_ticks` separates reversal from resumption AND Phase 1 produced a usable ratchet.**

**Files:**
- Modify: `src/mt5_trader/stops.py` (add `ntick_zscore`)
- Modify: `src/mt5_trader/backtest/engine.py` (`BTConfig` volume fields; `_simulate_stops` volume spike)
- Modify: `scripts/research_ratchet.py` (add a volume sweep block)
- Test: `tests/test_ratchet.py` (append)

**Interfaces:**
- Produces: `ntick_zscore(n_ticks: np.ndarray, window: int) -> np.ndarray`; `BTConfig.ratchet_r_tight`, `BTConfig.ratchet_vol_z`, `BTConfig.ratchet_vol_window`.

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_ratchet.py
from mt5_trader.stops import ntick_zscore


def test_ntick_zscore_flags_spike():
    nt = np.array([10.0] * 30 + [40.0])
    z = ntick_zscore(nt, window=20)
    assert z[-1] > 3.0           # the 40 is a clear spike
    assert abs(z[10]) < 1.0      # steady region ~0


def test_engine_volume_gate_tightens_on_spike():
    # same ramp+pullback, but spike n_ticks exactly at the post-peak pullback bar
    closes = [100, 101, 103, 106, 110, 115, 121, 128, 120, 119, 118]
    n = len(closes)
    nt = [10.0] * n
    nt[8] = 80.0    # volume spike at the pullback
    bars = pl.DataFrame({
        "ts": [f"2026-06-01T00:{i:02d}:00Z" for i in range(n)],
        "open": closes, "high": [c + 0.5 for c in closes],
        "low": [c - 0.5 for c in closes], "close": closes,
        "spread_mean": [0.1] * n, "n_ticks": nt,
    })
    sig = np.ones(n)
    loose = run(bars, sig, BTConfig(sl_atr_mult=8.0, atr_window=3,
                                    ratchet_trigger=2.0, ratchet_r=4.0))
    gated = run(bars, sig, BTConfig(sl_atr_mult=8.0, atr_window=3,
                                    ratchet_trigger=2.0, ratchet_r=4.0,
                                    ratchet_r_tight=1.0, ratchet_vol_z=3.0,
                                    ratchet_vol_window=5))
    # the spike makes the gated run exit at/around the pullback -> more turnover
    assert gated.metrics["total_turnover"] >= loose.metrics["total_turnover"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_ratchet.py::test_ntick_zscore_flags_spike -v`
Expected: FAIL — `ImportError: cannot import name 'ntick_zscore'`.

- [ ] **Step 3: Add `ntick_zscore` to `stops.py`**

```python
import numpy as np


def ntick_zscore(n_ticks: np.ndarray, window: int) -> np.ndarray:
    """Rolling z-score of tick volume: (n_ticks - rolling_mean) / rolling_std over
    `window` bars. NaN where the window is not yet full or std is 0; callers treat
    NaN as 'no spike'."""
    nt = np.asarray(n_ticks, dtype=float)
    out = np.full(len(nt), np.nan)
    for t in range(window, len(nt)):
        w = nt[t - window:t]
        sd = w.std()
        if sd > 0:
            out[t] = (nt[t] - w.mean()) / sd
    return out
```

- [ ] **Step 4: Add volume fields + gate to the engine**

In `BTConfig`, after `ratchet_r`:

```python
    ratchet_r_tight: float = 0.0   # tighter mult on a tick-volume spike; 0 = no volume gate
    ratchet_vol_z: float = 3.0     # n_ticks z-score threshold counted as a reversal spike
    ratchet_vol_window: int = 60   # lookback for the n_ticks z-score
```

In `engine.run`, where `_simulate_stops` is called, compute the z array and pass it:

```python
        ntz = None
        if cfg.ratchet_r_tight > 0 and "n_ticks" in bars.columns:
            from ..stops import ntick_zscore
            ntz = ntick_zscore(bars["n_ticks"].to_numpy(), cfg.ratchet_vol_window)
        ret, extra_turn = _simulate_stops(
            pos, close, bars["high"].to_numpy().astype(float),
            bars["low"].to_numpy().astype(float), atr_arr, cfg, ntz)
```

Change `_simulate_stops` signature to `def _simulate_stops(pos, close, high, low, atr_arr, cfg, ntz=None):` and update the ratchet line to pass the spike:

```python
        if cfg.sl_atr_mult > 0 and valid:
            opa = abs(extreme - entry) / atr_entry
            spike = ntz is not None and ntz[t] == ntz[t] and ntz[t] > cfg.ratchet_vol_z
            mult = ratchet_mult(opa, cfg.sl_atr_mult, cfg.ratchet_trigger, cfg.ratchet_r,
                                cfg.ratchet_r_tight, spike)
            sl = extreme - direction * mult * atr_entry
        else:
            sl = None
```

- [ ] **Step 5: Run tests + full suite**

Run: `uv run pytest tests/test_ratchet.py -q && uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Add a volume sweep block to `research_ratchet.py`**

After the (T, R) loop, for the best (T, R) per book, sweep `ratchet_vol_z ∈ {2.0, 2.5, 3.0}` × `ratchet_r_tight ∈ {1.0, 1.5}` and print return/Sharpe/maxDD/turnover vs the price-only (no-volume) ratchet. (Mirror the existing print loop; set `ratchet_r_tight`/`ratchet_vol_z`/`ratchet_vol_window` on the `BTConfig`.)

- [ ] **Step 7: Commit + GATE**

```bash
git add src/mt5_trader/stops.py src/mt5_trader/backtest/engine.py scripts/research_ratchet.py tests/test_ratchet.py
git commit -m "feat(stops,engine): tick-volume reversal gate for the profit-ratchet"
```

Run `uv run scripts/research_ratchet.py`; report whether the volume gate beats the price-only ratchet on a plateau. **Wait for sign-off.** If it does not beat price-only, deploy Phase 3 with the volume gate OFF (`ratchet_r_tight=0`).

---

### Task 6 (Phase 3): Wire the ratchet into the live runner — POST-GATE

**Files:**
- Modify: `src/mt5_trader/live/runner.py` (`chandelier_sl`; `RunnerCfg`; `PortfolioRunner.step` call)
- Test: `tests/test_reopen_guard.py` is unaffected; add `tests/test_ratchet.py` runner cases.

**Interfaces:**
- Consumes: `ratchet_mult`, `ntick_zscore`.
- Produces: `chandelier_sl(..., sl_mult, prev, ratchet_trigger=0.0, ratchet_r=0.0, ratchet_r_tight=0.0, volume_spike=False)`; `RunnerCfg.ratchet_trigger/ratchet_r/ratchet_r_tight/ratchet_vol_z/ratchet_vol_window`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_ratchet.py
from mt5_trader.live.runner import chandelier_sl


def test_chandelier_ratchet_tightens_deep_winner():
    # long, entry 100, atr 1, sl_mult 8. Drive the extreme to 110 (10 ATRs profit).
    prev = None
    sl0, prev = chandelier_sl(1.0, 100.0, 100.0, 100.0, 1.0, 8.0, prev)
    sl1, prev = chandelier_sl(1.0, 110.0, 110.0, 110.0, 1.0, 8.0, prev,
                              ratchet_trigger=2.0, ratchet_r=3.0)
    # disaster stop would be 110-8=102; ratchet (armed, 10>=2 ATRs) -> 110-3=107
    assert abs(sl1 - 107.0) < 1e-9


def test_chandelier_no_ratchet_unchanged():
    sl, _ = chandelier_sl(1.0, 100.0, 100.0, 100.0, 1.0, 8.0, None)
    assert abs(sl - 92.0) < 1e-9   # 100 - 8*1
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_ratchet.py::test_chandelier_ratchet_tightens_deep_winner -v`
Expected: FAIL — `chandelier_sl()` has no `ratchet_trigger` kwarg.

- [ ] **Step 3: Extend `chandelier_sl`**

Add the import at the top of `runner.py`: `from ..stops import ratchet_mult`. Change `chandelier_sl` to accept the ratchet params and swap the mult:

```python
def chandelier_sl(side, price, high, low, atr_value, sl_mult, prev,
                  ratchet_trigger=0.0, ratchet_r=0.0, ratchet_r_tight=0.0,
                  volume_spike=False):
    # ... unchanged body until the sl computation ...
    opa = abs(extreme - entry) / atr_entry if atr_entry > 0 else 0.0
    mult = ratchet_mult(opa, sl_mult, ratchet_trigger, ratchet_r,
                        ratchet_r_tight, volume_spike)
    sl = extreme - direction * mult * atr_entry
    return sl, (side, atr_entry, entry, extreme)
```

- [ ] **Step 4: Add `RunnerCfg` fields + pass them in `PortfolioRunner.step`**

In `RunnerCfg`, add: `ratchet_trigger: float = 0.0`, `ratchet_r: float = 0.0`, `ratchet_r_tight: float = 0.0`, `ratchet_vol_z: float = 3.0`, `ratchet_vol_window: int = 60`.

In `PortfolioRunner.step`, at the `chandelier_sl(...)` call, compute the volume spike from the stop-TF bars and pass the ratchet params:

```python
            spike = False
            if self.cfg.ratchet_r_tight > 0 and "n_ticks" in bars.columns:
                from ..stops import ntick_zscore
                z = ntick_zscore(bars["n_ticks"].to_numpy(), self.cfg.ratchet_vol_window)
                spike = z[-1] == z[-1] and z[-1] > self.cfg.ratchet_vol_z
            sl, self._trail[sym] = chandelier_sl(
                lots, price, high, low, atr_val, self.cfg.sl_atr_mult,
                self._trail.get(sym),
                ratchet_trigger=self.cfg.ratchet_trigger, ratchet_r=self.cfg.ratchet_r,
                ratchet_r_tight=self.cfg.ratchet_r_tight, volume_spike=spike)
```

- [ ] **Step 5: Run tests + full suite**

Run: `uv run pytest tests/test_ratchet.py -q && uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/mt5_trader/live/runner.py tests/test_ratchet.py
git commit -m "feat(live): profit-ratchet + volume gate in chandelier_sl (default off)"
```

---

### Task 7 (Phase 3): Load ratchet params from portfolio.yaml + deploy — POST-GATE

**Files:**
- Modify: `scripts/run_portfolio.py` (pass ratchet keys into `RunnerCfg`)
- Modify: `config/portfolio.yaml` (set the params that passed the gates)

- [ ] **Step 1: Read ratchet keys in `run_portfolio.py`**

Where `RunnerCfg(...)` is built from the yaml `cfg`, add (matching how `sl_atr_mult` is read):

```python
        ratchet_trigger=float(cfg.get("ratchet_trigger", 0.0)),
        ratchet_r=float(cfg.get("ratchet_r", 0.0)),
        ratchet_r_tight=float(cfg.get("ratchet_r_tight", 0.0)),
        ratchet_vol_z=float(cfg.get("ratchet_vol_z", 3.0)),
        ratchet_vol_window=int(cfg.get("ratchet_vol_window", 60)),
```

- [ ] **Step 2: Set the winning params in `portfolio.yaml`**

Add top-level keys (beside `sl_atr_mult`) with the values from the gates, with a comment citing the sweep result. Leave at 0 (off) if no config cleared its gate.

- [ ] **Step 3: Validate config loads + full suite**

Run: `uv run python -c "import yaml; print({k:v for k,v in yaml.safe_load(open('config/portfolio.yaml')).items() if 'ratchet' in k or k=='sl_atr_mult'})"`
Then: `uv run pytest -q`
Expected: prints the ratchet keys; suite passes.

- [ ] **Step 4: Commit**

```bash
git add scripts/run_portfolio.py config/portfolio.yaml
git commit -m "config(portfolio): enable profit-ratchet params that cleared the sweep"
```

Deploy (after merge): VPS `git pull` + restart `MT5Portfolio` via `Stop+Start-ScheduledTask`.

---

## Self-Review

**Spec coverage:**
- Late-arming ratchet (mult swap, never tightens entry stop) → Task 2 (`ratchet_mult`), Task 3 (engine). ✓
- Shared fn / parity (engine + runner) → Task 2 + Task 3 + Task 6. ✓
- Phase 0 diagnostic incl. the n_ticks-separation test → Task 1. ✓
- Tick-volume gate → Task 5 (`ntick_zscore` + engine), Task 6 (runner). ✓
- Per-phase gates → Tasks 1, 4, 5 stop for sign-off. ✓
- Deploy / portfolio.yaml params, default off → Task 6 (cfg), Task 7. ✓
- Disaster stop untouched / disabled-by-default → `ratchet_r<=0` short-circuit in Task 2, verified by `test_engine_ratchet_disabled_matches_default` + `test_chandelier_no_ratchet_unchanged`. ✓

**Placeholder scan:** Task 5 Step 6 (volume sweep block) describes a mirror of the existing print loop rather than full code — acceptable, the surrounding (T,R) loop in Task 4 is the concrete template and the new `BTConfig` fields are shown. All other steps carry concrete code.

**Type consistency:** `ratchet_mult(open_profit_atr, sl_mult, trigger_T, ratchet_R, ratchet_R_tight=0.0, volume_spike=False)` used identically in engine (Task 3/5) and runner (Task 6). `BTConfig.ratchet_trigger/ratchet_r/ratchet_r_tight/ratchet_vol_z/ratchet_vol_window` and the matching `RunnerCfg` names are consistent. `ntick_zscore(n_ticks, window)` consistent across Tasks 5/6.
