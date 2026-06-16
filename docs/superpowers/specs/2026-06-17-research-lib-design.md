# Research Primitives Library — Design

**Date:** 2026-06-17
**Status:** Design approved; pending implementation plan
**Type:** Refactor / consolidation (DRY) — no new analysis capability

## Motivation

The event-study / data-analysis primitives used across research are **duplicated
and scattered** as private functions inside individual scripts, so notebooks and
new analyses can't reuse them — they get rewritten inline every time.

Confirmed duplication (2026-06-17):
- `cost_bp` — 3 copies (`research_hf.py`, `research_sweep.py`, `research_volume.py`)
- `fwd_returns` — 2 copies (`research_sweep.py`, `research_volume.py`), byte-identical
- `_report` — 2 copies (`research_sweep.py`, `research_volume.py`), byte-identical
- `_dedupe`, `load_1s`, `_aligned_fwd`, `_verdict` — buried in `research_hf.py`
- `sweep_events` — buried in `research_sweep.py`

`cost_bp` has two column variants with the same formula: `spread/mid` on 1s grids
(`research_hf`) vs `spread_mean/close` on bars (`research_sweep`, `research_volume`).

Goal: lift these into one tested, importable module; point the scripts at it
(deleting their copies); make them importable from notebooks.

## Approach (chosen: A — single module)

A single module `src/mt5_trader/research.py` holding ~8 primitives. Clean notebook
import (`from mt5_trader.research import cost_bp, sweep_events, ...`); matches the
repo's single-module style (`backtest/metrics.py`). Split into a subpackage later
only if it grows. Rejected: a subpackage now (premature for 8 functions); scattering
the primitives into existing modules (no single discoverable "library").

## Component: `src/mt5_trader/research.py`

Public API (behavior preserved exactly from the current script copies):

```
SLIP_BP = 0.5   # default round-trip slippage add-on, bp

load_1s(symbol, sources=("data/processed",)) -> pl.DataFrame
    # 1s grid of last mid + mean spread, forward-filled inside gaps.
    # sources: tick dirs passed to load_ticks (parametrized — removes the old
    # hardcoded module-level DATA constant / monkeypatch).

cost_bp(df, spread_col="spread_mean", mid_col="close", slip_bp=SLIP_BP) -> float
    # round-trip cost bp = mean(df[spread_col]/df[mid_col])*1e4 + slip_bp.
    # bars: defaults. 1s grids: cost_bp(g, spread_col="spread", mid_col="mid").

fwd_returns(close, horizon) -> np.ndarray
    # forward log return over `horizon` bars, entry at the next bar (no lookahead),
    # NaN tail. close: np.ndarray.

dedupe(idx, min_gap) -> np.ndarray
    # greedily keep indices >= min_gap apart.

aligned_fwd(sig, fwd, events) -> tuple[float, float, int]
    # (mean signed forward bp, hit rate, n) over event indices; sign(sig)*fwd*1e4,
    # NaN-safe (returns (0.0, 0.0, 0) if no valid events).

sweep_events(bars, levels, side, quiet=60, confirm=5) -> tuple[np.ndarray, np.ndarray]
    # first bar where price crosses per-bar `levels` after `quiet` untouched bars;
    # returns (event indices, reclaim flags at `confirm` bars later). side=+1 high
    # side (buy stops above), -1 low side.

report(label, signed_fwd_bp, hurdle) -> None
    # prints "<label>: PASS/FAIL edge ... (cost ...), n=..., hit ..." (print format
    # byte-identical to research_sweep._report).

verdict(name, edge_bp, hurdle_bp, n, hit) -> None
    # prints "-> PASS/FAIL: edge ... bp/trade vs cost ... bp (n events, hit ...)"
    # (byte-identical to research_hf._verdict).
```

Internal: `load_1s` is a thin IO wrapper — `load_ticks(sources, symbol)` then a pure
`_grid_1s(ticks) -> pl.DataFrame` (the 1s group_by + upsample + ffill), so the grid
logic is unit-testable on an in-memory frame.

### Boundaries

All functions are pure except `load_1s` (IO via the existing `load_ticks`). Each has
one purpose, holds in context, and is independently testable. No changes to
`load_ticks`, `engine`, `metrics`, `data/*`.

## Tests: `tests/test_research.py`

Golden-value unit tests:
- `fwd_returns` — known close array → expected forward log returns + NaN tail.
- `dedupe` — indices → kept set respecting `min_gap`.
- `aligned_fwd` — sig/fwd/events → (mean_bp, hit, n); NaN handling → (0,0,0).
- `cost_bp` — a df with `spread_mean`/`close` → known bp; and the `spread`/`mid` variant.
- `sweep_events` — small bars + per-bar levels → expected event idx + reclaim flags
  (mirrors the `test_passive_limits` / sweep_fade style).
- `_grid_1s` — synthetic ticks → correct 1s grid shape + forward-fill.
- `report` / `verdict` — capsys: PASS vs FAIL wording on the threshold branch.

## Script refactor (true DRY)

`research_hf.py`, `research_sweep.py`, `research_volume.py`:
- Delete their private copies of the consolidated functions + `SLIP_BP`.
- `from mt5_trader.research import cost_bp, fwd_returns, dedupe, aligned_fwd, sweep_events, report, verdict, load_1s`.
- Fix call sites: `_dedupe→dedupe`, `_aligned_fwd→aligned_fwd`, `_report→report`,
  `_verdict→verdict`; `research_hf`'s grid `cost_bp` calls pass
  `spread_col="spread", mid_col="mid"`; `load_1s(sym, sources=[DATA])` to preserve
  the data/processed default.
- **Smoke-run each after refactor** (`research_hf momentum`, `research_sweep pdhl
  --symbol XAUUSD`, `research_volume <study>`) — confirm no errors and sane PASS/FAIL
  output. Unit tests pin exact primitive behavior.

## Notebooks

`notebooks/*.ipynb` import the primitives from `mt5_trader.research` instead of
reinventing them inline.

## Out of scope (YAGNI)

- No new study types (null test, param wiggle = separate "new capability" effort).
- No plotting helpers.
- No changes to `engine` / `metrics` / `data` / `load_ticks`.
- Do NOT extract study-specific orchestration (`_study`, `study_wiggle`,
  `study_*`) — those stay in the scripts.
