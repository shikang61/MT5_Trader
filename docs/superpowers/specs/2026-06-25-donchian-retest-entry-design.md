# Donchian@M5 retest-entry — design

**Date:** 2026-06-25
**Status:** approved (design); implementation not started
**Scope:** `DonchianBreakout` (`donchian`) on XAUUSD@M5 only. Backtest-first.

## Problem

Live observation: Donchian entries are "misplaced" — the breakout signal fires
when `close` crosses the channel edge, and the live market order then fills even
further past that edge (full spread + one M5 bar of latency in `reconcile` →
`market_order`). The strategy chases the move and enters at the extreme.

This is an **edge change**, not an execution-cost tweak. The desired behaviour is
to enter at a *better* price by waiting for a pullback (retest) to the broken
channel edge instead of buying the breakout top.

## Central risk (must be measured, not assumed)

Buy-the-retest can **invert a breakout edge**. The strongest breakouts never pull
back — they run. A retest filter systematically keeps the *weak* breakouts (that
retrace) and drops the *strong* ones (that don't). This frequently turns a
positive trend-follower negative. The whole point of the backtest-first plan is
to quantify the trades we *don't* take and prove the change helps the **net**
book before any live order machinery is built.

## Architectural constraint

The live runner nets all 6 books into **one** position per symbol and sends a
single market order via `reconcile` (`mt5_gateway.py:319`). There is no per-book
entry price today. True per-book resting limits (own magic, like
`passive_limits.py`) would end netting and hold gross legs simultaneously. We do
**not** build that yet — validation does not need live limits, only a backtest
model of them.

## Modeling approach

Chosen: **A — dedicated retest-fill simulator**, a sibling to
`engine._simulate_stops`. That function already walks bars and uses high/low to
detect an intrabar stop touch; the retest entry is its mirror (detect whether a
resting limit at the anchor filled).

Rejected:
- **B — tick replay** on `data/ticks` (~5wk): most honest fills, but heavier,
  tiny sample, new harness. Kept as an optional *follow-up confirmation* only if
  A survives Stage 2.
- **C — delayed position series, fill at fill-bar close**: simplest, but enters
  at the bar close (worse than the level `L`), so it cannot measure the entry
  improvement under test. Rejected.

## Core model (A)

- **Anchor `L`** = the broken channel edge captured *at the breakout bar*:
  `entry_hi` for a long, `entry_lo` for a short. `DonchianBreakout` already
  computes these (`momentum_breakout.py:28-30`); expose them alongside `signal()`
  (e.g. an `anchors(bars)` helper) without changing `signal()`'s output.
- **Arm** on the breakout bar (the bar where state goes 0→±1). Price has already
  crossed `L`, so a buy-limit rests correctly below market (sell-limit above).
  `L` is held **fixed** during the wait (the original broken level), not
  re-levelled as the channel drifts.
- **Fill** on the first bar `t+k` (k≥1, causal — never the breakout bar itself)
  whose `low ≤ L` (long) / `high ≥ L` (short). Entry price = `L`. Position is
  held from that bar to the normal Donchian exit.
- **Exits unchanged** — still the existing Donchian signal exits
  (`close < exit_lo` / `close > exit_hi`), modelled market-at-close as today.
  Only the **entry** becomes a limit.
- **Cancel / skip** if the Donchian exit fires before a fill (the trade is
  abandoned — this is a runaway breakout we choose not to chase). Donchian cannot
  flip ±1→∓1 directly (it returns to 0 via the exit channel first), so no
  mid-wait side flip to handle.
- **Timeout** at `max_wait` bars — policy swept (below).

### Parameters (swept in validation)
- `max_wait` ∈ {12, 24, 48} M5 bars (≈ 1h / 2h / 4h).
- `timeout_policy` ∈ {`skip`, `market_fallback`}:
  - `skip`: cancel, never enter (pure retest).
  - `market_fallback`: at `max_wait`, enter at market (chase as last resort).
- `entry_offset_bp`: default 0 (limit exactly at `L`). Reserved for a later
  buffer sweep; not a first-cut variable.

### Implementation seam
The simulator must price the fill bar from `L` to that bar's close (not
close-to-close), so it either (a) returns `(pos, entry_override)` consumed by a
small change in `engine.run`, or (b) returns `ret`/`turnover` directly like
`_simulate_stops` and reuses the rest of `run`. The exact seam is left to the
implementation plan; the requirement is fixed: **entry priced at `L`, exits and
cost model unchanged, the existing `validate` pipeline reused unmodified.**

## Validation (two stages — the netting lesson)

**Stage 1 — standalone Donchian@M5, retest vs baseline.** Report per variant:
`n_trades`, `n_filled`, **`n_skipped`** (armed but never retested within
`max_wait`), avg **entry-improvement (bp)** vs the baseline market entry, Sharpe,
total return, max DD, win rate, avg hold. The `n_skipped` × their forward PnL
directly exposes the inversion risk: if the skipped trades were the winners, the
edge degrades.

**Stage 2 — net portfolio.** Swap donchian-retest into the all-6-book net
backtest and compare net metrics (Sharpe, return, DD, turnover) against the
current net. **This is the gate that matters** — per prior findings, standalone
book gains wash out in the netted portfolio (net is already ~Sharpe-9; a single
book rarely moves net DD).

### Honesty caveats (stated in the report, not hidden)
- **Optimistic fill**: `low ≤ L ⇒ assumed filled at `L``. Real fills depend on
  queue position and wick depth. Confirmable later via tick replay (Approach B).
- **Sample**: ~5wk of real M5 data. Donchian already fails deflated-Sharpe on a
  5wk sample (see prior adoption note). This is **directional evidence, not a
  deploy gate.**

## Out of scope (explicitly)

- Live runner / `reconcile` / per-book limit orders — built **only if Stage 2
  survives**, in a separate follow-up.
- Other 5 books. The fade books already enter at levels; retest is a breakout fix.
- `entry_offset_bp` buffer sweep.

## Success criteria

1. `_simulate_retest_entry` produces a causal, no-lookahead position series with
   entry priced at `L`, covered by unit tests (fill on touch, skip on no-touch,
   skip on pre-fill exit, both timeout policies).
2. Stage 1 report generated for the full `max_wait` × policy sweep with all
   metrics above.
3. Stage 2 net-portfolio comparison generated.
4. A written recommendation: does retest entry improve the net book? If not,
   stop here — do not build live machinery.

## Findings

### Stage 1 — standalone Donchian@M5 sweep (Task 4)

| config | n_trades | n_filled | n_skipped | entry-impr (bp) | standalone Sharpe |
|--------|----------|----------|-----------|-----------------|-------------------|
| baseline (market) | 22 | 22 | 0 | — | 1.89 |
| mw12 / skip | 22 | 17 | 5 | ~+11 bp | **2.51** |
| mw24 / skip | 22 | 15 | 7 | ~+11 bp | ~2.1 |
| mw48 / skip | 22 | 12 | 10 | ~+11 bp | <1.9 |
| any / market_fallback | 22 | 22 | 0 | ~0/negative | diluted |

Key takeaways:
- `skip` policy with `max_wait=12` (mw12/skip) is the standalone winner: +11 bp entry improvement, Sharpe 2.51 vs baseline 1.89.
- The entry improvement is real for filled trades, but the 5 dropped trades (the runaway breakouts that never pull back) carry significant forward PnL — they are the strong breakouts.
- `market_fallback` dilutes the entry gain to ~0 or negative: chasing the market at timeout undoes the limit benefit.
- As `max_wait` grows, more winners are dropped (higher `n_skipped`), and standalone Sharpe declines.

### Stage 2 — net 6-book portfolio comparison (Task 5)

Script: `scripts/research_donchian_retest_net.py` (mw12/skip, exit_n=110, ~5wk real feed)

```
net (donchian baseline): ret +0.0748  sharpe 7.63  maxDD -0.0210  turn 67
net (donchian_retest)  : ret +0.0524  sharpe 5.85  maxDD -0.0221  turn 61
```

Net Sharpe **drops 7.63 → 5.85** (−23 %). Net return **drops +7.5% → +5.2%** (−30 %). Net maxDD is **marginally worse** (−0.0210 → −0.0221). Turnover falls slightly (67 → 61) due to fewer entries.

### 6-gate validate_candidate (donchian_retest, mw12/skip, exit_n=110, n_trials=6)

```
  week_by_week     PASS  {'pos_frac': 0.667, 'n_weeks': 6}
  cost_stress      PASS  {'ret_4x': 0.0511}
  turnover         PASS  {'per_bar': 0.0046}
  walk_forward     PASS  {'pos_frac': 0.6, 'mean_ret': 0.0112, 'folds': [0.0157, -0.0005, 0.0209, -0.0042, 0.0242]}
  deflated_sharpe  FAIL  {'raw': 0.0078, 'deflated': -0.0143, 'n_trials': 6}
  OVERALL: FAIL
```

`deflated_sharpe` fails as expected on a 5wk sample (documented caveat — not a blocker by itself). However, the standalone validate is beside the point: the net result is the gate that matters.

### Verdict: STOP HERE — do not build live retest machinery

The net portfolio test is definitive: swapping donchian → donchian_retest (best Stage-1 config, mw12/skip) **degrades** the net book on every metric that matters — return, Sharpe, and maxDD. This confirms the "inversion risk" stated in the design:

> The strongest breakouts never pull back — they run. A retest filter systematically keeps the weak breakouts (that retrace) and drops the strong ones (that don't).

The 5 skipped trades in Stage 1 are the strongest breakouts; removing them from the net book removes the bulk of the donchian contribution. The standalone Sharpe improvement (1.89 → 2.51) is precisely because the filtered-out trades are the loss-makers in isolation — but those same trades produce large correlated moves that anchor the net book's aggregate returns.

**Action: do not build live per-book limit-order machinery for donchian retest. The edge does not survive netting.** Close branch feat/donchian-retest-entry as research-only. Revisit only if net book composition changes substantially (e.g. donchian weight reduced to the point where a single-book improvement could matter).
