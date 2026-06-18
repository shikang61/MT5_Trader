# Passive Fade Geometry — Design

**Date:** 2026-06-18
**Status:** Design approved; light process (small change to `passive_limits.py`)
**Type:** Live paper-book tweak (consolidation passive, magic 2003)

## Motivation

Thread-3 review: the consolidation passive lost live (0/3 today, −$83), fading levels that
then stopped out. Diagnostics (`eval_passive_limits.py` gate sweep + a TP/SL touch-first
sweep on the live 8h/8h gate fills, data/real+ticks):

- **Gate is fine — leave it.** Live 8h/8h (`range_n=96`, `regime_coarsen=6`) is near-optimal:
  SELL forward drift +5–7bp at 30–60m. Tightening the gate to 4h makes sells badly adverse
  (−20bp). So "tighten the gate" is the wrong fix.
- **The geometry is the leak, and today's 1:2 was backwards.** TP/SL sweep (SELL, hold 30m):

  | | sl15 | sl20 | **sl25** | sl30 |
  |---|---|---|---|---|
  | tp20 | +4.4 | +5.9 | **+7.5 (win 73%)** | +6.8 |
  | tp15 | +2.6 | +3.8 | +5.3 | +4.6 |
  | tp30 (current) | **+3.8 (win 55%)** | +4.8 | +6.9 | +6.2 |

  A high-win-rate fade wants a **wider stop + closer target**. The tight 15bp stop cut fills at
  the noise-tail before the level reverted; the 1:2 (tp30/sl15) pushed the target past the
  ~15bp typical reversion. `tp20/sl25` (+7.5bp, 73%) dominates `tp30/sl15` (+3.8, 55%).
- Buy side ≈ 0bp (no reversion edge this down-trending month) — harmless, keep for symmetry.

## Changes (`src/mt5_trader/live/passive_limits.py`)

1. **`PassiveCfg` geometry:** `tp_bp 30 → 20`, `sl_bp 15 → 25` (the +7.5bp / 73%-win cell).
2. **Decouple the breakout-cancel from the stop:** add `breakout_bp: float = 15.0`; `_broke_out`
   uses `breakout_bp` instead of `sl_bp`. Otherwise widening `sl_bp` to 25 would widen the
   band-break tolerance and make the gate hold limits *into* breakouts longer (re-introducing
   the adverse fills the gate exists to prevent). Keep the cancel tight (15bp); let the position
   stop be wide (25bp) — they serve different roles.

## Testing (`tests/test_passive_limits.py`)

- Update `test_broke_out_cancels_outside_band` to set `breakout_bp=15.0` explicitly (margin
  unchanged at 15bp; same assertions hold).
- Add `test_broke_out_independent_of_sl_bp`: with `breakout_bp=15, sl_bp=25`, a price 20bp past
  the ceiling still breaks (driven by `breakout_bp`, not `sl_bp`).
- Existing `limit_params` / `daily_halt` / `restable` tests unchanged.

## Validation & caveats

n≈22 sells / 25 buys, one in-sample down-trend month, optimistic fills (front-of-queue). The
**direction** (wider stop beats tighter) is robust across the whole grid; the exact 20/25 is
provisional. This is the live **paper** book (small fixed lots) — deploy and gather real fills.

## Out of scope (YAGNI)

- No gate change (`range_n`/`regime_coarsen` stay 96/6).
- No change to the daily loss cap, time-exit, or relevel logic.
- Not a backtested live-money strategy — paper experiment only.
