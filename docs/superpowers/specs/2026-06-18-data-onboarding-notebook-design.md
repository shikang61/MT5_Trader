# Data Onboarding Notebook — Design

**Date:** 2026-06-18
**Status:** Design approved; light process (no TDD plan — verify via Restart & Run All)
**Type:** New onboarding notebook `notebooks/00_data_onboarding.ipynb`

## Motivation

A new researcher/dev needs to understand the two tick feeds before touching strategy
code. `data_analysis.ipynb` already explores them but is a terse *working template*;
this is a **teaching-first** read-top-to-bottom companion that explains what the feeds
are, how to load them, and their gotchas — then points to the working notebooks. The
`00_` prefix sorts it first ("start here").

## Principles

- **Thin over `mt5_trader.*`** (repo discipline, `notebooks/README.md`): use `load_ticks`,
  `build_bars`, `with_micro_features`, `time_bars` — never reimplement loaders/engine.
- **No synthetic fallback.** This notebook is about THESE feeds; if data is missing it
  prints how to get it (guard cell), it does not silently synthesize.
- **Slices, not full loads.** `load_ticks(..., start, end)` to load one day per feed —
  the full `data/real` is ~86M rows; never load it whole in a cell.
- Outputs stripped on commit by the existing nbstripout filter.

## Key facts the notebook must teach

- `data/real` = competition **depth dump**, historical **May 11 – Jun 10**, raw files carry
  the **full L2 ladder** (`bidprices[], bidsizes[], askprices[], asksizes[]`).
- `data/ticks` = **live broker logger**, **Jun 16 → now**, has `volume`, **no depth ladder**.
- `load_ticks` projects every feed to **`CORE_COLUMNS = [ts, symbol, bid, ask, bid_sz, ask_sz]`**
  before merging — so the loaded/merged view drops both `volume` and the depth ladder. To
  see depth, read a raw `data/real` parquet directly; `with_micro_features` gives
  `l1_imbalance` from the core sizes and `depth_imbalance`/`ofi` only when depth is present.
- There is a **gap Jun 11–15** (no data); weekends absent. Gold real spread **~0.21bp**;
  `data/processed` (Dukascopy) is ~1.37bp (6× wider) — the wrong feed for cost work.

## Cells (markdown narrative + small code)

1. **Orientation** — the two-feed mental model; `DEFAULT_DATA` stitches real+ticks.
2. **Data-present guard** — check `data/real/XAUUSD` and `data/ticks/XAUUSD` exist; if not,
   print how to obtain (`bash scripts/fetch_logs.sh ticks`, or the comp dump) and stop.
3. **How to load** — `load_ticks`, `build_bars`, `DEFAULT_DATA`; load one day of each feed
   via `start`/`end`. "Use loaders, not raw parquet → research-production parity."
4. **Schema tour** — side-by-side feed schemas + a column-meaning table; the `CORE_COLUMNS`
   projection lesson (depth/volume dropped on load).
5. **Coverage & gaps** — per-source date span + per-day row counts; the Jun 11–15 gap;
   a coverage bar/scatter.
6. **Spread reality** — real-feed spread distribution (~0.21bp) vs cost hurdle; intraday
   shape (session opens wide); the Dukascopy 6× warning.
7. **Ticks → bars** — `build_bars`/`time_bars`, mid-price, the `ofi` agg; a 5m close plot.
8. **Depth ladder (real only)** — read one raw `data/real` file; show `bidprices/bidsizes`;
   `with_micro_features` → `l1_imbalance`; note live feed lacks depth (microstructure can't
   deploy live).
9. **Where next** — pointers: `data_analysis.ipynb` (template), `signal_research.ipynb`
   (Stage 1–2), `backtesting.ipynb` (Stage 3), `docs/WORKFLOW.md`; "graduate ideas to
   registered strategies, not cells."

## Verification

`jupyter nbconvert --execute` (or Restart & Run All) completes with **zero errors** on the
present `data/real` + `data/ticks`. Strip outputs before commit.

## Out of scope (YAGNI)

- No new analysis the working notebooks already do (no parameter sweeps, no signal IC).
- No changes to `data_analysis.ipynb` or any `mt5_trader.*` code.
- Gold-focused (XAUUSD) examples; XAGUSD mentioned, not toured.
