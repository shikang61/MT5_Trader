# Reopen Disaster-Stop Guard — Design

**Date:** 2026-06-19
**Status:** Design approved; pending implementation plan
**Type:** Live runner change (portfolio disaster stop, magic 1001)

## Motivation

2026-06-18 review: six short positions all closed at 22:00:01 UTC, broker deal
**`reason=SL`**, every fill at the identical blown reopen quote (4250.65), then gold fell
to 4205 — the 8× disaster stops were **swept by the gold-reopen spread blowout**, closing
winning shorts at the worst tick.

Diagnostic (`/tmp/diag_reopen_spread.py`, raw `data/real` ticks):

- **8×-ATR stop distance: median 90bp.** **Reopen ask-spike: median 47bp, max 200bp →
  exceeds the stop on 21% of days.** Reopen open-spread: median 89bp, max 234bp.
- The blowout is a **sub-second phantom** (spread normalizes in ~0.7s median).
- The broker-side SL triggers on the raw wick; the bot polls ~2s on **completed bars**, so
  it would never act on the phantom (sees the collapsed price).
- The reopen overshoot **reverts** (`eod-asian-session-windows`: fade>ride) — so stopping
  on it is wrong.

So the disaster stop (a guardrail) is being triggered ~1-in-5 carried nights by a phantom
that reverts. The backtest hides it (mid-based bars under-represent the ask spike); it only
bites live.

## Approach (chosen): suppress the broker SL across the reopen window

When `now` (UTC) is inside the reopen guard window, the live portfolio runner detaches its
broker-side disaster stop, so the sub-second reopen wick cannot trigger it; normal
chandelier trailing re-attaches after the window. Rejected alternatives: a close-confirmed
bot-side stop (correct long-term but a larger change; revisit later) and a wide reopen-window
SL (fiddly multiple, a real >3× crash still unprotected).

## Component

`src/mt5_trader/live/runner.py` — `PortfolioRunner.step` (live magic 1001). The passive book
(magic 2003, own process, limit-order TP/SL) is unaffected. The single-strategy `Runner` is
out of scope (live uses `PortfolioRunner`); the pure helper is shared if it later needs it.

- **`RunnerCfg`** gains `reopen_guard: tuple[str, str] | None = ("21:58", "22:03")` — UTC
  `HH:MM` window, `None` disables. Starts before 22:00 so the SL is already detached when the
  spike lands; ends past the blowout (diag: ~0.7s typical, 22.9s worst).
- **Pure helper** `in_reopen_guard(now: datetime, window: tuple[str, str] | None) -> bool`:
  True when `now`'s UTC time-of-day is in `[start, end)`. Same-day window (no midnight wrap).

## Behavior (in `PortfolioRunner.step`, per symbol)

After computing the chandelier `sl`:
```
if in_reopen_guard(now_utc, cfg.reopen_guard):
    sl = 0.0                                            # new in-window entry: no sweepable SL
    gw.set_position_sltp(sym, 0.0, 0.0, magic)          # detach the parked broker SL
```
`reconcile(sym, lots, sl=sl, ...)` then runs as today (sl=0 → won't re-attach in-window).
`set_position_sltp` only sends on change, so it clears once then no-ops; out of the window
the normal `sl>0` path re-attaches on the next step.

## Testing (`tests/test_runner.py` or existing runner test)

- **`in_reopen_guard`** pure cases: before 21:58 → False; 21:58/22:00/22:02 → True; 22:03 →
  False; `None` window → always False.
- **Step-level** with a stub gateway: when `now` ∈ window, `set_position_sltp` is called with
  `sl=0` and `reconcile` receives `sl=0`; when out of window, `reconcile` receives `sl>0`
  (the chandelier). Use a fixed/injected `now` (see how the runner reads time).

## Caveats & out of scope

- ~5 min/night the portfolio carries **no broker-side stop**; residual risk = a *real* reopen
  crash *while the bot is dead*. Rare; the reopen reverts (fade>ride). Accepted.
- No change to the passive book, the kill-switch, vol-halt, or the backtest engine.
- Not re-tuning the 8× distance — only shielding it from the reopen phantom.
