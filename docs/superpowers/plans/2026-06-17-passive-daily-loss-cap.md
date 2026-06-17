# Passive Daily Loss Cap — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the passive consolidation book (magic 2003) a standalone daily loss cap on its OWN realized P&L, so it stands down for the rest of the UTC day when it bleeds past the cap and auto-resumes next day.

**Architecture:** A pure decision helper `daily_halt()` (unit-tested) decides trip/standdown/resume from the book's realized P&L. A new `MT5Gateway.realized_pnl(magic, since_utc)` reads that P&L from broker deal history (reusing `realized_by_magic`). `PassiveLimitRunner.step()` queries it (throttled), trips via the helper, and cancels+flattens+stands down. No portfolio or strategy changes.

**Tech Stack:** Python, pytest, `uv` (run via `uv run ...`). MetaTrader5 package is win32-only (the gateway method runs on the VPS; not unit-tested, like the other gateway methods).

## Global Constraints

- **Trip basis:** daily realized loss for magic 2003 (broker truth via `realized_by_magic`), re-derived each check → restart-safe. Realized-only (open float bounded by the existing 15bp SL + 30min time-exit). Verbatim from spec.
- **Cap:** `daily_loss_cap = 150.0` account currency; `0` = off.
- **On trip:** cancel pendings + flatten + stand down for the rest of the UTC day; auto-resume next UTC day (reuse the runner's `_day`-style date tracking).
- **No hard process halt:** `resilient_loop`'s halt stays `lambda: False`.
- **Throttle:** re-query realized P&L at most ~once/30s; force a refresh when the UTC day rolls over (else a stale cached loss re-trips on the new day).
- Scope: `passive_limits.py` + one `mt5_gateway.py` method only.

---

### Task 1: Pure `daily_halt` decision helper (TDD)

**Files:**
- Modify: `src/mt5_trader/live/passive_limits.py`
- Test: `tests/test_passive_limits.py`

**Interfaces:**
- Produces: `daily_halt(realized_today: float, cap: float, halted_day, today) -> tuple[bool, object]` — returns `(active_halt, new_halted_day)`. `halted_day`/`today` are `datetime.date` (or None for `halted_day`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_passive_limits.py`:

```python
from datetime import date

from mt5_trader.live.passive_limits import daily_halt


def test_daily_halt_trips_on_loss_past_cap():
    today = date(2026, 6, 17)
    active, hd = daily_halt(realized_today=-150.0, cap=150.0, halted_day=None, today=today)
    assert active is True and hd == today
    # just under the cap does not trip
    active2, hd2 = daily_halt(-149.99, 150.0, None, today)
    assert active2 is False and hd2 is None


def test_daily_halt_stays_halted_rest_of_day():
    today = date(2026, 6, 17)
    # already halted earlier today; realized has since recovered above the cap
    active, hd = daily_halt(realized_today=-10.0, cap=150.0, halted_day=today, today=today)
    assert active is True and hd == today


def test_daily_halt_resumes_next_day():
    yesterday, today = date(2026, 6, 16), date(2026, 6, 17)
    active, hd = daily_halt(realized_today=-5.0, cap=150.0, halted_day=yesterday, today=today)
    assert active is False and hd == yesterday   # past halt cleared -> resume


def test_daily_halt_off_when_cap_zero():
    today = date(2026, 6, 17)
    active, hd = daily_halt(realized_today=-9999.0, cap=0.0, halted_day=None, today=today)
    assert active is False and hd is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_passive_limits.py -k daily_halt -v`
Expected: FAIL — `ImportError: cannot import name 'daily_halt'`.

- [ ] **Step 3: Implement the helper**

In `src/mt5_trader/live/passive_limits.py`, add near the other module-level pure helpers (after `desired_sides`):

```python
def daily_halt(realized_today, cap, halted_day, today):
    """Daily loss-cap decision. Returns (active_halt, new_halted_day).

    Trips when today's realized P&L falls to -cap or below; once tripped, stays
    active for the rest of `today`; a halted_day from a previous day clears
    (auto-resume next UTC day). cap <= 0 disables the cap."""
    if cap > 0 and realized_today <= -cap:
        return True, today
    if halted_day == today:
        return True, today
    return False, halted_day
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_passive_limits.py -k daily_halt -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/mt5_trader/live/passive_limits.py tests/test_passive_limits.py
git commit -m "feat(passive): daily_halt loss-cap decision helper"
```

---

### Task 2: Gateway `realized_pnl` + runner wiring

**Files:**
- Modify: `src/mt5_trader/live/mt5_gateway.py`
- Modify: `src/mt5_trader/live/passive_limits.py`
- Test: `tests/test_passive_limits.py`

**Interfaces:**
- Consumes: `daily_halt` (Task 1); `mt5_trader.live.broker_pnl.realized_by_magic`.
- Produces: `MT5Gateway.realized_pnl(magic: int, since_utc: datetime) -> float`; `PassiveCfg.daily_loss_cap: float = 150.0`.

- [ ] **Step 1: Write the failing test** (config field default + runner state)

Add to `tests/test_passive_limits.py`:

```python
def test_passive_cfg_has_daily_loss_cap_default():
    assert PassiveCfg().daily_loss_cap == 150.0


def test_runner_initializes_halt_state():
    r = PassiveLimitRunner(None, PassiveCfg())
    assert r._halted_day is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_passive_limits.py -k "daily_loss_cap or halt_state" -v`
Expected: FAIL — `AttributeError: 'PassiveCfg' object has no attribute 'daily_loss_cap'`.

- [ ] **Step 3: Add the gateway method**

In `src/mt5_trader/live/mt5_gateway.py`:

Change the datetime import line `from datetime import datetime, timezone` to:
```python
from datetime import datetime, timedelta, timezone
```

Add the broker_pnl import after the polars import:
```python
from .broker_pnl import realized_by_magic
```

Add the method after `equity()`:
```python
    def realized_pnl(self, magic: int, since_utc: datetime) -> float:
        """Net realized P&L (profit+commission+swap) for `magic`'s CLOSED deals
        since `since_utc`, from broker deal history. Broker truth, so it is
        restart-safe (re-derived, not accumulated in-process). Mirrors the
        deal-window pattern in dashboard/remote_probe.py."""
        deals = mt5.history_deals_get(
            since_utc, datetime.now(timezone.utc) + timedelta(days=1)) or []
        agg = realized_by_magic(deals, mt5.DEAL_ENTRY_OUT)
        return agg.get(magic, {}).get("net", 0.0)
```

- [ ] **Step 4: Add the config field + runner state + wiring**

In `src/mt5_trader/live/passive_limits.py`:

Add to `PassiveCfg` (after `magic: int = 2003`):
```python
    daily_loss_cap: float = 150.0  # account-ccy realized-loss cap for THIS book's
                                   # magic over the UTC day; trip -> cancel+flatten+
                                   # stand down till next day. 0 = off. The book's
                                   # only aggregate circuit breaker (per-trade is
                                   # TP/SL+time-exit; account equity is shared).
```

Add to `__init__` (after `self._last_pos = 0.0`):
```python
        self._halted_day = None    # UTC date the daily loss cap tripped (stand down)
        self._cap_day = None       # UTC date of the last realized-pnl query
        self._cap_realized = 0.0   # cached realized P&L for _cap_day
        self._last_cap_check = 0.0 # monotonic time of the last realized-pnl query
```

In `step()`, insert the cap block immediately after `sell_level, buy_level = lv` (before the `regime = ...` line):

```python
        # Daily loss cap (this book's own realized P&L, broker truth). Throttled
        # to ~30s; force a refresh when the UTC day rolls over so a stale loss
        # can't re-trip the new day.
        today = now.date()
        if self.cfg.daily_loss_cap > 0:
            mono = time.monotonic()
            if today != self._cap_day or mono - self._last_cap_check >= 30.0:
                sod = now.replace(hour=0, minute=0, second=0, microsecond=0)
                self._cap_realized = self.gw.realized_pnl(self.cfg.magic, sod)
                self._cap_day, self._last_cap_check = today, mono
            was_active = self._halted_day == today
            active, self._halted_day = daily_halt(
                self._cap_realized, self.cfg.daily_loss_cap, self._halted_day, today)
            if active:
                self.gw.cancel_pending(self.cfg.symbol, magic=self.cfg.magic)
                if not was_active:                      # trip transition
                    self.gw.flatten(self.cfg.symbol, magic=self.cfg.magic)
                    self._log(event="KILL", scope="daily_loss_cap",
                              realized=self._cap_realized, cap=self.cfg.daily_loss_cap)
                    self._last_pos = 0.0
                if time.monotonic() - self._last_log_t >= self.cfg.heartbeat_seconds:
                    self._log(event="SNAP", equity=self.gw.equity(), position_lots=0.0,
                              regime=int(regime_series(bars, coarsen=self.cfg.regime_coarsen)[-1]),
                              sell_level=sell_level, buy_level=buy_level, halted=True)
                return  # stand down: no arming this step
```

(The rest of `step()` — regime, arm, position/exit — is unchanged and runs only when not halted.)

- [ ] **Step 5: Run the targeted + full suite**

Run: `uv run pytest tests/test_passive_limits.py -v && uv run pytest -q`
Expected: all pass (new cfg/state tests green; `daily_halt` tests green; no regressions).

- [ ] **Step 6: Commit**

```bash
git add src/mt5_trader/live/mt5_gateway.py src/mt5_trader/live/passive_limits.py tests/test_passive_limits.py
git commit -m "feat(passive): daily loss cap (realized magic 2003, stand down + auto-resume)"
```

---

## Self-Review

**Spec coverage:**
- `realized_pnl` gateway method (broker truth, restart-safe) → Task 2 Step 3. ✓
- `daily_halt` pure helper + tests → Task 1. ✓
- `PassiveCfg.daily_loss_cap=150.0`, 0=off → Task 2 Step 4. ✓
- Trip → cancel+flatten+KILL log; while-halted → cancel+skip arm+heartbeat+return; auto-resume next day → Task 2 Step 4. ✓
- Throttle ~30s + midnight refresh → Task 2 Step 4. ✓
- `resilient_loop` halt unchanged → not touched. ✓
- Realized-only / open float bounded by SL+time-exit → no float term added. ✓

**Placeholder scan:** all code shown verbatim; no TBD/TODO. ✓

**Type consistency:** `daily_halt(realized_today, cap, halted_day, today)` signature identical in helper, tests, and the runner call. `realized_pnl(magic, since_utc)` matches its call `self.gw.realized_pnl(self.cfg.magic, sod)`. `self._halted_day`/`_cap_day`/`_cap_realized`/`_last_cap_check` initialized in `__init__` and used in `step()`. `daily_loss_cap` field name consistent. ✓

**Deploy note (not a code task):** VPS `git pull`, then **relaunch the passive bot** — it is RDP-launched (`run_passive_paper.py`), not a scheduled task, so a pull alone won't restart it.
