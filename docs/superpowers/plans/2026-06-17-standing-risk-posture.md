# Standing Risk Posture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove competition-round logic from the risk module and replace it with a single standing posture whose kill switch resets daily (auto-resume next UTC day) instead of anchoring to inception and exiting the process.

**Architecture:** `RiskManager` drops the `rounds` list, `round_at()`, and round-name-keyed state; `RoundCfg` becomes `Posture`. Kill state is keyed by UTC day (`roll_day` resets the anchor each day; `kill` is sticky-for-the-day and auto-clears next day, mirroring `passive_limits.daily_halt`). The live runner's kill becomes a daily stand-down (flatten + keep looping) instead of a terminating halt. Manager + runner + config + tests change together (atomic — the runner calls a method the manager removes).

**Tech Stack:** Python, polars, pytest. `RiskManager` is pure (no MetaTrader5 import), tested without the VPS. Run tests with `uv run pytest`.

## Global Constraints

- Standing posture (verbatim): `max_leverage: 2`, `target_vol: 0.30`, `loss_limit: 0.04`, `give_back: 0.0`.
- Kill anchor = daily reset on true UTC `now.date()`; trip → flat + stand down rest of day; auto-resume next UTC day.
- `give_back` measures drawdown from the day's PEAK; `loss_limit` from the day's START. `give_back == 0` disables the trailing kill.
- A kill is a stand-down, NOT a process exit: the live loop runs until stopped externally.
- Do NOT touch: passive book (`passive_limits.py`, `run_passive_paper.py` — `lots`/`daily_loss_cap` stay fixed), sizing math, strategies, blackout, vol_guard, dashboard, `COMPETITION_PLAN.md`.
- `RoundCfg`/`RiskManager` are not in the curated public API — no `__init__.py` export changes needed.

---

### Task 1: Standing daily-reset posture (manager + runner + config + tests)

**Files:**
- Modify: `src/mt5_trader/risk/manager.py` (full rewrite of the class; remove `RoundCfg`/`rounds`/`round_at`/`mark_round_start`)
- Modify: `config/risk.yaml` (replace `default:` + `rounds:` with `posture:`)
- Modify: `src/mt5_trader/live/runner.py:104-138` (`LiveRunner.__init__`, `_kill_check`), `:185-190` (`LiveRunner.run`), `:357-362` (`PortfolioRunner.run`)
- Test: `tests/test_risk.py` (rewrite), `tests/test_kill_flatten.py` (migrate + new assertion), `tests/test_vol_guard.py:85-88` (mechanical), `tests/test_calendar.py:47-52` (mechanical)

**Interfaces:**
- Produces:
  - `Posture(max_leverage: float, loss_limit: float, target_vol: float, give_back: float = 0.0)` — dataclass.
  - `RiskManager(posture: Posture, blackout: BlackoutCfg | None = None, events=None, vol_guard: VolGuardCfg | None = None)`.
  - `RiskManager.from_yaml(path) -> RiskManager` — reads a top-level `posture:` block plus `blackout:`/`vol_guard:` (unchanged).
  - `RiskManager.roll_day(now: datetime, equity: float) -> None` — reset the daily anchor at a new UTC day.
  - `RiskManager.size(signal: float, realized_vol_ann: float, now: datetime) -> float` — unchanged formula, reads `self.posture` (0.0 in blackout).
  - `RiskManager.kill(equity: float, now: datetime) -> bool` — daily-reset, sticky-for-day, peak-trailing trip.
- Consumes: `RiskManager.in_blackout`, `vol_halt`, `BlackoutCfg`, `VolGuardCfg` (unchanged).

- [ ] **Step 1: Rewrite `tests/test_risk.py` with the new API + behaviour (failing test)**

Replace the entire file with:

```python
from datetime import datetime, timezone

from mt5_trader.risk.manager import RiskManager, Posture

# size/kill unit posture (mirrors the old round1 numbers so size asserts hold)
POSTURE = Posture(max_leverage=3.0, loss_limit=0.05, target_vol=0.25)
T = datetime(2026, 6, 22, 12, tzinfo=timezone.utc)
T_NEXT = datetime(2026, 6, 23, 9, tzinfo=timezone.utc)


def mk(p=POSTURE):
    return RiskManager(p)


def test_vol_targeting_and_cap():
    rm = mk()
    assert abs(rm.size(1.0, 0.50, T) - 0.5) < 1e-9   # high vol -> scaled below cap
    assert rm.size(1.0, 0.01, T) == 3.0              # low vol -> capped at max_leverage
    assert rm.size(-1.0, 0.01, T) == -3.0


def test_kill_floor_from_day_start():
    rm = mk()
    rm.roll_day(T, 1_000_000)
    assert not rm.kill(990_000, T)   # -1%, fine
    assert rm.kill(940_000, T)       # -6% > 5% limit


def test_kill_without_anchor_is_safe():
    assert not mk().kill(900_000, T)   # no roll_day yet -> no anchor -> no trip


def test_kill_sticky_within_day():
    rm = mk()
    rm.roll_day(T, 1_000_000)
    assert rm.kill(940_000, T)         # trip, stand down
    assert rm.kill(1_000_000, T)       # recovered intraday but still halted today


def test_daily_reset_auto_resume():
    rm = mk()
    rm.roll_day(T, 1_000_000)
    assert rm.kill(940_000, T)         # trip on day T
    rm.roll_day(T_NEXT, 1_000_000)     # new UTC day -> reset anchor + clear halt
    assert not rm.kill(990_000, T_NEXT)  # -1% from new day-start -> resumed


def test_trailing_kill_protects_peak():
    rm = mk(Posture(max_leverage=3.0, loss_limit=0.10, target_vol=0.25, give_back=0.05))
    rm.roll_day(T, 1_000_000)
    assert not rm.kill(1_100_000, T)   # +10%, sets the peak
    assert not rm.kill(1_060_000, T)   # -3.6% from peak, within give_back
    assert rm.kill(1_040_000, T)       # -5.5% from 1.1M peak -> trailing kill
    #   ...even though still +4% on day-start (loss_limit 10% not hit)


def test_give_back_off_by_default():
    rm = mk()  # POSTURE has no give_back -> 0.0
    rm.roll_day(T, 1_000_000)
    rm.kill(1_200_000, T)              # peak 1.2M, no trip
    assert not rm.kill(1_150_000, T)   # -4.2% from peak but give_back off; +15% floor not hit


def test_from_yaml_reads_standing_posture():
    rm = RiskManager.from_yaml("config/risk.yaml")
    assert rm.posture.max_leverage == 2.0
    assert rm.posture.target_vol == 0.30
    assert rm.posture.loss_limit == 0.04
    assert rm.posture.give_back == 0.0
```

- [ ] **Step 2: Run test_risk to verify it fails**

Run: `uv run pytest tests/test_risk.py -q`
Expected: FAIL at collection — `ImportError: cannot import name 'Posture'`.

- [ ] **Step 3: Rewrite `src/mt5_trader/risk/manager.py`**

Replace the whole file with:

```python
"""Risk management: vol-targeted sizing + daily-reset kill switch.

A single standing posture (config/risk.yaml `posture:`). The kill switch anchors
to each UTC day's start equity, stands down flat on a trip, and auto-resumes the
next UTC day. All times UTC.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from ..features.calendar import BlackoutCfg, blackout_mask, load_events
from ..features.vol_guard import VolGuardCfg, vol_spike_mask


@dataclass
class Posture:
    max_leverage: float
    loss_limit: float      # daily kill: drawdown fraction from day-START equity
    target_vol: float      # annualized vol budget
    give_back: float = 0.0  # daily trailing kill: drawdown fraction from day-PEAK (0 = off)


class RiskManager:
    def __init__(self, posture: Posture,
                 blackout: BlackoutCfg | None = None, events=None,
                 vol_guard: VolGuardCfg | None = None):
        self.posture = posture
        self._blackout = blackout or BlackoutCfg()
        self._events = (events if events is not None
                        else load_events(self._blackout.calendar) if self._blackout.enabled
                        else None)
        self._vol_guard = vol_guard or VolGuardCfg()
        self._day = None                      # UTC date the anchor was set for
        self._day_start_equity: float | None = None
        self._day_peak_equity: float | None = None
        self._halted_day = None               # UTC date the kill tripped (stand down)

    @classmethod
    def from_yaml(cls, path: Path | str) -> "RiskManager":
        with open(path) as f:
            cfg = yaml.safe_load(f)

        p = cfg["posture"]
        posture = Posture(
            max_leverage=float(p["max_leverage"]),
            loss_limit=float(p["loss_limit"]),
            target_vol=float(p["target_vol"]),
            give_back=float(p.get("give_back", 0.0)),
        )

        b = cfg.get("blackout") or {}
        blackout = BlackoutCfg(
            enabled=bool(b.get("enabled", False)),
            calendar=b.get("calendar", BlackoutCfg.calendar),
            before_min=int(b.get("before_min", BlackoutCfg.before_min)),
            after_min=int(b.get("after_min", BlackoutCfg.after_min)),
            min_impact=str(b.get("min_impact", BlackoutCfg.min_impact)),
            currencies=list(b.get("currencies", ["USD"])),
        )

        vg = cfg.get("vol_guard") or {}
        vol_guard = VolGuardCfg(
            enabled=bool(vg.get("enabled", False)),
            atr_n=int(vg.get("atr_n", VolGuardCfg.atr_n)),
            atr_base_n=int(vg.get("atr_base_n", VolGuardCfg.atr_base_n)),
            atr_mult=float(vg.get("atr_mult", VolGuardCfg.atr_mult)),
            ret_limit_bps=float(vg.get("ret_limit_bps", VolGuardCfg.ret_limit_bps)),
        )
        return cls(posture, blackout, vol_guard=vol_guard)

    def in_blackout(self, now: datetime) -> bool:
        """Inside an economic-calendar event window (defensive mute)?"""
        if not self._blackout.enabled:
            return False
        return bool(blackout_mask([now], self._events, self._blackout)[0])

    def vol_halt(self, bars) -> bool:
        """True if the latest bar is an abnormal-volatility spike (fast circuit
        breaker) -> caller forces flat. No-op when vol_guard is disabled."""
        if not self._vol_guard.enabled:
            return False
        return bool(vol_spike_mask(bars, self._vol_guard)[-1])

    def size(self, signal: float, realized_vol_ann: float, now: datetime) -> float:
        """Target position as a leverage multiple of equity (signed).
        Forced flat inside an event blackout window."""
        if self.in_blackout(now):
            return 0.0
        p = self.posture
        lev = min(p.target_vol / max(realized_vol_ann, 1e-4), p.max_leverage)
        return float(signal) * lev

    def roll_day(self, now: datetime, equity: float) -> None:
        """Reset the daily anchor at each new UTC day; otherwise track the peak."""
        d = now.date()
        if d != self._day:
            self._day = d
            self._day_start_equity = equity
            self._day_peak_equity = equity
        else:
            self._day_peak_equity = max(self._day_peak_equity, equity)

    def kill(self, equity: float, now: datetime) -> bool:
        """True -> flatten everything and stand down for the rest of this UTC day.
        Sticky once tripped today; auto-resumes next day (a fresh roll_day clears
        the anchor and the date no longer matches _halted_day). Trips on drawdown
        from day-start (loss_limit) or, when give_back is set, from the day's peak."""
        today = now.date()
        if self._halted_day == today:
            return True
        if self._day_start_equity is None:
            return False
        self._day_peak_equity = max(self._day_peak_equity, equity)
        hit_floor = equity / self._day_start_equity - 1.0 <= -self.posture.loss_limit
        gave_back = (self.posture.give_back > 0
                     and equity / self._day_peak_equity - 1.0 <= -self.posture.give_back)
        if hit_floor or gave_back:
            self._halted_day = today
            return True
        return False
```

- [ ] **Step 4: Run test_risk to verify it passes**

Run: `uv run pytest tests/test_risk.py -q`
Expected: PASS (8 tests). `test_from_yaml_reads_standing_posture` will still FAIL until Step 5 (config not yet updated) — that is expected; proceed to Step 5 before re-running.

- [ ] **Step 5: Update `config/risk.yaml`**

Replace the `default:` block and the entire `rounds:` list with a single `posture:` block. Leave `blackout:` and `vol_guard:` untouched.

```yaml
# Standing risk posture. Times UTC. Single always-on budget (no competition rounds).
# target_vol = annualized vol budget; sizing = min(target_vol / realized_vol, max_leverage).
# loss_limit = daily kill switch: drawdown fraction from the day-START equity. On a
#   trip the bot flattens and stands down flat for the rest of the UTC day, then
#   auto-resumes the next UTC day (the loop keeps running — a kill is not a process exit).
# give_back = daily trailing kill: flatten if equity falls this fraction from the
#   day's PEAK (protects an intraday gain; 0 = off).

posture:
  max_leverage: 2
  target_vol:   0.30   # gold runs ~0.40 ann vol -> lev ~0.75
  loss_limit:   0.04   # 4% daily kill, auto-resume next UTC day
  give_back:    0.0    # trailing daily kill off
```

(Keep the existing `blackout:` and `vol_guard:` sections exactly as they are.)

- [ ] **Step 6: Re-run test_risk (now including the from_yaml test)**

Run: `uv run pytest tests/test_risk.py -q`
Expected: PASS (8 tests, including `test_from_yaml_reads_standing_posture`).

- [ ] **Step 7: Update the kill path in `src/mt5_trader/live/runner.py`**

In `LiveRunner.__init__` (line ~110), replace the halt flag:

```python
        self.halted = False
```
with:
```python
        self._stood_down = False   # currently flat for the day on a kill trip
```

Replace `_kill_check` (lines ~126-137) with:

```python
    def _kill_check(self, now: datetime) -> tuple[float, bool]:
        """Snapshot equity, roll the daily anchor, test the kill switch. On a
        trip: flatten everything and stand down flat for the rest of the UTC day
        (the loop keeps running; auto-resume next day). Returns (equity, killed)
        so the caller's step() returns immediately when killed. Shared by every
        runner's step()."""
        equity = self.gw.equity()
        self.risk.roll_day(now, equity)
        if self.risk.kill(equity, now):
            self.flatten_all()
            if not self._stood_down:                 # log once on the trip transition
                self._stood_down = True
                self._log(event="KILL_SWITCH", equity=equity)
            return equity, True
        if self._stood_down:                         # a new UTC day cleared the stand-down
            self._stood_down = False
            self._log(event="RESUME", equity=equity)
        return equity, False
```

In `LiveRunner.run` (line ~188) change the halt callback:
```python
        resilient_loop(self.step, self._log, self.gw.reconnect,
                       self.cfg.poll_seconds, lambda: self.halted)
```
to:
```python
        resilient_loop(self.step, self._log, self.gw.reconnect,
                       self.cfg.poll_seconds, lambda: False)
```

In `PortfolioRunner.run` (line ~360) make the same change:
```python
        resilient_loop(self.step, self._log, self.gw.reconnect,
                       self.cfg.poll_seconds, lambda: False)
```

(The trailing `self._log(event="HALTED")` in each `run()` is now effectively unreachable — leave it; harmless. `PairRunner` inherits `LiveRunner.run`, so it is covered.)

- [ ] **Step 8: Migrate `tests/test_kill_flatten.py` to the new API + stand-down semantics**

Replace the file with:

```python
"""Kill-switch wiring: a tripped kill must flatten every symbol and stand down,
before any book/bars processing. Exercises the live PortfolioRunner.step path
with a fake gateway (no MT5, no VPS)."""
from datetime import datetime, timezone

from mt5_trader.live.runner import Book, PortfolioRunner, RunnerCfg
from mt5_trader.risk.manager import RiskManager, Posture

NOW = datetime.now(timezone.utc)


class FakeGW:
    def __init__(self, equity):
        self._eq = equity
        self.flattened = []

    def equity(self):
        return self._eq

    def flatten(self, symbol, magic=None):
        self.flattened.append(symbol)

    def bars(self, *a, **k):           # must never be reached once kill trips
        raise AssertionError("kill must short-circuit before fetching bars")


def test_kill_flattens_every_symbol_and_stands_down(tmp_path):
    gw = FakeGW(900_000)               # down 10% from the seeded day-start
    posture = Posture(max_leverage=2.0, loss_limit=0.05, target_vol=0.3)
    rm = RiskManager(posture)
    books = [Book(strategy=object(), symbol="XAUUSD"),
             Book(strategy=object(), symbol="XAGUSD")]
    cfg = RunnerCfg(symbol="PORTFOLIO", log_path=tmp_path / "p.jsonl")
    runner = PortfolioRunner(gw, books, rm, cfg)

    rm.roll_day(NOW, 1_000_000)        # day-start anchored high
    runner.step()

    assert runner._stood_down is True
    assert sorted(gw.flattened) == ["XAGUSD", "XAUUSD"]
    assert "KILL_SWITCH" in (tmp_path / "p.jsonl").read_text()
```

- [ ] **Step 9: Run test_kill_flatten to verify it passes**

Run: `uv run pytest tests/test_kill_flatten.py -q`
Expected: PASS (1 test).

- [ ] **Step 10: Mechanically migrate `tests/test_vol_guard.py` and `tests/test_calendar.py`**

In `tests/test_vol_guard.py`, change the import (line ~7) from `from mt5_trader.risk.manager import RiskManager, RoundCfg` to `from mt5_trader.risk.manager import RiskManager, Posture`, and replace the `_rm` helper (lines ~85-88) with:

```python
def _rm(vg: VolGuardCfg) -> RiskManager:
    p = Posture(max_leverage=2, loss_limit=0.04, target_vol=0.3)
    return RiskManager(p, vol_guard=vg)
```

In `tests/test_calendar.py`, change the import (line ~6) to `from mt5_trader.risk.manager import RiskManager, Posture`, and replace the `_rm` helper (lines ~47-52) with:

```python
def _rm():
    p = Posture(max_leverage=2, loss_limit=0.04, target_vol=0.3)
    cfg = BlackoutCfg(enabled=True, before_min=15, after_min=15,
                      min_impact="high", currencies=["USD"])
    return RiskManager(p, cfg, _events())
```

- [ ] **Step 11: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS — entire suite green (no `RoundCfg`/`round_at`/`mark_round_start` references remain).

Sanity grep — expect no matches in source/tests:
Run: `grep -rnE "RoundCfg|round_at|mark_round_start|\.halted\b|cfg\[.rounds.\]" src tests`
Expected: no output.

- [ ] **Step 12: Commit**

```bash
git add src/mt5_trader/risk/manager.py src/mt5_trader/live/runner.py config/risk.yaml \
        tests/test_risk.py tests/test_kill_flatten.py tests/test_vol_guard.py tests/test_calendar.py
git commit -m "feat(risk): standing daily-reset posture, remove competition rounds

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Deploy (after merge)

Not a code step — operator action. On the VPS (`ssh mt5-vps`):
1. `cd Desktop\MT5_Trader; git pull`
2. **Relaunch the portfolio bot** (RDP-launched via `start_all.bat`) so it loads the new `config/risk.yaml`. The passive + tick-logger bots are untouched and need no restart.
3. Confirm `logs\portfolio.jsonl` shows fresh STEP events with the standing posture (`weighted_frac` still consistent with `target_vol 0.30`, `max_leverage 2`).

## Self-Review

- **Spec coverage:** config rounds→posture (Step 5) ✓; `RoundCfg`→`Posture` + remove `round_at`/`rounds` (Step 3) ✓; daily-reset `roll_day`/`kill` (Step 3) ✓; runner stand-down, no process exit (Step 7) ✓; test migration + new behaviour tests: floor, no-anchor, sticky, auto-resume, trailing, give-back-off, from_yaml (Steps 1, 8, 10) ✓; passive/sizing/blackout/vol_guard untouched ✓.
- **Placeholder scan:** none — every code step shows full content.
- **Type consistency:** `Posture(max_leverage, loss_limit, target_vol, give_back=0.0)` and `RiskManager(posture, blackout=None, events=None, vol_guard=None)`, `roll_day(now, equity)`, `kill(equity, now)` used identically across manager, runner, and all tests.
