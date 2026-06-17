# Contributor Enablement + Clean Package APIs — Design

**Date:** 2026-06-17
**Status:** Design approved; pending implementation plan
**Type:** Docs + public-API surfacing (no code-logic change)

## Motivation

To let multiple people work in parallel on **signal research**, **backtesting**, and
**post-analysis**, and to add new helpers/functionality cleanly "as packages", the
codebase needs two things it lacks:

1. **Discoverable public APIs per sub-package.** `strategies/` exposes a clean
   surface (`REGISTRY`, `register`), but `backtest/`, `features/`, `data/` have
   **empty `__init__.py`** — contributors deep-import (`from mt5_trader.backtest.engine
   import run`) and there's no documented "public surface" / contract per package.
2. **A contributor guide + collaboration plumbing.** No `CONTRIBUTING.md`,
   `CODEOWNERS`, or PR template — no single place that says how to add a strategy /
   helper / feature / metric, what the evidence gate is, or how review is routed.

The layer **seams already exist** (Strategy Protocol + `@register`, the backtest
engine API, the `portfolio.jsonl` log schema, the new `research.py` toolkit). This
work surfaces and documents them — it does not add new machinery.

## Approach (chosen: A)

Populate the public `__init__.py` exports for `backtest`/`features`/`data`, write
`CONTRIBUTING.md`, add `CODEOWNERS` + a PR template. **No new registries** —
`strategies` needs one (runtime discovery, many of them); `features`/`metrics`/
`studies` have no runtime-discovery need yet, so adding registries now is speculative
(YAGNI). Rejected: B (add plugin registries everywhere — premature); C (docs only,
leave `__init__` empty — contributors keep deep-importing, misses the goal).

## Components

### 1. Public package APIs (`__init__.py` exports + `__all__`)

Curate the public surface so contributors import from the package, not deep modules.
Verified symbol names:

- `src/mt5_trader/backtest/__init__.py`:
  `BTConfig, BTResult, run` (engine); `sharpe, sortino, max_drawdown, summary`
  (metrics); `attribute, format_report, load_steps` (attribution); `gap_bounds,
  segmented_signal` (segment).
- `src/mt5_trader/features/__init__.py`:
  `ema, zscore, rsi, atr, realized_vol, add_sessions` (technicals);
  `efficiency_ratio, regime_series, RANGE, TREND_UP, TREND_DOWN` (regime);
  `with_micro_features, vol_spike_mask` (microstructure).
- `src/mt5_trader/data/__init__.py`:
  `load_ticks` (ingest); `time_bars, tick_bars` (bars).

Each `__init__` imports those names and sets `__all__`. **Risk:** import cycles or
heavy package-import side effects — mitigated by running the full suite after (no
cycles; `strategies/__init__` already imports heavily without issue). No behavior
change: deep imports (`from mt5_trader.backtest.engine import run`) keep working;
the new short imports are additive.

### 2. `CONTRIBUTING.md` (repo root)

One onboarding doc, organized by role:
- **Add a strategy** (signal research): copy `strategies/strategy_template.py` →
  implement `signal(bars) -> np.ndarray` (no lookahead) → add a unit test →
  register by importing it in `strategies/__init__.py`. Validate via `run_backtest`
  + the evidence gate.
- **Add a research helper:** add to `src/mt5_trader/research.py` (or a future
  `research/` sub-package) with a golden-value test.
- **Add a feature / metric:** add to `features/` or `backtest/metrics.py`, export it
  in the package `__init__`, add a test.
- **The evidence gate:** link `docs/WORKFLOW.md` + `docs/LESSONS.md` (cost first,
  weekly splits, param plateau not hot cell, realistic cost — the bar new edges must
  clear before sizing).
- **Run tests / style:** `uv run pytest -q`; match existing style.
- **Branch → PR flow:** branch off `main`, PR with the template, no direct pushes to
  `main`, one review.
- **Live deploy is gated to one owner** (shared VPS/account) — research/backtest/
  post-analysis are offline and safe to parallelize.

### 3. `.github/CODEOWNERS`

Route review by directory (owner usernames filled in by the user):
`src/mt5_trader/strategies/` + `features/` → signal-research owner; `backtest/` +
`scripts/run_backtest.py` + `validate_books.py` → backtesting owner;
`backtest/attribution.py` + `scripts/pnl_report.py` + `dashboard/` → post-analysis
owner; `live/` + `config/` → live-deploy owner.

### 4. `.github/PULL_REQUEST_TEMPLATE.md`

Checklist: tests pass (`uv run pytest`); new strategy cleared the evidence gate
(weekly + cost stress + param plateau); no `config/portfolio.yaml` / `risk.yaml`
change without live-owner sign-off; docs updated.

## Testing

- `uv run pytest -q` after the `__init__` changes — confirms no import cycles and
  nothing broke (the only code change is exports).
- A small `tests/test_public_api.py`: import each curated symbol from its package
  (`from mt5_trader.backtest import run, BTConfig`, etc.) — guards the public surface
  against accidental breakage.

## Out of scope (YAGNI)

- No new registries / plugin systems beyond the existing strategy registry.
- No separate installable pip packages.
- No CI / branch-protection enforcement (separate sub-project — CONTRIBUTING just
  describes the intended flow).
- No code-logic changes — only `__init__` exports and docs.
- CODEOWNERS usernames are placeholders for the user to fill (team not yet named).
