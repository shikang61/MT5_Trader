# VelocityBreakout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a backtest-only `VelocityBreakout` strategy — a US-session opening-range breakout gated by break velocity (Kaufman ER) — registered and unit-tested, NOT in `portfolio.yaml`.

**Architecture:** One `@register` dataclass in its own file conforming to the `Strategy` protocol (`signal(bars) -> np.ndarray` in [-1, 1], decided on bar close, engine shifts execution one bar). Per-UTC-day, per-arm-time state machine: accumulate a pre-range, take the first close beyond it within a scan window only if the break is clean (ER ≥ threshold), hold a fixed number of bars, then flat.

**Tech Stack:** Python 3.11, polars, numpy, pytest, uv. Reuses `features.regime.efficiency_ratio` and `features.technicals.atr`.

## Global Constraints

- Signal contract: `signal(bars) -> np.ndarray`, length == len(bars), values in [-1, 1]; decided on bar t's close; **no `.shift(-k)` / no future data** (engine shifts execution one bar).
- `bars` columns available: `ts` (UTC datetime), `open`, `high`, `low`, `close`, `spread_mean`, `n_ticks` (`ofi` when built by `run_backtest`).
- One strategy per file (repo pattern). New strategy is **registered + tested but NOT added to `portfolio.yaml`** (no live sizing).
- Gold-only. M1 timeframe.
- Defaults (from the design, `docs/superpowers/specs/2026-06-18-velocity-gated-session-breakout-design.md`): `arm_times=["12:30","14:00"]`, `pre_min=30`, `arm_min=30`, `er_n=10`, `er_min=0.5`, `push_min=0.0`, `hold_bars=120`.
- TDD, frequent commits, DRY, YAGNI.

---

### Task 1: Scaffold + register the strategy

**Files:**
- Create: `src/mt5_trader/strategies/velocity_breakout.py`
- Modify: `src/mt5_trader/strategies/__init__.py` (add import so `@register` runs)
- Test: `tests/test_strategies.py` (existing — runs automatically on registration)

**Interfaces:**
- Produces: `VelocityBreakout` dataclass, `name = "velocity_breakout"`, `signal(bars: pl.DataFrame) -> np.ndarray`. Registered into `REGISTRY` via the `@register` decorator on import.

- [ ] **Step 1: Write the file with a zeros stub**

`src/mt5_trader/strategies/velocity_breakout.py`:
```python
"""US-session opening-range breakout, gated by break velocity (Kaufman ER).

Arm at the US data-drop times (12:30, 14:00 UTC). For each arm, record the
pre-range (closes in [arm - pre_min, arm)); take the FIRST close beyond it within
[arm, arm + arm_min) only if the break is clean — efficiency ratio over er_n bars
>= er_min (a churn break fades, study A/diagnostic 2026-06-18). Hold hold_bars,
then flat. push_min (break-bar |ret|/ATR) is an optional secondary gate, off by
default. Backtest-only; not in portfolio.yaml. See the design doc for evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import polars as pl

from .base import register
from ..features.regime import efficiency_ratio
from ..features.technicals import atr


def _to_minutes(hhmm: str) -> int:
    """'12:30' -> 750 (minute of the UTC day)."""
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


@register
@dataclass
class VelocityBreakout:
    arm_times: list[str] = field(default_factory=lambda: ["12:30", "14:00"])
    pre_min: int = 30
    arm_min: int = 30
    er_n: int = 10
    er_min: float = 0.5
    push_min: float = 0.0
    hold_bars: int = 120

    name = "velocity_breakout"

    def signal(self, bars: pl.DataFrame) -> np.ndarray:
        return np.zeros(len(bars))
```

- [ ] **Step 2: Register it**

In `src/mt5_trader/strategies/__init__.py`, add `velocity_breakout` to the existing `from . import ...` block (match the existing import style for the other strategy modules).

- [ ] **Step 3: Run the registry tests to verify registration**

Run: `uv run pytest tests/test_strategies.py -q`
Expected: PASS. `test_all_strategies_produce_valid_signals` and `test_no_lookahead` now include `velocity_breakout`; the zeros stub is a valid, causal signal.

- [ ] **Step 4: Commit**

```bash
git add src/mt5_trader/strategies/velocity_breakout.py src/mt5_trader/strategies/__init__.py
git commit -m "feat(strategy): scaffold velocity_breakout (zeros stub, registered)"
```

---

### Task 2: Implement the signal (pre-range, velocity-gated first break, hold)

**Files:**
- Modify: `src/mt5_trader/strategies/velocity_breakout.py` (replace the stub `signal`)
- Test: `tests/test_velocity_breakout.py` (create)

**Interfaces:**
- Consumes: `efficiency_ratio(n) -> pl.Expr` (net/path over `close`, unsigned, in [0, 1]); `atr(n) -> pl.Expr` (needs high/low/close).
- Produces: the real `VelocityBreakout.signal` behaviour relied on by the Task 3 backtest.

- [ ] **Step 1: Write the failing tests**

`tests/test_velocity_breakout.py`:
```python
from datetime import datetime, timedelta, timezone

import numpy as np
import polars as pl

from mt5_trader.strategies.velocity_breakout import VelocityBreakout


def make_day(date: str, closes, start_hour=12, start_min=0) -> pl.DataFrame:
    """1-min bars for one UTC day. OHLC all = close so ATR is well-defined and
    only close-based logic is exercised."""
    t0 = datetime.fromisoformat(f"{date} {start_hour:02d}:{start_min:02d}").replace(
        tzinfo=timezone.utc)
    ts = [t0 + timedelta(minutes=i) for i in range(len(closes))]
    c = pl.Series("close", [float(x) for x in closes])
    return pl.DataFrame({
        "ts": ts, "open": c, "high": c, "low": c, "close": c,
        "spread_mean": [0.0] * len(closes), "n_ticks": [1] * len(closes),
    })


# Small test params: single arm 12:30, er_n=5, hold 10 bars.
def strat():
    return VelocityBreakout(arm_times=["12:30"], pre_min=30, arm_min=30,
                            er_n=5, er_min=0.5, hold_bars=10)


def test_clean_break_enters_long_and_holds():
    # 30 flat pre-range bars (12:00-12:30) -> pre_hi=pre_lo=100; clean up-break.
    closes = [100.0] * 30 + [100.5] * 15
    pos = strat().signal(make_day("2026-01-05", closes))
    assert np.all(pos[:30] == 0.0)          # pre-range + before break
    assert np.all(pos[30:40] == 1.0)        # entry bar + 9 = hold_bars(10) bars long
    assert np.all(pos[40:] == 0.0)          # flat after the hold


def test_clean_break_down_enters_short():
    closes = [100.0] * 30 + [99.5] * 15
    pos = strat().signal(make_day("2026-01-05", closes))
    assert np.all(pos[30:40] == -1.0)
    assert np.all(pos[40:] == 0.0)


def test_churn_break_rejected():
    # pre_hi=101, pre_lo=99; the first close >101 arrives via a choppy path -> low ER.
    pre = [101.0, 99.0] + [100.0] * 28           # 30 bars, sets the range
    scan = [100.0, 101.0, 100.0, 101.0, 100.0, 101.2] + [100.0] * 9
    pos = strat().signal(make_day("2026-01-05", pre + scan))
    assert np.all(pos == 0.0)                     # churn break fails the ER gate


def test_one_entry_per_arm_per_day():
    # clean break, hold ends at idx 40, price dips then breaks again -> no re-entry.
    closes = [100.0] * 30 + [100.5] * 5 + [100.0] * 5 + [101.0] * 10
    pos = strat().signal(make_day("2026-01-05", closes))
    assert np.all(pos[30:40] == 1.0)
    assert np.all(pos[40:] == 0.0)               # arm consumed for the day


def test_no_entry_after_scan_window():
    # first break only at 13:00 (idx 60), past [12:30, 13:00) scan window.
    closes = [100.0] * 60 + [101.0] * 10
    pos = strat().signal(make_day("2026-01-05", closes))
    assert np.all(pos == 0.0)


def test_state_resets_across_days():
    day1 = make_day("2026-01-05", [100.0] * 30 + [100.5] * 15)
    day2 = make_day("2026-01-06", [100.0] * 30 + [100.5] * 15)
    pos = strat().signal(pl.concat([day1, day2]))
    assert np.all(pos[30:40] == 1.0)             # day 1 entry
    assert np.all(pos[45 + 30:45 + 40] == 1.0)   # day 2 entry (offset by day1 length 45)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_velocity_breakout.py -q`
Expected: FAIL — the stub returns all zeros, so the long/short-entry assertions fail.

- [ ] **Step 3: Implement the signal**

Replace the stub `signal` in `src/mt5_trader/strategies/velocity_breakout.py` with:
```python
    def signal(self, bars: pl.DataFrame) -> np.ndarray:
        n = len(bars)
        close = bars["close"].to_numpy()
        minute_of_day = (bars["ts"].dt.hour() * 60 + bars["ts"].dt.minute()).to_numpy()
        dates = bars["ts"].dt.date().to_numpy()
        er = bars.select(er=efficiency_ratio(self.er_n))["er"].to_numpy()
        push = bars.select(p=(pl.col("close").diff().abs() / atr(14)))["p"].to_numpy()
        arms = [_to_minutes(a) for a in self.arm_times]

        # per-arm state, reset each UTC day
        pre_hi = {a: None for a in arms}
        pre_lo = {a: None for a in arms}
        entered = {a: False for a in arms}
        apos = {a: 0.0 for a in arms}     # this arm's current held position
        hold = {a: 0 for a in arms}       # bars remaining to hold

        pos = np.zeros(n)
        day = None
        for t in range(n):
            if dates[t] != day:
                day = dates[t]
                for a in arms:
                    pre_hi[a] = pre_lo[a] = None
                    entered[a] = False
                    apos[a] = 0.0
                    hold[a] = 0
            mt = minute_of_day[t]
            total = 0.0
            for a in arms:
                if hold[a] > 0:                      # age the open position
                    hold[a] -= 1
                    if hold[a] == 0:
                        apos[a] = 0.0
                if a - self.pre_min <= mt < a:       # accumulate the pre-range
                    c = close[t]
                    pre_hi[a] = c if pre_hi[a] is None else max(pre_hi[a], c)
                    pre_lo[a] = c if pre_lo[a] is None else min(pre_lo[a], c)
                elif (a <= mt < a + self.arm_min and not entered[a]
                        and pre_hi[a] is not None and apos[a] == 0.0):
                    c = close[t]
                    direction = 1.0 if c > pre_hi[a] else -1.0 if c < pre_lo[a] else 0.0
                    if direction != 0.0:             # first break consumes the arm
                        entered[a] = True
                        clean = (not np.isnan(er[t])) and er[t] >= self.er_min
                        fast = self.push_min <= 0.0 or (
                            not np.isnan(push[t]) and push[t] >= self.push_min)
                        if clean and fast:
                            apos[a] = direction
                            hold[a] = self.hold_bars
                total += apos[a]
            pos[t] = max(-1.0, min(1.0, total))      # net the arms, clip to [-1, 1]
        return pos
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_velocity_breakout.py tests/test_strategies.py -q`
Expected: PASS — strategy-specific tests plus the registry no-lookahead/valid-signal checks on the real signal.

- [ ] **Step 5: Commit**

```bash
git add src/mt5_trader/strategies/velocity_breakout.py tests/test_velocity_breakout.py
git commit -m "feat(strategy): velocity-gated US-session breakout signal + tests"
```

---

### Task 3: Backtest sanity + record result, then commit the spec update

**Files:**
- Modify: `docs/superpowers/specs/2026-06-18-velocity-gated-session-breakout-design.md` (add a "Backtest result (sanity)" section with the real numbers)

**Interfaces:**
- Consumes: the registered `velocity_breakout` strategy and `scripts/run_backtest.py`.

- [ ] **Step 1: Run the default-slippage backtest**

Run: `uv run scripts/run_backtest.py --strategy velocity_breakout --symbol XAUUSD --data data/real --every 1m`
Expected: runs without error and prints metrics (total_return, sharpe, max_drawdown, hit_rate, turnover, in-market fraction). Sanity = sign positive, low in-market % (a once/twice-a-day, 120-bar-hold strategy). **Record the printed numbers.**

- [ ] **Step 2: Run the cost-stress backtest (2bp)**

Run: `uv run scripts/run_backtest.py --strategy velocity_breakout --symbol XAUUSD --data data/real --every 1m --slippage-bps 2`
Expected: runs without error; **record** whether the edge survives 2bp (event-time fills are wider than `spread_mean`). A sign flip to negative here is a finding, not a failure — note it.

- [ ] **Step 3: Write the results into the spec**

Add a "## Backtest result (sanity, 2026-06-18)" section to the design doc with the exact metrics from Steps 1–2 and the one-line caveat that this is one in-sample month (thin, overlapping episodes) and stays backtest-only pending forward comp-data validation. (Mirror the AsianSweepFade spec's result section.)

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-06-18-velocity-gated-session-breakout-design.md
git commit -m "docs: record velocity_breakout backtest sanity result"
```

---

## Self-Review

**Spec coverage:**
- New `VelocityBreakout` standalone class, registered, not in portfolio → Task 1. ✓
- Signal logic (pre-range, first break in scan window, velocity gate, hold, day reset, no-lookahead) → Task 2. ✓
- Parameters with the design defaults → Task 1 dataclass + Global Constraints. ✓
- Velocity gate = single ER primary, push optional/off-by-default, no AND-stacking → Task 2 `clean and fast` with `push_min<=0` short-circuit. ✓
- Unit tests enumerated in the spec (pre-range source, break direction, churn rejected, one-per-day, outside-window, day reset, no-lookahead) → Task 2 tests + registry `test_no_lookahead`. ✓
- Backtest sanity + cost-stress → Task 3. ✓
- Validation-plan gates (week-by-week, cost-stress, wiggle, null-test, forward OOS) → these are **research/promotion gates beyond this plan's scope** (this plan delivers the registered, tested, backtested strategy; the full gate battery is a separate research effort, as the spec states it stays backtest-only). Noted intentionally, not a gap.

**Placeholder scan:** No TBD/TODO; all code shown in full; the only "record the numbers" steps are real backtest-output capture, not fabricated results.

**Type consistency:** `signal(bars) -> np.ndarray` consistent across tasks; `_to_minutes`, `er`, `push`, `pre_hi/pre_lo/entered/apos/hold` names consistent within Task 2; `name="velocity_breakout"` consistent with the backtest `--strategy` arg in Task 3.
