# Spec: EURUSD → Gold intraday lead-lag (cross-asset alpha)

Status: design (backtest-only). Author: QR. Date: 2026-06-23.

## Goal & hypothesis

Add a cross-asset alpha source that **trades XAUUSD off a USD signal**. Hypothesis:
the USD (faster, more liquid) **leads** spot gold intraday — a sharp USD move
predicts gold continuation over the next few minutes. This extends the one HF
pattern that survived our taker sweep (gold→silver lead-lag, see
[[hf-strategy-research]]) to gold's dominant macro driver.

USD proxy = **EURUSD** (most liquid FX, cleanest aligned history, ~96% of DXY).
Sign: EURUSD and gold are *both* inverse-USD → they co-move, so **gold follows
EURUSD with positive beta** (EURUSD↑ → USD↓ → gold↑).

This is backtest-only until it clears the validation bar (below). It does NOT
touch the live portfolio until then.

## Signal specification

Inputs per bar `t` (M1): gold OHLC + aligned EURUSD close.

```
r_eur(t)   = log(eur_close[t] / eur_close[t - W])     # USD-proxy move over lead window W
signal(t)  = +sign(r_eur(t))      if |r_eur(t)| > theta   else 0
position   = signal, held for H bars (time exit), taker entry on gold
```

- `+sign`: gold follows EURUSD (positive co-move via shared inverse-USD link).
- `theta`: only act on *sharp* USD moves (a lead signal, not noise). `theta = 0`
  allowed (act on every bar) — swept.
- Exit: flat after `H` bars, or flip on an opposite signal. (TP/SL deferred — the
  first cut is a pure lead-lag horizon test.)

**Swept params (the core experiment — find the lead horizon):**
- `W` (lead window): {1, 2, 3, 5, 8, 13} min
- `H` (hold): {1, 3, 5, 10, 20, 30} min
- `theta` (move gate): {0, 0.5, 1.0} × recent EURUSD bar-vol

The empirical question: does a tradeable **1–15 min** lead exist? If the lead is
sub-second it's arbed away → expect no plateau → kill it.

## Data ingestion (gating prerequisite)

1. **Pull EURUSD history** via IBKR (`get_price_history`) over the gold backtest
   windows: `data/real` span (2026-05-11 → 2026-06-10) **and** the comp/live span
   (2026-06-16 →). M1 bars (ts UTC, OHLC).
2. **Store**: `data/macro/EURUSD/<date>_<ms>.parquet`, canonical schema (ts, open,
   high, low, close). Mirrors the `data/ticks` layout.
3. **Loader**: `load_macro(symbol, ...)` (or extend `load_ticks`) — returns aligned
   M1 bars; apply the SAME `drop_market_closed` weekend filter (EURUSD and gold
   share ~24/5 hours, so alignment is clean — forward-fill any sparse gaps).
4. **Alignment**: join EURUSD close onto the gold bar frame on `ts` (M1), before
   `signal()` — see framework fit.

**Risk:** if IBKR can't deliver clean aligned EURUSD M1 history for the window,
the whole spec is blocked. Step 1 is therefore the FIRST implementation task and a
go/no-go gate.

## Framework fit

Model on the existing **`ratio_mr`** book, which already consumes a *signal-only
second series* (`symbol2=XAGUSD`, `trade_symbol2: false`) via the pair plumbing
(`PairRunner` / the symbol2 join). The cross-asset case is mechanically identical
— a second series joined to the traded symbol's bars; only the asset class differs.

- New strategy class `UsdLeadLag` (name TBD) in `strategies/`, `@register`,
  implementing `signal(bars) -> positions` where `bars` carries the joined EURUSD
  column (e.g. `eur_close`).
- Reuse the symbol2 join/runner path; no new strategy interface.
- Research–production parity preserved (same `signal()` object in backtest + live),
  per `docs/WORKFLOW.md`.

## Validation plan (the bar this must clear)

Same rigor as the live books:

1. **Param plateau**: sweep `W × H × theta`; require a *contiguous* region of
   positive after-cost return (not a lone spike) — robustness, not overfit.
2. **Null test**: break the temporal lead (circular-shift / shuffle EURUSD returns
   vs gold) → edge must vanish, **p < 0.01**. Proves lead-lag, not drift/co-trend.
3. **Cost stress**: net-positive at **4× real spread** (~0.21bp gold → ~0.84bp),
   on the plateau.

Pass all three → graduate to paper-live (its own magic), then portfolio fit
(weight, correlation to existing books). Fail → document and shelve (like the dead
HF sweep), do not deploy.

## Out of scope (future, reuse this macro data)

- **② Macro-direction gate** on the trend books (cost/quality overlay).
- **③ Cross-asset confirmation sizing** (conviction scalar).
- TP/SL exits, multi-proxy (DXY/yields/ES) ensembles, XAGUSD as a second traded leg.

## Open questions / risks

- **Lead horizon unknown** — the central bet; the sweep answers it. High chance of
  "no tradeable lead" (would be a clean negative result).
- **EURUSD↔gold beta is regime-dependent** (risk-on/off can decouple them) — a
  single static sign may underperform; note for v2.
- **IBKR data quality/alignment** for the historical window (see Data risk).
- **Cost realism** — EURUSD signal is free to compute; only gold round-trip cost
  applies (taker). Slippage on gold market entries must be in the 4× stress.
