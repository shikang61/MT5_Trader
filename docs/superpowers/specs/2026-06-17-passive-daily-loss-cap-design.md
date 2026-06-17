# Design: Daily loss cap for the passive consolidation book

Date: 2026-06-17
Status: approved (design); implementation pending
Scope: `PassiveLimitRunner` (magic 2003) + one `MT5Gateway` method. No portfolio
or strategy changes.

## Problem

The passive consolidation book (`run_passive_paper.py`, magic 2003) has only
**per-trade** risk control: a 15bp TP/SL bracket and a 30min time-exit (both
present since the first commit `48d6fd3`, 2026-06-15). It has **no
account/aggregate circuit breaker** — `grep` confirms no RiskManager, no kill,
no loss limit; its `resilient_loop` halt is hardcoded `lambda: False`.

Consequences on the shared MT5 account:
- A string of stop-outs in a fakeout-heavy range is unbounded by the per-trade
  stop (ten 15bp losers = 150bp).
- The portfolio kill switch (`runner.py:183`) only flattens **magic 1001**, so a
  −5% combined drop leaves the passive book trading into the drawdown.
- The passive book's losses drag combined equity and can trip the *portfolio's*
  kill.

TP/SL is per-position, not cumulative — it does not isolate account risk. The
fix is a standalone daily loss cap on the passive book's **own** realized P&L.

## Decisions (agreed during brainstorming)

- **Trip basis:** daily realized loss for magic 2003 (broker truth via
  `realized_by_magic`), re-derived each check → **restart-safe** (survives the
  process restarts seen in the logs). Realized-only: open-position float is
  already bounded by the 15bp SL + 30min time-exit.
- **Cap:** `daily_loss_cap = 150.0` (account currency) ≈ 5 max-loss trades
  (0.05 lot, 15bp ≈ $32/trade) ≈ 0.15% of account. `0` = off.
- **On trip:** cancel pendings + flatten + stand down for the rest of the UTC
  day, **auto-resume next UTC day** (reuses the runner's existing `_day`
  tracking). Not a hard process halt.

## Components

### 1. `MT5Gateway.realized_pnl(magic, since_utc)` (new method)

```python
def realized_pnl(self, magic: int, since_utc: datetime) -> float:
    """Net realized P&L (profit+commission+swap) for `magic`'s CLOSED deals
    since `since_utc`, from broker history. Broker truth; restart-safe."""
    deals = mt5.history_deals_get(
        since_utc, datetime.now(timezone.utc) + timedelta(days=1)) or []
    agg = realized_by_magic(deals, mt5.DEAL_ENTRY_OUT)
    return agg.get(magic, {}).get("net", 0.0)
```
Mirrors `dashboard/remote_probe.py`'s deal-window pattern and reuses
`mt5_trader.live.broker_pnl.realized_by_magic`. Thin mt5 wrapper — not
unit-tested (consistent with the other gateway methods); verified via the
existing `pnl_report.py --broker` path.

### 2. `daily_halt(realized_today, cap, halted_day, today)` (pure helper)

In `passive_limits.py` (matches the file's "pure helpers unit-tested, loop is a
thin broker wrapper" convention):
```python
def daily_halt(realized_today, cap, halted_day, today):
    """(active_halt, new_halted_day). Trips when realized_today <= -cap; stays
    halted for the rest of `today`; a past halted_day clears (auto-resume)."""
    if cap > 0 and realized_today <= -cap:
        return True, today
    if halted_day == today:
        return True, today
    return False, halted_day
```

### 3. `PassiveLimitRunner` wiring

- `PassiveCfg.daily_loss_cap: float = 150.0` (account currency; 0 = off).
- New state `self._halted_day = None`; throttle field `self._last_cap_check = 0.0`.
- In `step()`, before arming:
  - Throttle: re-query `realized_pnl` at most once per ~30s (cache the last
    value between checks) to avoid a history call every 5s poll.
  - `sod` = current UTC day midnight; `realized = gw.realized_pnl(magic, sod)`.
  - `active, self._halted_day = daily_halt(realized, cfg.daily_loss_cap,
    self._halted_day, now.date())`.
  - **On the trip transition** (became active this step): `cancel_pending` +
    `flatten` + `_log(event="KILL", realized=..., cap=...)`.
  - **While `active`:** `cancel_pending` (defensive — catch a late fill),
    skip arming, keep the SNAP heartbeat, return early.
  - **When not active:** existing arm/relevel/exit logic runs unchanged;
    a rolled-over `halted_day` naturally yields `active=False` → auto-resume.

`resilient_loop`'s halt callback stays `lambda: False` (stand down, not exit).

## Tests (`tests/test_passive_limits.py`)

`daily_halt` only (pure):
- `realized <= -cap` → `(True, today)` (trip).
- already `halted_day == today`, `realized` above cap → still `(True, today)`.
- `halted_day` = yesterday, `realized` fine today → `(False, yesterday)` → resume.
- `cap = 0` → never trips regardless of `realized`.

## Out of scope

- Realized + open-float trip (float bounded by SL + time-exit).
- Hard process halt / separate MT5 account isolation (the stronger option (a),
  not chosen now).
- Portfolio runner, strategies, other magics.

## Risks

- **Day boundary vs server time.** UTC-midnight window into `history_deals_get`;
  MT5 deal times are server-time (UTC+2/3, Lesson #12). For a coarse ~5-trade
  safety cap this edge imprecision is acceptable; revisit if false trips cluster
  near midnight.
- **Throttle vs latency.** A ~30s cap-check cadence means up to ~30s of extra
  fills past the cap before standing down; bounded by lot size + per-trade SL,
  negligible at 0.05 lot.
- **Shared account remains.** This caps the passive book's own loss but does not
  fully isolate equity/margin — a separate MT5 account (option a) would; out of
  scope here.

## Workflow

Branch `feat/passive-daily-loss-cap` → TDD `daily_halt` → wire the runner + add
the gateway method → full suite → PR. Deploy: VPS `git pull`, then **relaunch
the passive bot** (RDP-launched, not a scheduled task).
