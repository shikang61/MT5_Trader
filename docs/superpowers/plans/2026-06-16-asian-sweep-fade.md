# AsianSweepFade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a backtest-only `AsianSweepFade` strategy — fade the reclaim of the Asian-session (00:00–06:00 UTC) high/low during the London session — registered and tested, NOT in `portfolio.yaml`.

**Architecture:** A standalone strategy class mirroring `sweep_fade.py`, conforming to the `Strategy` protocol (`.signal(bars) -> np.ndarray`). Same sweep-and-reclaim state machine, but the level source is today's Asian range (computed causally from the day's early bars) instead of the prior day's high/low, with a longer 60-bar hold. Registered via `strategies/__init__.py` so the existing registry tests (validity + no-lookahead) cover it automatically.

**Tech Stack:** Python 3.11, numpy, polars; pytest. Data for the sanity check: `data/real` (competition depth dump).

---

## Task 1: AsianSweepFade strategy + unit tests

**Files:**
- Create: `src/mt5_trader/strategies/asian_sweep.py`
- Test: `tests/test_asian_sweep.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_asian_sweep.py
from datetime import datetime, timezone

import numpy as np
import polars as pl

from mt5_trader.strategies.asian_sweep import AsianSweepFade


def _bars(rows):
    # rows: (datetime, high, low, close)
    return pl.DataFrame({
        "ts": [r[0] for r in rows],
        "high": [r[1] for r in rows],
        "low": [r[2] for r in rows],
        "close": [r[3] for r in rows],
    })


D = datetime(2026, 5, 12, tzinfo=timezone.utc)
def at(h, m=0):
    return D.replace(hour=h, minute=m)


# small params so test fixtures stay tiny
CFG = dict(session_end_hour=6, quiet_bars=2, confirm_bars=1, hold_bars=3)


def test_no_signal_before_session_end():
    # only Asian-window bars (hour < 6) -> never trade
    bars = _bars([(at(0), 100.0, 99.0, 99.5),
                  (at(1), 100.0, 99.2, 99.8),
                  (at(5), 99.9, 99.1, 99.5)])
    sig = AsianSweepFade(**CFG).signal(bars)
    assert np.all(sig == 0.0)


def test_short_on_high_sweep_reclaim():
    # Asian high=100 (set 00:00); after 06:00 sweep above then close back inside
    bars = _bars([
        (at(0), 100.0, 99.0, 99.5),     # 0  Asian window -> ah=100, al=99
        (at(6, 0), 100.5, 100.0, 100.4),  # 1  high>100 -> sweep up; pend at +1
        (at(6, 1), 100.2, 99.6, 99.8),    # 2  pend bar: close 99.8 < 100 -> SHORT, hold 3
        (at(6, 2), 99.9, 99.5, 99.7),     # 3  short
        (at(6, 3), 99.9, 99.5, 99.7),     # 4  short
        (at(6, 4), 99.9, 99.5, 99.7),     # 5  flat again
    ])
    sig = AsianSweepFade(**CFG).signal(bars)
    assert sig[2] == -1.0 and sig[3] == -1.0 and sig[4] == -1.0
    assert sig[5] == 0.0 and sig[0] == 0.0


def test_long_on_low_sweep_reclaim():
    bars = _bars([
        (at(0), 100.0, 99.0, 99.5),     # 0  ah=100, al=99
        (at(6, 0), 99.0, 98.5, 98.6),     # 1  low<99 -> sweep down; pend at +1
        (at(6, 1), 99.4, 98.8, 99.2),     # 2  pend bar: close 99.2 > 99 -> LONG, hold 3
        (at(6, 2), 99.5, 99.1, 99.3),     # 3  long
        (at(6, 3), 99.5, 99.1, 99.3),     # 4  long
        (at(6, 4), 99.5, 99.1, 99.3),     # 5  flat
    ])
    sig = AsianSweepFade(**CFG).signal(bars)
    assert sig[2] == 1.0 and sig[3] == 1.0 and sig[4] == 1.0
    assert sig[5] == 0.0


def test_no_entry_on_held_breakout():
    # sweep above but close STILL above the level at the confirm bar -> no fade
    bars = _bars([
        (at(0), 100.0, 99.0, 99.5),     # ah=100
        (at(6, 0), 100.5, 100.0, 100.4),  # sweep up
        (at(6, 1), 100.8, 100.2, 100.6),  # pend bar: close 100.6 > 100 -> held, no short
        (at(6, 2), 100.7, 100.3, 100.5),
    ])
    sig = AsianSweepFade(**CFG).signal(bars)
    assert np.all(sig == 0.0)


def test_asian_level_uses_window_only_not_later_high():
    # a higher high AFTER 06:00 must not raise the Asian level (else no sweep)
    bars = _bars([
        (at(0), 100.0, 99.0, 99.5),     # ah=100 from the window
        (at(6, 0), 100.5, 100.0, 100.4),  # high 100.5 > 100 -> sweep (would fail if ah=100.5)
        (at(6, 1), 100.2, 99.6, 99.8),    # reclaim -> short
        (at(6, 2), 99.9, 99.5, 99.7),
        (at(6, 3), 99.9, 99.5, 99.7),
    ])
    sig = AsianSweepFade(**CFG).signal(bars)
    assert sig[2] == -1.0


def test_no_lookahead_prefix_stable():
    # appending future bars must not change earlier signals
    bars = _bars([
        (at(0), 100.0, 99.0, 99.5),
        (at(6, 0), 100.5, 100.0, 100.4),
        (at(6, 1), 100.2, 99.6, 99.8),
        (at(6, 2), 99.9, 99.5, 99.7),
        (at(6, 3), 99.9, 99.5, 99.7),
        (at(6, 4), 99.9, 99.5, 99.7),
    ])
    full = AsianSweepFade(**CFG).signal(bars)
    trunc = AsianSweepFade(**CFG).signal(bars[:4])
    assert np.allclose(full[:4], trunc)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_asian_sweep.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mt5_trader.strategies.asian_sweep'`

- [ ] **Step 3: Write the implementation**

```python
# src/mt5_trader/strategies/asian_sweep.py
"""Asian-session high/low sweep fade.

Research (scripts/research_sweep.py + null test, May 11-Jun 10 2026 XAUUSD):
fading the reclaim of the Asian-session (00:00-06:00 UTC) range during the
London session made +10.7bp/event at a 60-min hold (q60,c5), a plateau across
quiet/confirm; null test p=0.005 (sweep-specific, not session drift). Thin
(n~25, one month, edge only at 60m) -> backtest-only, NOT in portfolio.yaml.

Structurally SweepFade with a different level source: today's Asian range
instead of the prior day's high/low, and a longer (60-bar) hold. M1 bars.
Defaults assume 1-minute bars.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from .base import register


@register
@dataclass
class AsianSweepFade:
    session_end_hour: int = 6   # Asian window is [0, session_end_hour) UTC
    quiet_bars: int = 60
    confirm_bars: int = 5
    hold_bars: int = 60

    name = "asian_sweep"

    def signal(self, bars: pl.DataFrame) -> np.ndarray:
        df = bars.with_columns(date=pl.col("ts").dt.date(),
                               hour=pl.col("ts").dt.hour())
        # Asian-session H/L per day from the [0, session_end_hour) window only.
        # Those bars always precede the day's trade bars (hour >= end), so this
        # is causal: a trade bar never depends on a future bar.
        asia = (df.filter(pl.col("hour") < self.session_end_hour)
                .group_by("date")
                .agg(ah=pl.col("high").max(), al=pl.col("low").min()))
        df = df.join(asia, on="date", how="left")
        high, low, close = (df["high"].to_numpy(), df["low"].to_numpy(),
                            df["close"].to_numpy())
        ah, al = df["ah"].to_numpy(), df["al"].to_numpy()
        hour = df["hour"].to_numpy()
        dates = df["date"].to_numpy()

        pos = np.zeros(len(close))
        day = None
        big = 10**9
        last_hi = last_lo = -big        # last bar beyond the level
        pend_hi = pend_lo = -1          # bar at which to classify the sweep
        short_left = long_left = 0
        for t in range(len(close)):
            if dates[t] != day:
                day = dates[t]
                last_hi = last_lo = -big
                pend_hi = pend_lo = -1
            active = hour[t] >= self.session_end_hour and not np.isnan(ah[t])
            if active:
                if high[t] > ah[t]:
                    if t - last_hi > self.quiet_bars and pend_hi < 0:
                        pend_hi = t + self.confirm_bars
                    last_hi = t
                if low[t] < al[t]:
                    if t - last_lo > self.quiet_bars and pend_lo < 0:
                        pend_lo = t + self.confirm_bars
                    last_lo = t
                if t == pend_hi:
                    if close[t] < ah[t]:
                        short_left = self.hold_bars
                    pend_hi = -1
                if t == pend_lo:
                    if close[t] > al[t]:
                        long_left = self.hold_bars
                    pend_lo = -1
            if short_left > 0:
                pos[t] -= 1.0
                short_left -= 1
            if long_left > 0:
                pos[t] += 1.0
                long_left -= 1
        return pos
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_asian_sweep.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/mt5_trader/strategies/asian_sweep.py tests/test_asian_sweep.py
git commit -m "feat(strategy): AsianSweepFade — Asian-session H/L sweep fade"
```

---

## Task 2: Register in the strategy registry

**Files:**
- Modify: `src/mt5_trader/strategies/__init__.py:2`

- [ ] **Step 1: Add the import that populates the registry**

Change line 2 of `src/mt5_trader/strategies/__init__.py` from:

```python
from . import fast_meanrev, momentum_breakout, orderflow_imbalance, meanrev_pairs, regime_switch, session_vol, sweep_fade, vwap_trend  # noqa: F401 — populate registry
```

to (add `asian_sweep`):

```python
from . import asian_sweep, fast_meanrev, momentum_breakout, orderflow_imbalance, meanrev_pairs, regime_switch, session_vol, sweep_fade, vwap_trend  # noqa: F401 — populate registry
```

- [ ] **Step 2: Verify it is registered**

Run: `uv run python -c "from mt5_trader.strategies import REGISTRY; print('asian_sweep' in REGISTRY)"`
Expected: `True`

- [ ] **Step 3: Run the registry tests (validity + no-lookahead cover the new book)**

Run: `uv run pytest tests/test_strategies.py -q`
Expected: PASS — `test_all_strategies_produce_valid_signals` and `test_no_lookahead` now include `asian_sweep` (finite, in [-1,1], prefix-stable).

- [ ] **Step 4: Run the full suite (nothing else broke)**

Run: `uv run pytest -q`
Expected: PASS (all green; `validate_books`/`portfolio.yaml` untouched, so no live book changed).

- [ ] **Step 5: Commit**

```bash
git add src/mt5_trader/strategies/__init__.py
git commit -m "feat(strategy): register asian_sweep in the backtest registry"
```

---

## Task 3: Backtest sanity check on the real dump

**Files:**
- None (analysis); optionally append a Results note to the spec.

- [ ] **Step 1: Run the backtest on the competition dump**

Run: `uv run scripts/run_backtest.py --strategy asian_sweep --symbol XAUUSD --data data/real --every 1m`
Expected: prints metrics (total_return, sharpe, max_drawdown, turnover, hit_rate) and writes `reports/asian_sweep_XAUUSD.png`. No exceptions.

- [ ] **Step 2: Sanity-check the numbers**

Confirm the book trades (turnover > 0) and the sign/scale is consistent with the
event study (a positive, modest edge — the 60-bar hold means low turnover). This
is a smoke check, not a gate; the strategy is backtest-only pending more data.
If `total_return` is sharply negative or turnover is 0, stop and investigate
(likely a level/hour bug) before claiming success.

- [ ] **Step 3 (optional): Record the result**

Append a short "Backtest result" line (return/sharpe/turnover/hit) to
`docs/superpowers/specs/2026-06-16-asian-sweep-fade-design.md`, then:

```bash
git add docs/superpowers/specs/2026-06-16-asian-sweep-fade-design.md
git commit -m "docs(asian_sweep): record backtest sanity result"
```

---

## Self-review notes

- **Spec coverage:** component + `@register` (Task 1–2), signal logic with Asian
  window / trade-after-06:00 / sweep-reclaim / day reset (Task 1 tests), no-lookahead
  (Task 1 test + Task 2 registry test), params from research (impl defaults), backtest
  sanity on `data/real` (Task 3), NOT in `portfolio.yaml` (never edited). All covered.
- **No-lookahead is the subtle one:** Asian H/L is computed per-date from that date's
  `[0, session_end_hour)` bars, which always precede the day's trade bars. Truncating
  the frame cannot change a past trade signal (verified by `test_no_lookahead_prefix_stable`
  and the registry-wide `test_no_lookahead`). Do not replace the per-day groupby with a
  rolling/centered window — that would leak.
- **Defaults vs test params:** impl defaults are the research values (`quiet_bars=60,
  confirm_bars=5, hold_bars=60`); tests pass small overrides (`2/1/3`) so fixtures stay
  tiny. Same field names in both — consistent.
