# Strategy Playbook

Candidate strategies for Model to Market (Jun 21–24, 2026). All are implemented
in `src/mt5_trader/strategies/` and runnable via `scripts/run_backtest.py`.
Parameters below are starting points — calibrate on the historical dump and
paper trading before trusting any of them.

## Tournament context (read first)

Ranking = **account equity at the end of each round**. Three elimination rounds:
top 200 → 150 → 100. This is not a Sharpe contest until the Finals:

- **Rounds 1–2 (survive):** You don't need to be top 10, you need to be top
  200/150. Most entrants either (a) do nothing and stay flat, or (b) blow up on
  max leverage. A steady +1–3% with a hard stop likely clears the cutoff.
  Config: `target_vol: 0.25`, `max_leverage: 3`, kill at −5%.
- **Round 3 (push):** Top 100 advance; finals seeding may matter. Vol budget
  doubles (`0.50`, lev 6, kill at −10%). If mid-round equity is comfortably
  above the likely cutoff, *reduce* risk and lock the seat — a seat in the
  Finals is worth more than rank 40 vs 60.
- **Edge case:** if near the cutoff with hours left, variance is your friend —
  a 50/50 coin flip with leverage beats certain elimination. This is a manual
  decision, not coded.

## 1. London Open Range Breakout — Gold (`london_orb`)

**Idea:** Gold volatility clusters at the London open. The 06:00–07:00 UTC
range defines the day's battleground; a clean break tends to extend through
the London morning and NY overlap.

- Entry: first close outside the 06:00–07:00 UTC high/low, between 07:00–16:00.
- Exit: flat at 16:00 UTC (or opposite-side break = stop).
- Validate on the dump: distribution of (day range) / (opening range) for
  XAUUSD; win rate of first-break direction.
- **Role:** Rounds 1–2 workhorse. One decision per day, bounded loss, easy to
  supervise manually.

## 2. Donchian Trend — Gold + majors (`donchian`)

- Entry: close breaks 4h (240×M1) high/low **and** agrees with the 1-day EMA.
- Exit: close breaks the opposite 1h extreme.
- **Role:** Round 3 push vehicle. Trend strategies have fat right tails —
  exactly what you want when you need to climb a leaderboard. Run on XAUUSD +
  one FX major with the round-3 leverage budget.

## 3. Order-Flow Imbalance Momentum (`ofi`)

**Idea:** The competition hands out 5-level depth that most entrants will
ignore. OFI (Cont–Kukanov–Stoikov) measures net add/cancel pressure at the
touch; spikes predict short-horizon drift.

- Signal: per-bar sum of tick OFI, z-scored over 4h; enter on |z| ≥ 2 in the
  spike's direction, hold ~30 min.
- **Caveat:** this is a *simulated* book. First research task on the dump:
  does OFI actually predict 1–30 min returns here? If yes, this may be the
  biggest edge in the field. If the sim book is synthetic noise, drop it.
- **Role:** deploy only after the dump validates it. Also the best story for
  the **Best Technical Architecture** prize.

## 4. Gold/Silver Ratio Mean Reversion (`ratio_mr`)

- Spread: log(XAUUSD) − β·log(XAGUSD), β from a regression on the dump (start β=1).
- Entry: |z| ≥ 2 on a 1-day window, exit |z| ≤ 0.5. Long spread = long gold /
  short silver (live runner must place both legs).
- **Role:** low-correlation ballast in Rounds 1–2 and the natural **Sharpe
  prize** candidate. Market-neutral → survives direction shocks that kill
  the leveraged crowd.

## 5. FX Triangle Consistency (research-first)

EURUSD × EURGBP⁻¹ vs GBPUSD must be consistent in a real market. A simulated
environment may not enforce it. Check on the dump:
`spread_bars(EURUSD_bars, synthetic_from(GBPUSD, EURGBP))` deviation z-scores.
If persistent misalignments exist → near-arbitrage, the safest money in the
competition. If not, costs you one research hour.

## 6. Regime Switch — Gold (`regime_switch`)

**Idea:** classify gold into trend-up / trend-down / range via Kaufman
efficiency ratio (computed on 15m-coarsened closes — M1 noise kills ER;
empirically max ER(240×M1) was 0.28 on a month of real ticks while 15m ER
spreads properly). Ride trends at full size; scalp the range.

**Empirical status (May 12 – Jun 11 2026 Dukascopy XAUUSD ticks):**
- Detector: 10% trend-up / 74% range / 17% trend-down — plausible.
- Trend leg: ~breakeven-positive (+0.2%, Sharpe 0.3). Needs a better month
  or parameter work before earning a portfolio weight.
- Fade (range-scalp) leg as a *taker*: **−3.4% to −5.8% after costs**, with
  and without stops, turnover ~350. Dead as implemented — costs eat it.
- **Open research idea:** the same scalp as a *maker* (resting limit orders
  at the range edges, collecting spread instead of paying it) flips the cost
  sign. Requires a passive-fill backtest against the competition dump's
  5-level depth. Gateway primitives (`limit_order`, `cancel_pending`)
  already exist. Gate: only deploy if the fill-model backtest survives
  pessimistic queue assumptions.

## 7. VWAP Stretch Continuation — Gold (`vwap_trend`)

**Idea:** price 2σ beyond the session VWAP (tick-count weighted) *continues*
rather than reverts on gold — found by inverting a failed VWAP-reversion
study. Enter with the stretch after the first 2h of the session, exit when
|z| ≤ 0.5 or session end.

**Evidence (May 12 – Jun 11 2026 ticks):** event study +3.5 to +5.6bp/trade
after costs, 70 deduped episodes, positive 4 of 5 weeks; full engine
backtest **+3.5%/month, Sharpe 3.3, max DD −4.7%, turnover 85**. Caveats:
parameters chosen on the same month (in-sample risk); re-validate on the
competition dump and paper fills before sizing it. Candidate for the
Round 1–2 book alongside london_orb.

**Volume studies that failed (same data):** activity-conditioned breakout
continuation (breakouts faded regardless of volume), L1 quote-imbalance
direction (Dukascopy sizes carry no signal). FX triangle on real data:
consistent (the only "deviations" were stale weekend-open quotes) — rerun
`scripts/research_hf.py triangle` against the sim feed in paper week.

## 8. Cross-asset results (May 12 – Jun 11 2026)

Both validated gold strategies are **gold-specific**: on EURUSD/GBPUSD,
london_orb ≈ −0.9% and vwap_trend −0.1 to −2.5%. FX majors are too
efficient/mean-reverting for these mechanics — do not deploy them there.

**BTC (Binance 1m klines, synthetic 2bp spread):** london_orb +0.4% but
Sharpe 0.3 with −10% DD; vwap_trend/donchian/regime_switch all negative.
No crypto deployment on current evidence.

**Open crypto lead: taker-flow following.** Volume-weighted taker-buy
imbalance (real flow — crypto has it, FX doesn't), z>2 vs 1-day baseline,
4h horizon: +17bp/trade, 57% hit, positive 4/5 weeks. Caveats: threshold
cliff at z>1.5 (+1.3bp) fails the wiggle gate; n=95; and live use needs a
Binance feed beside MT5 (taker flow isn't in MT5 data). Revisit in prep
week if the main book is stable.

## Portfolio per round (initial plan)

| Round | Book | Vol budget |
|---|---|---|
| 1 (top 200) | london_orb (XAUUSD) + ratio_mr (XAU/XAG) | 0.25 ann. |
| 2 (top 150) | same, plus ofi if validated | 0.25 |
| 3 (top 100) | donchian (XAUUSD + 1 FX major) at lev 6, keep ratio_mr | 0.50 |

## Research checklist on the historical dump

1. Cost reality: median/percentile spread by hour and symbol → set
   `slippage_bps` honestly. Costs kill more competition strategies than ideas do.
2. OFI predictive power: corr(bar OFI, next 1/5/30-min return) per symbol.
3. Opening-range stats for XAUUSD (idea 1 validation).
4. Gold/silver β + spread half-life (idea 4 viability: half-life ≪ round length).
5. FX triangle deviations (idea 5 go/no-go).
6. Walk-forward: fit params on weeks 1–3, test on week 4. Anything that only
   works in-sample, cut.
