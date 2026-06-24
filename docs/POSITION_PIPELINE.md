# Position Pipeline — how books become one live order

End-to-end walkthrough of how the portfolio runner turns several strategy signals
into a single broker position each cycle. Source of truth: `PortfolioRunner.step`
and `Book.targets` in `src/mt5_trader/live/runner.py`; risk in
`src/mt5_trader/risk/manager.py` + `config/risk.yaml`; books in
`config/portfolio.yaml`.

## The books (one process, one netted position, magic 1001)

`scripts/run_portfolio.py` runs every book in `config/portfolio.yaml` in one loop.
All books on a symbol share **one netted position** tagged **magic 1001**.

| book | strategy | TF | weight | role |
|---|---|---|---|---|
| DonchianBreakout | `donchian` | M5 | 0.35 | trend anchor |
| AsianSweepFade | `asian_sweep` | M1 | 0.20 | Asian H/L fade |
| VwapTrend | `vwap_trend` | M5 | 0.15 | trend (regime-gated) |
| SweepFade | `sweep_fade` | M1 | 0.15 | PDH/PDL fade |
| RatioMeanRev | `ratio_mr` | M5 | 0.10 | XAU off XAU/XAG z-score |
| LondonBreakout | `london_orb` | M1 | 0.05 | opening-range breakout |

Weights are a share of the risk budget; they sum to ~1. (The `passive_consolidation`
book is a **separate** process at **magic 2003** — NOT part of this net.)

## One cycle, step by step

### 1. Per book → a target (`Book.targets`)
Each book, on the latest **closed** bar at its timeframe (no lookahead; one-bar
execution delay — same convention as the backtester):

1. **Signal:** `sig = strategy.signal(closed)[-1]` ∈ [−1, 1] (mostly discrete −1/0/+1).
2. **Vol-target × weight:** `frac = risk.size(sig, realized_vol, now) × weight`, where
   `risk.size = sig × min(target_vol / realized_vol, max_leverage)`
   (`target_vol=0.30`, `max_leverage=2`). Size scales **up** in quiet vol, **down**
   in high vol, then by the book's weight.
3. **Regime gate** (opt-in; `vwap_trend`, `ratio_mr`): zero the position when the
   efficiency-ratio regime is RANGE (`features/regime.py`).
4. **Lots:** single leg → `lots = frac × equity / (contract_size × price)`; pair
   (`ratio_mr`) → `pair_leg_lots`. Cached between bars; recomputed only on a new bar.

### 2. Combine → net target per symbol
`net[sym] = Σ each book's lots`. Same-direction books **stack**; opposing books
**cancel** — this offsetting *is* the portfolio's diversification (the blended book
is far smoother than any single book). XAG only feeds `ratio_mr`'s signal; it is
not traded (`trade_symbol2: false`).

### 3. Risk overlays on the NET (`PortfolioRunner.step`)
Applied per symbol to the net target, in order:

- **Kill switch** (`RiskManager.kill`): if the day's drawdown breaches
  `loss_limit` (4% from day-start), flatten and stand down flat for the rest of the
  UTC day (auto-resume next day). `give_back` (trailing-from-peak kill) exists but is
  off (0.0).
- **Event blackout** (`RiskManager.in_blackout`): force flat around high-impact
  calendar events — enabled in config but inert until `data/calendar/events.parquet`
  is populated + cleared by `scripts/eval_blackout.py`.
- **Fast-vol halt** (`vol_halt`): force flat on an abnormal bar — off (tested, churns).
- **Exposure cap** (`cap_lots`): clamp `|net|` to `exposure_cap = 0.5` × equity
  leverage, so aligned books can't stack past the cap.
- **Disaster stop** (`chandelier_sl`): one trailing stop on the net at
  `extreme ∓ 8×ATR` (ATR on the **coarsest** book TF for that symbol = M5).
  Detached across the 21:58–22:03 UTC reopen window (`reopen_guard`).

### 4. Reconcile → trade the delta (`gateway.reconcile`)
`reconcile(sym, net_lots, sl, tp, magic=1001)` trades only the **difference**
between the current broker position and the target, then attaches the trailing
stop. Restarts are idempotent (it re-derives the delta), so manual intervention is
safe. A `STEP` event (books, fills, equity, per-symbol `error`) is logged to
`logs/portfolio.jsonl` — the audit source of truth.

## Worked example

donchian `+1`, asian `−1`, others flat; realized_vol ~0.22 → leverage
`min(0.30/0.22, 2) = 1.36`:

```
donchian frac = +1 × 1.36 × 0.35 = +0.476
asian    frac = −1 × 1.36 × 0.20 = −0.272
net frac      = +0.204            → small net LONG
```
→ under the 0.5 cap (fine) → `lots = 0.204 × equity / (100 × price)` → reconcile the
delta → one long XAUUSD position carrying the 8×ATR trailing stop.

## What is NOT in the live path

Several researched overlays are coded but **default-off** (no behavior change) and
were shelved at the backtest gate — scale-out (`stops.scaleout_remaining` + engine),
profit-ratchet, breakeven, CUSUM regime gate. The live position is exactly: **netted,
vol-targeted, weight-scaled book signals, capped at 0.5×, on a trailing 8×ATR
disaster stop.** See the `profit-ratchet-dead` findings: per-book overlays wash out
in the netted book — validate any new overlay on the **net**.

## See also
`docs/ARCHITECTURE.md` (two-box design), `config/portfolio.yaml` (book weights +
rationale), `config/risk.yaml` (posture), `src/mt5_trader/live/README.md`,
specs `2026-06-18-net-exposure-cap`, `2026-06-17-standing-risk-posture`,
`2026-06-19-reopen-stop-guard`.
