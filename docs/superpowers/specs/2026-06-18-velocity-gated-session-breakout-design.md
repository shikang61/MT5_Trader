# VelocityBreakout — Design

**Date:** 2026-06-18
**Status:** Design approved; pending implementation plan
**Type:** Backtest-only strategy (registered + tested; NOT in `portfolio.yaml`)

## Motivation

Two gaps motivated this: a coverage hole found while diagnosing live behaviour
(the breakout books are donchian @ 20h and the M1 fade books — a 4h-scale break
falls between them), and a proposed momentum-burst scalper (ride a level break
confirmed by volume + velocity + momentum). Rather than build that greenfield —
which re-derives known-dead research — the components were tested against the
real-feed tick archive (`data/real`, 2026-05-11→06-10, XAUUSD M1) on 2026-06-18.

What the data said:

- **Volume confirmation is backwards.** `research_volume.py` study A buckets 60-min
  channel breaks by hour-normalised activity: quiet +0.21bp, normal −0.21bp,
  **high-activity −1.66bp (hit 43%)** vs a 0.73bp cost hurdle. High-volume breaks
  *fade* (stop-runs / exhaustion) — the opposite of continuation. Volume is
  dropped.
- **The breakout edge is session-locked.** `research_event_breakout.py`: an ORB
  armed at **12:30 UTC** (US data drop) pays **+20.2bp/trade at a 120-min hold**
  (slip 2bp), sum +323bp, **4/5 weeks positive**, hold plateau 90–180m all green;
  06:00 and 18:00 arms are negative. 14:00 is also positive (+7.2bp/30m). But it
  is thin (~16 trades/mo) and **never gated/deployed**.
- **Velocity separates continuation from fade — but only in the US window.**
  Diagnostic (throwaway `/tmp/diag_velocity_break.py`, same break events, bucketed
  by velocity instead of volume):
  - ALL hours: no edge in any velocity bucket (gold breaks fade in general).
  - **US window 12–15 UTC, fwd 120m:** HIGH velocity by Kaufman ER (clean break)
    = **+13.5bp, hit 60%**; HIGH velocity by break-bar push/ATR = **+13.9bp, hit
    56%**. Mid/churn buckets fade. fwd 30m: HIGH-ER +4.5bp (hit 55%).

**Conclusion:** the deployable form of the alive-but-marginal `event_breakout` is a
US-session ORB **gated by break velocity** (clean/fast breaks continue, churn
breaks fade). Velocity is the confirmation leg that works where volume did not.

**Caveats:** one in-sample month; the velocity diagnostic counts overlapping 1m
breakout episodes (effective n far below the 323 shown); `event_breakout` is ~16
trades/mo. Underpowered. Hence backtest-only until forward validation on the
competition dump (Jun 15–27), which adds fresh, unseen US-session events.

## Approach (chosen: A — new standalone class)

A new `VelocityBreakout` strategy in its own file, mirroring `session_vol.py`'s
time-of-day opening-range-breakout structure plus the velocity gate. One strategy
per file is the repo pattern.

Rejected:
- **B (add a velocity param to `LondonBreakout` / generalise `event_breakout`):**
  `LondonBreakout` is a live-book strategy — modifying it for a backtest-only
  experiment fails surgical-changes (`LESSONS.md #9`: don't risk a live book).
  `event_breakout` is a *research script*, not a `Strategy`, so there is nothing
  to extend.
- **C (extract a shared ORB helper now):** refactors before a second ORB strategy
  exists — YAGNI. Revisit if/when this is promoted.

## Component

`src/mt5_trader/strategies/velocity_breakout.py`

- `@register` dataclass `VelocityBreakout`, `name = "velocity_breakout"`, conforming
  to the `Strategy` protocol: `signal(bars) -> np.ndarray` returning target position
  in [-1, 1] per bar (decided on bar close, engine shifts execution one bar).
- Registered → picked up by the backtest registry, `validate_books`, and
  `test_strategies`. **Not** added to `portfolio.yaml` (no live sizing).

## Signal logic (per UTC day, per arm time)

1. **Pre-range:** for each arm time, the high/low of closes in `[arm − pre_min, arm)`
   (close-based, mirrors `event_breakout`). Complete before any trade bar.
2. **Break scan:** in `[arm, arm + arm_min)`, the first close beyond the pre-range
   is a candidate break (above → up, below → down).
3. **Velocity gate (the novel bit):** accept the break only if the Kaufman
   efficiency ratio over `er_n` bars at the break bar `≥ er_min` (clean, directional
   — not churn). `push_min` (break-bar |return|/ATR ≥ threshold) is an optional
   secondary lever, **off by default**; the two gates are NOT AND-ed by default
   (each extra gate halves an already-thin sample).
4. **Enter** in the break direction, hold `hold_bars`, then flat. At most one entry
   per arm time per UTC day; a rejected (churn) break arms nothing.
5. State resets each UTC day. No-lookahead: the pre-range uses only `[arm−pre, arm)`
   and the break/ER use only bars ≤ t. No `.shift(-k)`.

## Parameters (defaults from the diagnostic)

| Param | Default | Meaning |
|---|---|---|
| `arm_times` | `["12:30", "14:00"]` | UTC; US data-drop windows where breaks trend |
| `pre_min` | 30 | pre-range window length before the arm (min) |
| `arm_min` | 30 | scan window after the arm for the first break |
| `er_n` | 10 | bars for the Kaufman ER (break cleanliness) |
| `er_min` | 0.5 | min ER to accept a break (provisional; swept at validation) |
| `push_min` | 0.0 | optional secondary gate, break-bar \|ret\|/ATR; 0 = off |
| `hold_bars` | 120 | hold (M1 ⇒ 120-min, where the edge concentrates) |

Timeframe: M1 (matches the research).

## Testing (`tests/test_velocity_breakout.py`)

- Pre-range computed from `[arm−pre, arm)` only (a later-day extreme must not move it).
- Break detection: first close beyond the pre-range within `[arm, arm+arm_min)`;
  correct direction (above → long, below → short).
- **Velocity gate:** a churn break (ER `< er_min`) is rejected (no position); a clean
  break (ER `≥ er_min`) is taken. `push_min` gate when enabled.
- No entry outside the arm windows; at most one entry per arm per UTC day.
- Hold/time-exit flattens at `hold_bars`.
- State resets across UTC days.
- No-lookahead: `signal[t]` depends only on bars ≤ t.

A `run_backtest` check on `data/real` XAUUSD (sanity, not a unit test) to confirm it
reproduces the diagnostic's sign/scale (positive, US-window-concentrated).

## Validation plan (gates before any promotion — `docs/WORKFLOW.md` Stage 3)

The diagnostic is the prior, not a pass. Before this leaves backtest-only it must clear:

- **Week-by-week / walk-forward:** positive in a majority of weeks (event_breakout
  was 4/5; hold this bar).
- **Cost stress 2–4bp:** event-time fills are wider than `spread_mean`; the edge must
  survive realistic slippage, not the 0.73bp diagnostic hurdle.
- **Param wiggle ±25%** on `er_min` and `hold_bars`: a plateau, not a hot cell.
- **Null test:** time-shuffled same-hour, same-direction entries ≈ 0bp (the edge is
  velocity-specific, not US-session drift) — the `asian_sweep` null-test pattern.
- **Forward OOS:** re-run on the competition dump (Jun 15–27) for unseen US-session
  events; this is the real out-of-sample.

## Backtest result (sanity, 2026-06-18)

`run_backtest --strategy velocity_breakout --symbol XAUUSD --data data/real --every 1m`
(full month, defaults from the design):

| slippage | total_return | sharpe | maxDD | hit | turnover | in-market |
|---|---|---|---|---|---|---|
| 0.5bp (default) | **+3.21%** | 5.53 | −1.97% | 49.0% | 28 | 1666/31613 (~5.3%) |
| **2bp (cost-stress)** | **+2.78%** | 4.79 | −2.14% | 49.0% | 28 | same |

The edge **survives 2bp** (+3.21% → +2.78%): turnover is only 28 (≈1–1.5 round-trips/day
across two arms, 120-bar hold), so it is cost-insensitive — the opposite of a scalper.
Hit 49% with positive return = the edge lives in the right tail / the hold, as designed.

**Not a gate, and flattered:** one in-sample month (~28 trades, overlapping nothing but
still thin), Sharpe ~5 is not believable forward. Stays backtest-only pending the
validation-plan gates above and forward comp-data (Jun 15–27) OOS.

### Forward OOS update (Jun 16–18, comp ticks pulled from VPS, 2bp)

First live-comp days. **One** qualifying trade: Jun 16 12:31 UTC LONG @ 4347.09 (12:30
arm, clean break passed the ER gate) → exit 14:31 @ 4318.52 = **−0.70%**. Jun 17–18:
**no** qualifying clean breaks at the arm windows. Full-span return falls +2.78% →
**+2.06%** with the OOS day folded in.

n=1 — statistically meaningless, but it is a loss, not a confirm: the continuation-long
was run over by the Jun 16→18 gold downtrend (same regime that gave back the live
portfolio at the 22:00 reopen). The strategy has **no directional/regime filter** — it
takes clean breaks either way in the US window, so longs fail in a down-regime. Flagged
as the first thing to study before promotion (a trend-alignment gate on the break
direction). Verdict unchanged: **backtest-only**, needs many more comp days.

## Out of scope (YAGNI)

- Not in `portfolio.yaml`; no live or paper deployment (gated on forward validation).
- **Momentum-indicator leg dropped for v1** — the sample is too thin to AND-stack a
  third filter onto a ~16-trade/mo base; revisit only if events allow.
- **Volume gate excluded** (study A: high-volume breaks fade).
- No default AND-stacking of `push_min` with `er_min` (single ER gate; push is an
  optional swept lever).
- Gold-only — every validated edge here is gold-specific.
- No changes to `session_vol` / `LondonBreakout` or the `event_breakout` research.
