# AsianSweepFade — Design

**Date:** 2026-06-16
**Status:** Design approved; pending implementation plan
**Type:** Backtest-only strategy (registered + tested; NOT in `portfolio.yaml`)

## Motivation

Liquidity-pool research on the competition depth dump (`data/real`, 2026-05-11→06-10,
XAUUSD 1m bars) tested sweep-and-reclaim reversals at several level sources. Only two
pay on gold after costs:

- **Prior-day high/low** — already live as `sweep_fade` (taker reclaim-fade,
  +6.5–7.5bp at 15–30m).
- **Asian-session high/low** — the one *new* source that survives. Fade the reclaim
  of the 00:00–06:00 UTC range during the London session.

Asian H/L evidence (full month):
- Event study: fade 60m +10.7bp (q60,c5), +13.3bp (q90,c5), beats the realistic
  ~4.65bp gold cost hurdle across the whole quiet/confirm grid (a plateau, not a hot
  cell). Hit ~61%, reclaim ~62%.
- Null test (5000 draws): time-shuffle (random same-hour, same-direction London
  entries) null ≈ 0bp, real +10.7 → **p=0.005–0.015, sweep-specific, not session
  drift.** Sign-shuffle borderline (p≈0.06) — underpowered at n≈25.
- **Caveats:** one month, n≈25–28, edge lives only at the **60-minute** horizon
  (15/30m below cost). Underpowered. Hence backtest-only until more data / forward
  comp validation.

Horizon contrast with PDH/PDL: PDH/PDL is a fast snapback (15–30m, decays by 60m);
Asian is a slow reversal that plays out over the London hour (60m).

## Approach (chosen: A — new standalone class)

A new `AsianSweepFade` strategy in its own file, mirroring `sweep_fade.py`. One
strategy per file is the repo pattern (`fast_meanrev`, `vwap_trend`, etc.).

Rejected:
- **B (generalize `sweep_fade` with a level-source param):** modifies a live,
  validated strategy for a backtest-only experiment — fails surgical-changes.
  `LESSONS.md #9`: the registry test once caught `vwap_trend` crashing on live bars;
  do not risk the live book.
- **C (extract a shared sweep helper now):** refactors live code internals before a
  third variant exists — YAGNI.

## Component

`src/mt5_trader/strategies/asian_sweep.py`

- `@register` dataclass `AsianSweepFade`, `name = "asian_sweep"`, conforming to the
  `Strategy` protocol: `signal(bars: pl.DataFrame) -> np.ndarray` returning the target
  position in [-1, 1] per bar (same contract as `SweepFade`).
- Registered → picked up by the backtest registry, `validate_books`, and
  `test_strategies`. **Not** added to `portfolio.yaml` (no live sizing).

## Signal logic (per UTC day)

1. **Asian H/L** = high/low of bars with `hour ∈ [0, session_end_hour)` (00:00–06:00
   UTC). Computed causally — the window is complete before any trade bar.
2. **Trade window:** only act on bars at `hour ≥ session_end_hour` of the *same* day.
   State resets each UTC day.
3. **Sweep + reclaim** (same machine as `sweep_fade`): price's high crosses the Asian
   high after `quiet_bars` untouched → mark pending → classify `confirm_bars` later: if
   the close is back inside (`close < asian_high`) → **fade short**, hold `hold_bars`.
   Symmetric for the Asian low → **long**.
4. Days with no Asian window (gaps / Sunday open) → Asian H/L null → skip the day.

No-lookahead: the signal uses only data up to the current bar (the engine shifts
execution by one bar). No `.shift(-k)`.

## Parameters (defaults from the research/wiggle)

| Param | Default | Meaning |
|---|---|---|
| `session_end_hour` | 6 | Asian window is `[0, 6)` UTC; trade after |
| `quiet_bars` | 60 | bars a level must sit untouched before a cross counts |
| `confirm_bars` | 5 | bars after the sweep to classify reclaim vs hold |
| `hold_bars` | 60 | bars to hold the fade (M1 ⇒ 60-min hold, where the edge lives) |

Timeframe: M1 (matches the research and `sweep_fade`).

## Testing (`tests/test_asian_sweep.py`)

- Asian H/L computed from the 00:00–06:00 window only (a later-day high above the
  Asian high must not change the level).
- Sweep → reclaim → correct fade sign (high reclaim ⇒ short; low reclaim ⇒ long).
- **No entry on a held breakout** (close still beyond the level at `confirm_bars`).
- **No entry before `session_end_hour`.**
- State resets across UTC days.
- No-lookahead: signal at bar t depends only on bars ≤ t.

Then a `run_backtest` check on `data/real` XAUUSD to confirm it reproduces the
~+10bp/60m event-study edge (sanity, not a unit test).

## Backtest result (sanity, 2026-06-16)

`run_backtest --strategy asian_sweep --symbol XAUUSD --data data/real --every 1m`
(full month, **0.5bp default slippage**): total_return +2.98%, Sharpe 7.17,
maxDD −1.72%, hit 51.6%, turnover 56, in-market ~5% (1646/31613 bars). Sign/scale
consistent with the +10bp/60m event study; low turnover matches the 60-bar hold.

**Not a gate, and flattered:** 0.5bp slippage is far below the realistic ~4.65bp
gold cost, and it is one in-sample month (n≈28). Sharpe 7 is not believable
forward. Stays backtest-only pending more months / forward comp data and a
realistic-cost re-run.

## Out of scope (YAGNI)

- Not in `portfolio.yaml`; no live or paper deployment (gated on more data).
- No parameter sweep beyond the defaults.
- Gold-only — no FX / other symbols (every validated edge here is gold-specific).
- No changes to `sweep_fade` or the shared engine.
