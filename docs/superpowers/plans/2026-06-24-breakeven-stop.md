# Breakeven Stop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Once a trade is up `T`×ATR, floor its stop to ~entry so a winner can't become a loss — without truncating trends.

**Architecture:** A pure `breakeven_floor()` raises (long) / lowers (short) the chandelier disaster stop toward `entry + cost-buffer` once a position is `T`×ATR in profit. The backtest engine (`_simulate_stops`) and live runner (`chandelier_sl`) both call it → research == live. Research-gated; default off.

**Tech Stack:** Python 3.11, polars, numpy, pytest, `uv run`.

## Global Constraints

- TDD: failing test first. Suite: `uv run pytest`.
- The disaster stop is untouched until armed; `breakeven_trigger <= 0` ⇒ disabled ⇒ byte-identical to today. Default off.
- One shared `breakeven_floor()` in `src/mt5_trader/stops.py`; engine + runner both call it (parity). `stops.py` imports nothing internal (no circular import).
- `armed` derives from `|extreme - entry| / atr_entry >= trigger` — no extra state.
- Buffer is in ATRs; floor = `entry + direction * buffer * atr_entry` (long above entry, short below) so a stop-out is net non-loss after round-trip cost.
- Real broker feed (`build_bars`) for backtests. Phase 2 (Tasks 4–5) only after the Task 3 gate + user sign-off.

---

### Task 1: `breakeven_floor` pure helper

**Files:**
- Create: `src/mt5_trader/stops.py`
- Test: `tests/test_breakeven.py`

**Interfaces:**
- Produces: `breakeven_floor(sl: float, side: float, entry: float, atr_entry: float, open_profit_atr: float, trigger: float, buffer: float) -> float`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_breakeven.py
from mt5_trader.stops import breakeven_floor


def test_below_trigger_unchanged():
    # 1 ATR profit, trigger 2 -> not armed -> disaster sl unchanged
    assert breakeven_floor(92.0, 1.0, 100.0, 1.0, 1.0, trigger=2.0, buffer=0.1) == 92.0


def test_armed_long_floors_up_to_entry_plus_buffer():
    # disaster sl 92 below entry; armed (5>=2) -> floor up to 100 + 0.1*1
    assert breakeven_floor(92.0, 1.0, 100.0, 1.0, 5.0, 2.0, 0.1) == 100.1


def test_armed_short_floors_down_to_entry_minus_buffer():
    assert breakeven_floor(108.0, -1.0, 100.0, 1.0, 5.0, 2.0, 0.1) == 99.9


def test_disabled_when_trigger_nonpositive():
    assert breakeven_floor(92.0, 1.0, 100.0, 1.0, 5.0, trigger=0.0, buffer=0.1) == 92.0


def test_deep_winner_disaster_already_past_entry_is_noop():
    # disaster sl 105 already above entry -> breakeven floor does not lower it
    assert breakeven_floor(105.0, 1.0, 100.0, 1.0, 9.0, 2.0, 0.1) == 105.0


def test_zero_buffer_floors_at_entry():
    assert breakeven_floor(92.0, 1.0, 100.0, 1.0, 5.0, 2.0, 0.0) == 100.0
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_breakeven.py -q`
Expected: FAIL — `ModuleNotFoundError: mt5_trader.stops`.

- [ ] **Step 3: Implement**

```python
# src/mt5_trader/stops.py
"""Stop-distance helpers shared by the backtester and the live runner (parity).
Pure: no MT5, no I/O."""
from __future__ import annotations


def breakeven_floor(sl: float, side: float, entry: float, atr_entry: float,
                    open_profit_atr: float, trigger: float, buffer: float) -> float:
    """Floor the chandelier stop at ~entry once open profit reaches `trigger` ATRs,
    so a winner cannot turn into a loss. Returns `sl` unchanged when `trigger <= 0`,
    not yet armed, or `atr_entry <= 0`. Long: raises sl up to `entry + buffer*atr`;
    short: lowers sl down to `entry - buffer*atr`. A deep winner whose disaster stop
    is already past entry is unaffected (the floor only ever moves sl toward entry,
    never loosens it)."""
    if trigger <= 0 or atr_entry <= 0 or open_profit_atr < trigger:
        return sl
    be = entry + (1.0 if side > 0 else -1.0) * buffer * atr_entry
    return max(sl, be) if side > 0 else min(sl, be)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_breakeven.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/mt5_trader/stops.py tests/test_breakeven.py
git commit -m "feat(stops): breakeven_floor — protect a winner from turning into a loss"
```

---

### Task 2: Wire breakeven into the engine

**Files:**
- Modify: `src/mt5_trader/backtest/engine.py` (`BTConfig` fields; import; `_simulate_stops`)
- Test: `tests/test_breakeven.py` (append)

**Interfaces:**
- Consumes: `breakeven_floor` (Task 1).
- Produces: `BTConfig.breakeven_trigger: float = 0.0`, `BTConfig.breakeven_buffer: float = 0.0`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_breakeven.py
import numpy as np
import polars as pl

from mt5_trader.backtest.engine import BTConfig, run


def _ramp_then_through_entry():
    # 3 warmup bars (sig 0) so ATR(3) is defined at entry; modest ramp (shallow
    # winner: disaster stop stays below entry) then a retrace through entry and down.
    closes = [100, 101, 102, 103, 104, 105, 104, 102, 100, 98, 96]
    n = len(closes)
    bars = pl.DataFrame({
        "ts": [f"2026-06-01T00:{i:02d}:00Z" for i in range(n)],
        "open": closes, "high": [c + 0.5 for c in closes],
        "low": [c - 0.5 for c in closes], "close": closes,
        "spread_mean": [0.1] * n, "n_ticks": [10.0] * n,
    })
    sig = np.array([0.0, 0.0, 0.0] + [1.0] * (n - 3))
    return bars, sig


def test_engine_breakeven_disabled_matches_default():
    bars, sig = _ramp_then_through_entry()
    cfg = dict(sl_atr_mult=8.0, atr_window=3)
    a = run(bars, sig, BTConfig(breakeven_trigger=0.0, **cfg))
    b = run(bars, sig, BTConfig(**cfg))
    assert np.array_equal(a.bars["pnl"].to_numpy(), b.bars["pnl"].to_numpy())


def test_engine_breakeven_avoids_the_loss():
    bars, sig = _ramp_then_through_entry()
    base = run(bars, sig, BTConfig(sl_atr_mult=8.0, atr_window=3))
    be = run(bars, sig, BTConfig(sl_atr_mult=8.0, atr_window=3,
                                 breakeven_trigger=2.0, breakeven_buffer=0.1))
    # breakeven exits near entry on the retrace; baseline rides the wide stop into loss
    assert be.metrics["total_return"] > base.metrics["total_return"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_breakeven.py::test_engine_breakeven_avoids_the_loss -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'breakeven_trigger'`.

- [ ] **Step 3: Add `BTConfig` fields + import**

In `engine.py`, beside the other imports:

```python
from ..stops import breakeven_floor
```

In `BTConfig`, after `atr_window`:

```python
    atr_window: int = 60       # ATR lookback for the stop distance
    breakeven_trigger: float = 0.0  # arm breakeven at this many ATRs of open profit; 0 = off
    breakeven_buffer: float = 0.0   # floor sits at entry +/- buffer*ATR (covers round-trip cost)
```

- [ ] **Step 4: Apply the floor in `_simulate_stops`**

Replace the `sl = (...)` assignment with:

```python
        if cfg.sl_atr_mult > 0 and valid:
            sl = extreme - direction * cfg.sl_atr_mult * atr_entry
            opa = abs(extreme - entry) / atr_entry
            sl = breakeven_floor(sl, side, entry, atr_entry, opa,
                                 cfg.breakeven_trigger, cfg.breakeven_buffer)
        else:
            sl = None
```

- [ ] **Step 5: Run tests + full suite**

Run: `uv run pytest tests/test_breakeven.py -q && uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/mt5_trader/backtest/engine.py tests/test_breakeven.py
git commit -m "feat(engine): apply breakeven floor to the chandelier stop sim"
```

---

### Task 3: Breakeven sweep `research_breakeven.py` — GATE

**Files:**
- Create: `scripts/research_breakeven.py`

Research script; run-and-report. **STOP at the gate.**

- [ ] **Step 1: Write the sweep**

```python
#!/usr/bin/env python
"""RESEARCH: sweep the breakeven stop (trigger T, buffer B) per trend book on the
real feed. Keep a book's breakeven only if it raises/holds Sharpe on a plateau and
trims the left tail (maxDD) vs the no-breakeven baseline.

    uv run scripts/research_breakeven.py
"""
from __future__ import annotations

import yaml

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.pipeline.data import bars_per_year, build_bars
from mt5_trader.strategies import REGISTRY
from mt5_trader.strategies.meanrev_pairs import spread_bars

TF_EVERY = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}
SL_ATR_MULT, ATR_WINDOW, SLIPPAGE_BPS = 8.0, 60, 0.5
TRIGGERS = [0.0, 1.0, 2.0, 3.0, 4.0]   # 0 = baseline (no breakeven)
BUFFERS = [0.0, 0.1, 0.2]
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
        print(f"{'T':>4}{'B':>5}{'return%':>10}{'sharpe':>9}{'maxDD%':>9}{'turnover':>10}")
        for T in TRIGGERS:
            for B in ([0.0] if T == 0 else BUFFERS):
                cfg = BTConfig(sl_atr_mult=SL_ATR_MULT, atr_window=ATR_WINDOW,
                               slippage_bps=SLIPPAGE_BPS, bars_per_year=bpy,
                               breakeven_trigger=T, breakeven_buffer=B)
                m = run(bars, sig, cfg).metrics
                print(f"{T:>4.0f}{B:>5.1f}{m['total_return']*100:>10.2f}"
                      f"{m['sharpe']:>9.2f}{m['max_drawdown']*100:>9.2f}"
                      f"{m['total_turnover']:>10.2f}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `uv run scripts/research_breakeven.py`
Expected: per book, a (T, B) grid vs the T=0 baseline.

- [ ] **Step 3: Commit**

```bash
git add scripts/research_breakeven.py
git commit -m "feat(research): breakeven (T,B) sweep per trend book"
```

- [ ] **Step 4: GATE — report + wait**

Per book: does any (T, B) raise/hold Sharpe AND trim maxDD vs T=0 on a plateau? Recommend the winning (T, B) per book, or "none — drop breakeven." **Wait for sign-off before Phase 2 (Tasks 4–5).**

---

### Task 4 (Phase 2): Wire breakeven into the live runner — POST-GATE

**Files:**
- Modify: `src/mt5_trader/live/runner.py` (`chandelier_sl`; `RunnerCfg`; `PortfolioRunner.step` call)
- Test: `tests/test_breakeven.py` (append)

**Interfaces:**
- Consumes: `breakeven_floor`.
- Produces: `chandelier_sl(..., sl_mult, prev, breakeven_trigger=0.0, breakeven_buffer=0.0)`; `RunnerCfg.breakeven_trigger/breakeven_buffer`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_breakeven.py
from mt5_trader.live.runner import chandelier_sl


def test_chandelier_breakeven_floors_armed_long():
    # entry 100, atr 1, sl_mult 8. Push extreme to 103 (3 ATRs profit) -> armed.
    _, prev = chandelier_sl(1.0, 100.0, 100.0, 100.0, 1.0, 8.0, None)
    sl, _ = chandelier_sl(1.0, 103.0, 103.0, 103.0, 1.0, 8.0, prev,
                          breakeven_trigger=2.0, breakeven_buffer=0.1)
    # disaster sl would be 103-8=95; breakeven floors up to 100 + 0.1*1
    assert abs(sl - 100.1) < 1e-9


def test_chandelier_no_breakeven_unchanged():
    sl, _ = chandelier_sl(1.0, 100.0, 100.0, 100.0, 1.0, 8.0, None)
    assert abs(sl - 92.0) < 1e-9   # 100 - 8*1, untouched
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_breakeven.py::test_chandelier_breakeven_floors_armed_long -v`
Expected: FAIL — `chandelier_sl()` has no `breakeven_trigger` kwarg.

- [ ] **Step 3: Extend `chandelier_sl`**

Add at the top of `runner.py`: `from ..stops import breakeven_floor`. Change `chandelier_sl` to accept the params and floor the computed sl (keep the `prev`/extreme/atr_entry logic unchanged):

```python
def chandelier_sl(side, price, high, low, atr_value, sl_mult, prev,
                  breakeven_trigger=0.0, breakeven_buffer=0.0):
    # ... unchanged body until: sl = extreme - direction * sl_mult * atr_entry
    opa = abs(extreme - entry) / atr_entry if atr_entry > 0 else 0.0
    sl = breakeven_floor(sl, side, entry, atr_entry, opa,
                         breakeven_trigger, breakeven_buffer)
    return sl, (side, atr_entry, entry, extreme)
```

- [ ] **Step 4: Add `RunnerCfg` fields + pass them in `PortfolioRunner.step`**

In `RunnerCfg`, add: `breakeven_trigger: float = 0.0`, `breakeven_buffer: float = 0.0`.

In `PortfolioRunner.step`, at the `chandelier_sl(...)` call, pass them:

```python
            sl, self._trail[sym] = chandelier_sl(
                lots, price, high, low, atr_val, self.cfg.sl_atr_mult,
                self._trail.get(sym),
                breakeven_trigger=self.cfg.breakeven_trigger,
                breakeven_buffer=self.cfg.breakeven_buffer)
```

- [ ] **Step 5: Run tests + full suite**

Run: `uv run pytest tests/test_breakeven.py -q && uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/mt5_trader/live/runner.py tests/test_breakeven.py
git commit -m "feat(live): breakeven floor in chandelier_sl (default off)"
```

---

### Task 5 (Phase 2): Load breakeven params from portfolio.yaml — POST-GATE

**Files:**
- Modify: `scripts/run_portfolio.py` (pass breakeven keys into `RunnerCfg`)
- Modify: `config/portfolio.yaml` (set the params that passed the gate)

- [ ] **Step 1: Read breakeven keys in `run_portfolio.py`**

Where `RunnerCfg(...)` is built from the yaml `cfg`, add (matching how `sl_atr_mult` is read):

```python
        breakeven_trigger=float(cfg.get("breakeven_trigger", 0.0)),
        breakeven_buffer=float(cfg.get("breakeven_buffer", 0.0)),
```

- [ ] **Step 2: Set the winning params in `portfolio.yaml`**

Add top-level keys (beside `sl_atr_mult`) with the gate's values + a comment citing the sweep result. Leave at 0 (off) if no config cleared the gate.

- [ ] **Step 3: Validate config loads + full suite**

Run: `uv run python -c "import yaml; print({k:v for k,v in yaml.safe_load(open('config/portfolio.yaml')).items() if 'breakeven' in k or k=='sl_atr_mult'})"`
Then: `uv run pytest -q`
Expected: prints the keys; suite passes.

- [ ] **Step 4: Commit**

```bash
git add scripts/run_portfolio.py config/portfolio.yaml
git commit -m "config(portfolio): enable breakeven params that cleared the sweep"
```

Deploy (after merge): VPS `git pull` + restart `MT5Portfolio` via `Stop+Start-ScheduledTask`.

---

## Self-Review

**Spec coverage:**
- Breakeven floor (floor sl at entry+buffer once armed, disaster untouched, no-op for deep winners) → Task 1 (`breakeven_floor`), Task 2 (engine). ✓
- Shared fn / parity → Task 1 + Task 2 + Task 4. ✓
- Phase 1 sweep + gate → Task 3. ✓
- Phase 2 deploy / portfolio.yaml, default off → Task 4 (cfg), Task 5. ✓
- Default-off / disaster untouched → `trigger<=0` short-circuit (Task 1), verified by `test_engine_breakeven_disabled_matches_default` + `test_chandelier_no_breakeven_unchanged`. ✓
- Buffer in ATRs, long-above / short-below entry → Task 1 tests. ✓

**Placeholder scan:** none — every step carries concrete code/commands.

**Type consistency:** `breakeven_floor(sl, side, entry, atr_entry, open_profit_atr, trigger, buffer)` used identically in engine (Task 2) and runner (Task 4). `BTConfig.breakeven_trigger/breakeven_buffer` and `RunnerCfg.breakeven_trigger/breakeven_buffer` names align; `run_portfolio.py` reads the same yaml keys (Task 5).
