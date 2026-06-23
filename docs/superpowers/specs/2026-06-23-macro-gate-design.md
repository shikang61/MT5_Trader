# Spec: Macro-direction gate on the trend books (②)

Status: design (backtest-only). Author: QR. Date: 2026-06-23.

## Goal & hypothesis

The directional trend books — `donchian` (DonchianBreakout) and `london_orb`
(LondonBreakout) — bleed when they trade **against the macro USD trend**. A slow
EURUSD trend acts as a **directional veto**: zero out a book's position whenever it
disagrees with the macro direction, keep it when it agrees. Expected win is
**lower max-drawdown + lower turnover on an existing edge** (cut counter-macro
losers), NOT new alpha.

This is a FILTER on existing books, not a new standalone signal — deliberately
unlike the dead M1 EURUSD→gold lead-lag ([[usd-leadlag-dead]]), which added
turnover and died on cost. The gate adds **zero** turnover (it only ever removes
trades) and uses a SLOW (hourly) trend, a different hypothesis from that M1 study.

Backtest-only. Touches no live config until it clears the bar below.

## Macro signal: slow EURUSD trend

On the same M1 bars (EURUSD joined onto gold via `attach_eur`, giving `eur_close`):

```
eur_ema   = ewm_mean(eur_close, span=trend_span)         # slow EURUSD EMA
eur_slope = eur_ema / eur_ema.shift(slope_lag) - 1       # fractional slope over slope_lag bars
macro_dir = +1 if eur_slope >  band      # EURUSD up   -> USD down -> gold-supportive (permit longs)
            -1 if eur_slope < -band       # EURUSD down -> USD up   -> gold-bearish    (permit shorts)
             0 otherwise                  # neutral band -> no veto (permit both)
```

- `trend_span` (EMA span, bars) and `slope_lag` are SLOW (hours): e.g. span ~240–1440
  M1 bars (4h–1d), slope over ~60–240 bars. Swept.
- `band` (neutral dead-zone, fractional slope): a small |slope| means "no clear USD
  trend" → do not veto either side (avoids whipsaw at flat USD). Swept incl. 0.
- Causal: EMA and a backward `.shift(+lag)` only — no lookahead.

## Gate mechanism (pure function, overlay)

```
macro_gate(book_signal: np.ndarray, macro_dir: np.ndarray) -> np.ndarray
```

- `macro_dir == +1` (gold-supportive): keep longs (`book_signal > 0`), VETO shorts → 0.
- `macro_dir == -1` (gold-bearish): keep shorts, VETO longs → 0.
- `macro_dir == 0` (neutral): keep the book signal unchanged (no veto).
- Element-wise, same length as `book_signal`. Never opens a position the book did
  not ask for, never flips a sign — it can only zero a disagreeing position. So the
  gated book's turnover ≤ the ungated book's.

Applied as a post-process to each trend book's `signal(bars)` array. No change to
the strategy classes themselves; the gate is a standalone, independently tested
function.

## Validation (backtest-only) — the bar this must clear

The key comparison is **gated vs the UNGATED baseline of the same book** (does the
filter improve the book it wraps?):

1. **Improvement**: for donchian AND london_orb, gated must beat ungated on a
   param plateau (`trend_span × slope_lag × band`) at **4× cost**, with **lower
   max-drawdown AND lower turnover** (the stated win). A return improvement alone
   is not enough — DD/turnover must also improve, else the gate is just luck.
2. **Placebo (not a null permutation)**: a RANDOM veto that removes the same
   *number* of trades as the macro veto must NOT match the macro veto's
   improvement (≥ ~100 random draws; macro must beat the random-veto distribution,
   p < 0.05). Proves the edge is the macro signal, not merely "trading less."
3. **Plateau, not a spike**: the improving region must be contiguous across
   neighbouring `trend_span`/`band` — robustness, not one lucky cell.

Pass all three on BOTH books → propose live wiring (separate change: a runner
overlay, its own review). Fail → document and shelve like the lead-lag, do not
deploy.

## Framework fit

- New module `src/mt5_trader/strategies/macro_gate.py` with `macro_signal(bars,
  trend_span, slope_lag, band) -> np.ndarray` (the `macro_dir` series) and
  `macro_gate(book_signal, macro_dir) -> np.ndarray` (the veto). Pure functions,
  unit-tested in isolation.
- Reuse `strategies.usd_leadlag.attach_eur` to put `eur_close` on gold bars (already
  built + tested).
- New `scripts/research_macro_gate.py`: builds gold M1 bars + eur, runs donchian
  and london_orb signals, applies the gate, and reports gated-vs-ungated +
  placebo via the existing `backtest/engine.run` + `metrics.summary`.
- Data: `data/real` (XAUUSD + EURUSD, same source/clock — already ingested).

## Out of scope (future)

- **Live wiring** (a `PortfolioRunner` per-book overlay) — only if it passes, its
  own spec/review.
- Synthetic-DXY basket macro signal (broader USD); soft-scale (size by agreement
  instead of veto); real-yields signal; gating the fade books (no directional
  macro thesis).

## Open questions / risks

- **One in-sample month** (May11–Jun10, same window as everything else) — the gate
  may overfit; the plateau + placebo guard against it but OOS confirmation is a
  later step.
- **Trend-regime dependence**: in a month where gold and USD decouple (risk-on/off),
  the macro veto could remove *winners*. The gated-vs-ungated DD comparison will
  expose this.
- **Neutral band tuning**: too wide → gate never fires (≈ ungated); too narrow →
  whipsaw vetoes. The sweep finds the plateau; band=0 (always veto) is included as
  a reference.
