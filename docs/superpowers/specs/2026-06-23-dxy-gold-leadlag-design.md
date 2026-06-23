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

## Data ingestion (RESOLVED for in-sample; OOS is the remaining gap)

A local cross-asset tick dump exists: **`/Volumes/SK Storage-1/Trading/2026-05-11_2026-06-10`**
(~20 GB) — the **same source/clock as the gold data**, covering the in-sample
window. Contains EURUSD (27 days, ~1.08M ticks/day) **and XAUUSD itself**, plus the
full FX-majors + gold-cross universe, with **L2 depth** (vendor schema: `time` str,
`sym`, `bid`/`ask`, `bidprices`/`bidsizes`/`askprices`/`asksizes` lists). This is
the canonical L2 source the schema (`schema.py`, depth cols) was built for.

1. **Ingest EURUSD + XAUUSD from this dump** — using *both* from the same dump
   gives clean lead-lag alignment (**no cross-feed clock skew — critical for a
   lead-lag study**). Reuse the vendor→canonical pipeline (`normalise` +
   `config/schema_map.yaml`), adding a parquet reader + L2-list→depth-column
   explosion. Output canonical parquet (CORE_COLUMNS + depth).
2. **Build M1 bars** for both; join EURUSD close onto the gold bar frame on `ts`.
   Apply `drop_market_closed` (both share ~24/5 hours).
3. **Verify `time` is UTC** — cross-check the dump's own XAUUSD against `data/real`
   (canonical) to confirm tz/clock before trusting alignment.

**Scope note — OOS/live is the real remaining gap.** The dump covers the
**in-sample window only** (2026-05-11→06-10). For out-of-sample / live (06-16+),
capture EURUSD from the comp feed (the live MT5 broker likely quotes it — add to
`log_ticks`). Validate in-sample first; OOS is the confirmation step.

**Universe bonus:** the dump also has GBPUSD/USDJPY/AUDUSD/NZDUSD/USDCAD/USDCHF/
USDCNH/USDHKD + gold crosses → a **synthetic DXY basket** USD signal and the ②/③
ideas all become backtestable on real, same-source, L2 data later.

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
- **Ingestion of the vendor parquet** (L2-list explosion + UTC verification) is now
  the only data task for in-sample — small, not a blocker.
- **OOS/live EURUSD not yet captured** (06-16+) — in-sample validates the
  hypothesis; OOS confirmation needs the comp-feed capture (follow-up).
- **Cost realism** — EURUSD signal is free to compute; only gold round-trip cost
  applies (taker). Slippage on gold market entries must be in the 4× stress.
