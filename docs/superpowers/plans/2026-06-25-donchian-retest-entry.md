# Donchian@M5 Retest-Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure, in backtest, whether delaying each Donchian@M5 breakout entry to a pullback (retest) of the broken channel edge improves the net portfolio — before building any live order machinery.

**Architecture:** A pure, causal retest-fill simulator (`backtest/retest.py`) converts the existing Donchian `{-1,0,1}` signal into a transformed position series where entry is delayed until a resting limit at the broken edge `L` fills (or skips / market-falls-back at timeout). A registered `donchian_retest` strategy wraps it so every existing tool (`engine.run`, `validate_candidate`, the portfolio net builder) consumes it unmodified. Two research scripts produce the Stage-1 (standalone, L-priced entry quality) and Stage-2 (net portfolio) evidence.

**Tech Stack:** Python, numpy, polars, pytest, uv. Existing repo modules: `mt5_trader.backtest.engine`, `mt5_trader.strategies`, `mt5_trader.pipeline.data`, `mt5_trader.pipeline.validate`.

## Global Constraints

- Scope is **`donchian` on XAUUSD@M5 only**. Do not touch the other 5 books, the live runner, `reconcile`, or `mt5_gateway`. (Live machinery is a separate follow-up, built only if Stage 2 survives.)
- **No-lookahead is mandatory.** `donchian_retest.signal()` must be causal: `signal(bars)[:k] == signal(bars[:k])`. It is checked automatically by the registry-wide `tests/test_strategies.py::test_no_lookahead`.
- **Exits stay identical to baseline Donchian.** Only the entry changes. Enforce via the invariant `transformed_pos != 0 ⟹ transformed_pos == raw_signal` (position held only while the raw signal is nonzero).
- Anchor `L` = the broken channel edge captured **at the breakout bar**: `entry_hi` for a long, `entry_lo` for a short.
- Sweep is `max_wait ∈ {12, 24, 48}` M5 bars × `policy ∈ {skip, market_fallback}` (`fallback: bool`).
- Honesty caveats stated in every report: optimistic touch-fill (`low ≤ L ⇒ filled at L`); ~5wk sample → Donchian already fails deflated-Sharpe there → **directional evidence, not a deploy gate.**
- Test runner: `uv run pytest`. Scripts: `uv run scripts/<name>.py`.
- Commits: this work is on a feature branch `feat/donchian-retest-entry` (created in Task 0). Commit after each task.

---

### Task 0: Branch

**Files:** none (git only).

- [ ] **Step 1: Create the feature branch**

Run:
```bash
git checkout -b feat/donchian-retest-entry
```
Expected: `Switched to a new branch 'feat/donchian-retest-entry'`

- [ ] **Step 2: Commit the design + plan docs already written**

```bash
git add docs/superpowers/specs/2026-06-25-donchian-retest-entry-design.md docs/superpowers/plans/2026-06-25-donchian-retest-entry.md
git commit -m "docs(donchian-retest): design spec + implementation plan

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 1: Expose Donchian channel levels (DRY refactor)

The retest sim needs the same `entry_hi`/`entry_lo` the signal already computes. Extract them into a `levels()` method so there is one source of truth, guarded by the existing Donchian tests.

**Files:**
- Modify: `src/mt5_trader/strategies/momentum_breakout.py:26-57` (the `signal` method)
- Test: `tests/test_strategies.py` (existing `test_all_strategies_produce_valid_signals`, `test_no_lookahead` already cover behaviour preservation)

**Interfaces:**
- Produces: `DonchianBreakout.levels(bars: pl.DataFrame) -> dict[str, np.ndarray]` with keys `entry_hi, entry_lo, exit_hi, exit_lo, ema` (all length `len(bars)`, np.float64, with NaNs in the warmup region).

- [ ] **Step 1: Run the existing Donchian tests to capture the baseline (must pass before refactor)**

Run: `uv run pytest tests/test_strategies.py -q`
Expected: PASS (all green).

- [ ] **Step 2: Extract `levels()` and have `signal()` consume it**

Replace the body of `signal` (momentum_breakout.py) so the channel math lives in `levels`:

```python
    def levels(self, bars: pl.DataFrame) -> dict[str, np.ndarray]:
        df = bars.with_columns(
            entry_hi=pl.col("close").rolling_max(self.entry_n).shift(1),
            entry_lo=pl.col("close").rolling_min(self.entry_n).shift(1),
            exit_hi=pl.col("close").rolling_max(self.exit_n).shift(1),
            exit_lo=pl.col("close").rolling_min(self.exit_n).shift(1),
            ema=pl.col("close").ewm_mean(span=self.ema_n),
        )
        return {k: df[k].to_numpy() for k in
                ("entry_hi", "entry_lo", "exit_hi", "exit_lo", "ema")}

    def signal(self, bars: pl.DataFrame) -> np.ndarray:
        lv = self.levels(bars)
        close = bars["close"].to_numpy()
        entry_hi, entry_lo = lv["entry_hi"], lv["entry_lo"]
        exit_hi, exit_lo = lv["exit_hi"], lv["exit_lo"]
        ema = lv["ema"]

        pos = np.zeros(len(close))
        state = 0.0
        for t in range(len(close)):
            if np.isnan(entry_hi[t]):
                pos[t] = 0.0
                continue
            if state == 0.0:
                if close[t] > entry_hi[t] and close[t] > ema[t]:
                    state = 1.0
                elif close[t] < entry_lo[t] and close[t] < ema[t]:
                    state = -1.0
            elif state == 1.0 and close[t] < exit_lo[t]:
                state = 0.0
            elif state == -1.0 and close[t] > exit_hi[t]:
                state = 0.0
            pos[t] = state
        return pos
```

- [ ] **Step 3: Run the Donchian tests to verify behaviour is unchanged**

Run: `uv run pytest tests/test_strategies.py -q`
Expected: PASS (identical to Step 1 — the refactor is behaviour-preserving).

- [ ] **Step 4: Commit**

```bash
git add src/mt5_trader/strategies/momentum_breakout.py
git commit -m "refactor(donchian): extract channel levels() for reuse

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Retest-fill simulator (core, TDD)

**Files:**
- Create: `src/mt5_trader/backtest/retest.py`
- Test: `tests/test_retest.py`

**Interfaces:**
- Produces:
  - `RetestFill` dataclass with fields `pos: np.ndarray`, `entry_px: np.ndarray`, `n_armed: int`, `n_filled: int`, `n_fallback: int`, `n_skipped: int`.
  - `simulate_retest_entry(raw_signal, entry_hi, entry_lo, high, low, close, max_wait: int, fallback: bool) -> RetestFill`. All array args are length-`n` np.float64. `pos` ∈ {-1,0,1}; `entry_px` is `L` at limit fills, `close` at market fallbacks, `NaN` elsewhere. Invariant: `n_armed == n_filled + n_fallback + n_skipped`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_retest.py`:

```python
import numpy as np

from mt5_trader.backtest.retest import simulate_retest_entry

# Long-breakout episode: raw nonzero on bars 2..6, exit (->0) at bar 7.
RAW = np.array([0, 0, 1, 1, 1, 1, 1, 0], dtype=float)
HI = np.full(8, 100.0)   # anchor L for a long = entry_hi captured at breakout bar 2
LO = np.full(8, 90.0)    # unused for a long
HIGH = np.full(8, 101.0) # unused for a long
CLOSE = np.full(8, 101.0)


def test_fills_on_touch_priced_at_level():
    # low dips to <=100 on bar 4 (bars 2,3 stay above the level)
    low = np.array([101, 101, 100.5, 100.2, 99.9, 100.1, 100.1, 100.1], dtype=float)
    f = simulate_retest_entry(RAW, HI, LO, HIGH, low, CLOSE, max_wait=5, fallback=False)
    assert list(f.pos) == [0, 0, 0, 0, 1, 1, 1, 0]   # held from the fill bar to the exit
    assert f.entry_px[4] == 100.0                      # priced at L, not the bar close
    assert (f.n_armed, f.n_filled, f.n_fallback, f.n_skipped) == (1, 1, 0, 0)


def test_no_fill_on_breakout_bar():
    # breakout bar 2 low is below L, but a fill there would be non-causal -> never fills
    low = np.array([101, 101, 99.0, 100.2, 100.3, 100.4, 100.5, 100.6], dtype=float)
    f = simulate_retest_entry(RAW, HI, LO, HIGH, low, CLOSE, max_wait=5, fallback=False)
    assert not f.pos.any()
    assert (f.n_armed, f.n_filled, f.n_skipped) == (1, 0, 1)


def test_skips_when_no_touch_under_skip_policy():
    low = np.full(8, 100.5)  # never reaches L
    f = simulate_retest_entry(RAW, HI, LO, HIGH, low, CLOSE, max_wait=2, fallback=False)
    assert not f.pos.any()                              # no re-arm mid-run
    assert (f.n_armed, f.n_filled, f.n_fallback, f.n_skipped) == (1, 0, 0, 1)


def test_fallback_market_entry_at_deadline():
    low = np.full(8, 100.5)  # never reaches L; deadline is bar 4 (arm bar 2 + max_wait 2)
    f = simulate_retest_entry(RAW, HI, LO, HIGH, low, CLOSE, max_wait=2, fallback=True)
    assert list(f.pos) == [0, 0, 0, 0, 1, 1, 1, 0]
    assert f.entry_px[4] == 101.0                       # market = bar close at the deadline
    assert (f.n_armed, f.n_filled, f.n_fallback, f.n_skipped) == (1, 0, 1, 0)


def test_skip_when_exit_fires_before_deadline_even_with_fallback():
    raw = np.array([0, 0, 1, 1, 0, 0, 0, 0], dtype=float)  # exits at bar 4, before deadline
    low = np.full(8, 100.5)  # never touches L
    f = simulate_retest_entry(raw, HI, LO, HIGH, low, CLOSE, max_wait=10, fallback=True)
    assert not f.pos.any()
    assert (f.n_armed, f.n_filled, f.n_fallback, f.n_skipped) == (1, 0, 0, 1)


def test_counts_invariant_on_random_input():
    rng = np.random.default_rng(0)
    n = 500
    raw = np.sign(np.where(rng.random(n) < 0.1, rng.normal(size=n), 0.0))
    px = 100 + np.cumsum(rng.normal(size=n))
    hi = px - 0.5
    lo = px + 0.5  # for shorts (high>=entry_lo)
    f = simulate_retest_entry(raw, hi, lo, px + 0.3, px - 0.3, px,
                              max_wait=10, fallback=False)
    assert f.n_armed == f.n_filled + f.n_fallback + f.n_skipped
    # exits unchanged: a held bar must coincide with a nonzero raw bar
    held = f.pos != 0
    assert np.array_equal(f.pos[held], raw[held])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_retest.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'mt5_trader.backtest.retest'`.

- [ ] **Step 3: Implement the simulator**

Create `src/mt5_trader/backtest/retest.py`:

```python
"""Retest-entry fill model for breakout strategies (backtest-only).

Delays each breakout entry to a pullback of the broken channel edge: when the
raw {-1,0,1} signal opens a new nonzero episode, an anchor L is captured at the
breakout bar (entry_hi for a long, entry_lo for a short) and a resting limit at
L is simulated. A buy-limit fills the first LATER bar whose low<=L (a sell-limit
high>=L), priced at L. If the strategy's own exit fires before a fill, the trade
is skipped; if max_wait bars pass with no fill, it is either skipped
(fallback=False) or entered at market (fallback=True).

The output position is held only while the raw signal is nonzero, so EXITS match
the baseline exactly — only the entry changes. Causal by construction: bar t uses
data up to and including bar t (the limit rests at the breakout bar's close, so
the earliest possible fill is the next bar).

Optimistic-fill caveat: a bar whose low touches L is assumed filled at L; real
fills depend on queue position and wick depth (confirm later via tick replay).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class RetestFill:
    pos: np.ndarray        # transformed position series {-1,0,1}; entry delayed to fill bar
    entry_px: np.ndarray   # L at limit fills, close at market fallbacks, NaN elsewhere
    n_armed: int           # breakout episodes seen
    n_filled: int          # entered by the resting limit at the anchor
    n_fallback: int        # entered at market after max_wait (fallback=True only)
    n_skipped: int         # never entered (exit fired first, or wait expired under skip)


def simulate_retest_entry(raw_signal, entry_hi, entry_lo, high, low, close,
                          max_wait: int, fallback: bool) -> RetestFill:
    raw = np.asarray(raw_signal, dtype=float)
    n = len(raw)
    pos = np.zeros(n)
    entry_px = np.full(n, np.nan)
    n_armed = n_filled = n_fallback = n_skipped = 0
    in_pos = armed = resolved = False
    side = L = 0.0
    wait = 0
    for t in range(n):
        s = raw[t]
        if s == 0.0:                       # flat: episode (if any) is over
            if armed:                      # was still waiting -> exit beat the fill
                n_skipped += 1
            in_pos = armed = resolved = False
            continue
        if in_pos:                         # already filled -> hold to the exit
            pos[t] = side
            continue
        if resolved:                       # decided (skipped) -> wait for flat, no re-arm
            continue
        if not armed:                      # new breakout episode at this bar
            armed, side, wait = True, s, 0
            L = entry_hi[t] if s > 0 else entry_lo[t]
            n_armed += 1
            continue                       # causal: earliest fill is the NEXT bar
        wait += 1                          # armed and waiting for the retest
        hit = low[t] <= L if side > 0 else high[t] >= L
        if hit:
            in_pos, armed = True, False
            pos[t], entry_px[t] = side, L
            n_filled += 1
        elif wait >= max_wait:
            armed, resolved = False, True
            if fallback:
                in_pos = True
                pos[t], entry_px[t] = side, close[t]
                n_fallback += 1
            else:
                n_skipped += 1
    return RetestFill(pos, entry_px, n_armed, n_filled, n_fallback, n_skipped)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_retest.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/mt5_trader/backtest/retest.py tests/test_retest.py
git commit -m "feat(backtest): retest-entry fill simulator

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `donchian_retest` strategy (registered)

Wrap the simulator as a registered strategy so `engine.run`, `validate_candidate`, and the portfolio net builder consume it unchanged. `signal()` returns the transformed (delayed-entry) position series.

**Files:**
- Modify: `src/mt5_trader/strategies/momentum_breakout.py` (add the class; it is already imported by `strategies/__init__.py`)
- Test: `tests/test_strategies.py` (add focused tests; the registry-wide tests pick the new strategy up automatically)

**Interfaces:**
- Consumes: `DonchianBreakout.levels` (Task 1), `simulate_retest_entry` (Task 2).
- Produces: `DonchianRetest` registered as `"donchian_retest"`, fields `entry_n=240, exit_n=60, ema_n=1440, max_wait=24, fallback=False`. `signal(bars) -> np.ndarray` of the transformed position series.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_strategies.py`:

```python
def test_donchian_retest_registered_and_delays_entry():
    from mt5_trader.strategies.momentum_breakout import DonchianBreakout
    bars = make_bars(n=4000, seed=3)
    assert "donchian_retest" in REGISTRY
    base = DonchianBreakout().signal(bars)
    retest = REGISTRY["donchian_retest"]().signal(bars)
    # exits unchanged: a held retest bar always coincides with a held baseline bar
    held = retest != 0
    assert held.any()                                   # non-vacuous
    assert np.array_equal(retest[held], base[held])
    # entry is delayed: the retest is in the market on fewer (or equal) bars
    assert (retest != 0).sum() <= (base != 0).sum()


def test_donchian_retest_fallback_enters_at_least_as_often():
    bars = make_bars(n=4000, seed=3)
    skip = REGISTRY["donchian_retest"](fallback=False).signal(bars)
    fb = REGISTRY["donchian_retest"](fallback=True).signal(bars)
    assert (fb != 0).sum() >= (skip != 0).sum()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_strategies.py::test_donchian_retest_registered_and_delays_entry -q`
Expected: FAIL with `KeyError: 'donchian_retest'`.

- [ ] **Step 3: Implement `DonchianRetest`**

Append to `src/mt5_trader/strategies/momentum_breakout.py` (and add the import at the top of the file):

```python
from ..backtest.retest import simulate_retest_entry
```

```python
@register
@dataclass
class DonchianRetest:
    """Donchian breakout that waits for a pullback (retest) to the broken channel
    edge before entering, instead of buying the breakout at market. See
    backtest/retest.py. signal() returns the delayed-entry position series; the
    precise entry price (L) is measured by scripts/research_donchian_retest.py."""
    entry_n: int = 240
    exit_n: int = 60
    ema_n: int = 1440
    max_wait: int = 24      # M5 bars to wait for the retest (~2h)
    fallback: bool = False  # True = market entry if no retest by max_wait; False = skip

    name = "donchian_retest"

    def signal(self, bars: pl.DataFrame) -> np.ndarray:
        base = DonchianBreakout(self.entry_n, self.exit_n, self.ema_n)
        raw = base.signal(bars)
        lv = base.levels(bars)
        fill = simulate_retest_entry(
            raw, lv["entry_hi"], lv["entry_lo"],
            bars["high"].to_numpy(), bars["low"].to_numpy(),
            bars["close"].to_numpy(), self.max_wait, self.fallback)
        return fill.pos
```

- [ ] **Step 4: Run the new tests AND the registry-wide guards (valid signal + no-lookahead)**

Run: `uv run pytest tests/test_strategies.py -q`
Expected: PASS — including `test_all_strategies_produce_valid_signals` and `test_no_lookahead`, which now also exercise `donchian_retest`.

- [ ] **Step 5: Run the full suite to confirm nothing regressed**

Run: `uv run pytest -q`
Expected: PASS (no regressions; a possible import-cycle between `strategies` and `backtest` surfaces here — if so, the import in Step 3 must be inside `signal()` rather than at module top; move it and re-run).

- [ ] **Step 6: Commit**

```bash
git add src/mt5_trader/strategies/momentum_breakout.py tests/test_strategies.py
git commit -m "feat(strategy): donchian_retest pullback-entry variant

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Stage-1 research — standalone entry quality

Standalone Donchian@M5: baseline vs retest across the full sweep. Reports trade counts, the precise **entry-improvement (bp)** at the anchor `L`, and conservative (fill-at-close) PnL via the existing engine. This is the diagnostic that exposes the inversion risk (skipped trades).

**Files:**
- Create: `scripts/research_donchian_retest.py`

**Interfaces:**
- Consumes: `simulate_retest_entry`, `DonchianBreakout`, `DonchianRetest`, `engine.run`, `pipeline.data.{build_bars, bars_per_year}`.

- [ ] **Step 1: Write the research script**

Create `scripts/research_donchian_retest.py`:

```python
#!/usr/bin/env python
"""STAGE 1 (research, not a live component): does waiting for a retest of the
broken Donchian channel edge give a better entry on XAUUSD@M5 — and is the trade
we skip (the breakout that never pulls back) worth more than the better fill?

Baseline = current donchian (exit_n=110, market entry at the breakout).
Retest   = donchian_retest over max_wait x policy.

Reports per config: trade/fill/skip counts, mean entry-improvement at the anchor
L vs the baseline breakout close (bp, +ve = retest entered better), and the
engine PnL. CAVEATS: engine PnL prices the fill at the fill-bar CLOSE (conservative
proxy); the entry-improvement column is the precise L-vs-breakout edge. Optimistic
touch-fill; ~5wk sample -> DIRECTIONAL evidence, not a deploy gate.

    uv run scripts/research_donchian_retest.py
"""
from __future__ import annotations

import numpy as np

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.backtest.retest import simulate_retest_entry
from mt5_trader.pipeline.data import bars_per_year, build_bars
from mt5_trader.strategies.momentum_breakout import DonchianBreakout, DonchianRetest

SYMBOL, EVERY, EXIT_N = "XAUUSD", "5m", 110
MAX_WAITS = (12, 24, 48)          # M5 bars ~ 1h / 2h / 4h
POLICIES = (("skip", False), ("market_fallback", True))


def entry_improvement_bp(raw, entry_hi, entry_lo, entry_px, close) -> list[float]:
    """Per filled episode: side * (baseline breakout close - L) / breakout close,
    in bp. +ve means the retest entered at a better price than the market breakout."""
    out = []
    n = len(raw)
    for t in range(1, n):
        if raw[t] != 0 and raw[t - 1] == 0:           # breakout bar of a new episode
            side, ref = raw[t], close[t]
            for j in range(t, n):                      # find this episode's fill
                if raw[j] == 0:
                    break
                if not np.isnan(entry_px[j]):
                    out.append(side * (ref - entry_px[j]) / ref * 1e4)
                    break
    return out


def main():
    bars = build_bars(SYMBOL, EVERY)
    byr = bars_per_year(EVERY)
    cfg = BTConfig(bars_per_year=byr)
    close = bars["close"].to_numpy()

    base = DonchianBreakout(exit_n=EXIT_N)
    raw = base.signal(bars)
    lv = base.levels(bars)
    bm = run(bars, raw, cfg).metrics
    n_breakouts = int(((raw != 0) & (np.concatenate([[0], raw[:-1]]) == 0)).sum())
    print(f"baseline donchian@M5 exit_n={EXIT_N}: ret {bm['total_return']:+.4f}  "
          f"sharpe {bm['sharpe']:.2f}  maxDD {bm['max_drawdown']:.4f}  "
          f"trades {n_breakouts}")
    print(f"\n{'config':<26}{'fill':>5}{'fb':>4}{'skip':>5}"
          f"{'entry+bp':>10}{'ret':>9}{'sharpe':>8}{'maxDD':>9}")

    for mw in MAX_WAITS:
        for label, fb in POLICIES:
            fill = simulate_retest_entry(
                raw, lv["entry_hi"], lv["entry_lo"],
                bars["high"].to_numpy(), bars["low"].to_numpy(), close, mw, fb)
            imp = entry_improvement_bp(raw, lv["entry_hi"], lv["entry_lo"],
                                       fill.entry_px, close)
            m = run(bars, fill.pos, cfg).metrics
            mean_imp = float(np.mean(imp)) if imp else 0.0
            print(f"{f'mw{mw}/{label}':<26}{fill.n_filled:>5}{fill.n_fallback:>4}"
                  f"{fill.n_skipped:>5}{mean_imp:>10.1f}{m['total_return']:>9.4f}"
                  f"{m['sharpe']:>8.2f}{m['max_drawdown']:>9.4f}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it and read the output**

Run: `uv run scripts/research_donchian_retest.py`
Expected: a baseline line then 6 config rows (3 `max_wait` × 2 policy). Sanity checks: `fill + fb + skip` per row equals the baseline `trades` count; `entry+bp` is positive for the `skip` rows (the retest, when it fills, enters at or better than the breakout); `market_fallback` rows have `skip` ≈ 0 and a lower `entry+bp` (fallbacks dilute it).

- [ ] **Step 3: Commit**

```bash
git add scripts/research_donchian_retest.py
git commit -m "feat(research): stage-1 donchian retest entry-quality report

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Stage-2 research — net portfolio + validate gates (the decision)

Swap `donchian_retest` (best Stage-1 config) into the all-6-book net portfolio and compare net metrics against the current net. Then run the standard `validate_candidate` gate report. This produces the go/no-go evidence; **no live change is made here regardless of outcome.**

**Files:**
- Create: `scripts/research_donchian_retest_net.py`

**Interfaces:**
- Consumes: `pipeline.data.{build_bars, bars_per_year}`, `features.technicals.realized_vol`, `strategies.REGISTRY`, `strategies.meanrev_pairs.spread_bars`, `pipeline.validate.validate_candidate`, `config/portfolio.yaml`.

- [ ] **Step 1: Write the net-comparison + validate script**

Create `scripts/research_donchian_retest_net.py`. The net builder mirrors the established `scripts/research_portfolio_overlay.py` pattern (vol-targeted per-book frac forward-filled onto a 1m grid, summed, executed one bar later at real spread):

```python
#!/usr/bin/env python
"""STAGE 2 (research, not a live component): does swapping donchian ->
donchian_retest improve the NET 6-book portfolio? Standalone book gains routinely
wash out in the netted book (the lesson behind validating overlays on the net),
so THIS is the gate that matters. Also prints the standard 6-gate
validate_candidate report for donchian_retest.

Set BEST_MAX_WAIT / BEST_FALLBACK from the Stage-1 winner before running.
CAVEATS: net builder is an APPROXIMATE relative comparison (close-to-close, 1m
grid), and ~5wk sample -> directional, not a deploy gate (DSR expected to fail).

    uv run scripts/research_donchian_retest_net.py
"""
from __future__ import annotations

import numpy as np
import polars as pl
import yaml

from mt5_trader.features.technicals import realized_vol
from mt5_trader.pipeline.data import bars_per_year, build_bars
from mt5_trader.pipeline.validate import validate_candidate
from mt5_trader.strategies import REGISTRY
from mt5_trader.strategies.meanrev_pairs import spread_bars

SYMBOL = "XAUUSD"
TF_EVERY = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}
SLIP = 0.5e-4
TARGET_VOL, MAX_LEV, VOL_WIN = 0.30, 2.0, 240
BEST_MAX_WAIT, BEST_FALLBACK = 24, False   # <-- set from Stage 1
_cache: dict[str, pl.DataFrame] = {}


def gb(every: str) -> pl.DataFrame:
    if every not in _cache:
        _cache[every] = build_bars(SYMBOL, every).sort("ts")
    return _cache[every]


def book_frac_1m(book: dict, master: pl.DataFrame) -> np.ndarray:
    name, tf = book["strategy"], book.get("timeframe", "M1")
    every, w = TF_EVERY[tf], float(book.get("weight", 1.0))
    params = book.get("params", {}) or {}
    bars = (spread_bars(gb(every), build_bars("XAGUSD", every).sort("ts"), 1.0)
            if name == "ratio_mr" else gb(every))
    sig = np.asarray(REGISTRY[name](**params).signal(bars), dtype=float)
    vol = bars.select(realized_vol(VOL_WIN, bars_per_year(every))).to_series().to_numpy()
    lev = np.minimum(TARGET_VOL / np.maximum(vol, 1e-4), MAX_LEV)
    frac = np.nan_to_num(sig * lev) * w
    sdf = pl.DataFrame({"ts": bars["ts"], "s": frac}).sort("ts")
    joined = master.select("ts").join_asof(sdf, on="ts", strategy="backward")
    return np.nan_to_num(joined["s"].to_numpy())


def net_metrics(books: list[dict], master: pl.DataFrame, byr: float) -> dict:
    expo = sum(book_frac_1m(b, master) for b in books)
    close = master["close"].to_numpy()
    ret = np.concatenate([[0.0], close[1:] / close[:-1] - 1.0])
    sp = master["spread_mean"].fill_null(0).to_numpy() / np.where(close == 0, 1.0, close)
    pos = np.concatenate([[0.0], expo[:-1]])
    turn = np.abs(np.diff(pos, prepend=0.0))
    pnl = pos * ret - turn * (sp / 2 + SLIP)
    eq = np.cumprod(1.0 + pnl)
    dd = float((eq / np.maximum.accumulate(eq) - 1.0).min())
    sharpe = float(pnl.mean() / pnl.std() * np.sqrt(byr)) if pnl.std() > 0 else 0.0
    return {"ret": float(eq[-1] - 1.0), "sharpe": sharpe, "maxDD": dd,
            "turn": float(turn.sum())}


def main():
    books = yaml.safe_load(open("config/portfolio.yaml"))["books"]
    master = gb("1m")
    byr = bars_per_year("1m")

    base = net_metrics(books, master, byr)
    swapped = []
    for b in books:
        if b["strategy"] == "donchian":
            b = {**b, "strategy": "donchian_retest",
                 "params": {**(b.get("params") or {}),
                            "max_wait": BEST_MAX_WAIT, "fallback": BEST_FALLBACK}}
        swapped.append(b)
    retest = net_metrics(swapped, master, byr)

    print(f"net (donchian baseline): ret {base['ret']:+.4f}  sharpe {base['sharpe']:.2f}  "
          f"maxDD {base['maxDD']:.4f}  turn {base['turn']:.0f}")
    print(f"net (donchian_retest)  : ret {retest['ret']:+.4f}  sharpe {retest['sharpe']:.2f}  "
          f"maxDD {retest['maxDD']:.4f}  turn {retest['turn']:.0f}")

    print("\n6-gate validate_candidate(donchian_retest):")
    v = validate_candidate("donchian_retest", gb("5m"), every="5m",
                           params={"exit_n": 110, "max_wait": BEST_MAX_WAIT,
                                   "fallback": BEST_FALLBACK},
                           n_trials=6)  # 3 max_wait x 2 policy searched
    for gate, d in v["gates"].items():
        print(f"  {gate:<16} {'PASS' if d['pass'] else 'FAIL'}  {d}")
    print(f"  OVERALL: {'PASS' if v['overall'] else 'FAIL'}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Set the best config and run**

Edit `BEST_MAX_WAIT` / `BEST_FALLBACK` to the Stage-1 winner (the config with the best net of entry-improvement and PnL/skip trade-off), then:

Run: `uv run scripts/research_donchian_retest_net.py`
Expected: two net lines (baseline vs retest) and the 6-gate report. The decision: does `donchian_retest` raise net Sharpe / cut net maxDD without gutting return? (deflated_sharpe is expected to FAIL on the 5wk sample — per the spec that gate is not a blocker here.)

- [ ] **Step 3: Commit**

```bash
git add scripts/research_donchian_retest_net.py
git commit -m "feat(research): stage-2 donchian retest net-portfolio + gates

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 4: Write the verdict**

Append a short findings block (numbers + recommendation) to `docs/superpowers/specs/2026-06-25-donchian-retest-entry-design.md` under a new `## Findings` heading: the Stage-1 table summary, the Stage-2 net comparison, and a clear **build-live-machinery / stop-here** call. Commit:

```bash
git add docs/superpowers/specs/2026-06-25-donchian-retest-entry-design.md
git commit -m "docs(donchian-retest): stage-1/2 findings + verdict

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Anchor `L` = broken edge → Task 1 (`levels`) + Task 2 (capture at breakout bar). ✓
- Arm on breakout, fill on touch priced at `L`, causal (no breakout-bar fill) → Task 2 + tests. ✓
- Exits unchanged → invariant test in Tasks 2 & 3. ✓
- Skip if exit before fill; timeout skip vs market_fallback → Task 2 tests. ✓
- Sweep `max_wait`×policy → Tasks 4 & 5. ✓
- Stage-1 counts + entry-improvement bp → Task 4. ✓
- Stage-2 net + the netting lesson → Task 5. ✓
- Caveats (optimistic fill, 5wk/DSR) → stated in module + both script docstrings. ✓
- No live changes; build-only-if-survives → Task 5 verdict step; live work out of scope. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code; `BEST_MAX_WAIT` is a documented post-Stage-1 value, not a placeholder. ✓

**Type consistency:** `levels()` returns the dict consumed in Task 2/3; `simulate_retest_entry` signature and `RetestFill` fields are used identically in Tasks 2/3/4; `validate_candidate(...)` call matches the `validate.py` signature (`name, bars, every=, params=, n_trials=`). ✓
