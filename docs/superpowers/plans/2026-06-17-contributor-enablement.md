# Contributor Enablement + Clean Package APIs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface curated public `__init__` APIs for `backtest`/`features`/`data`, guarded by a test, and add `CONTRIBUTING.md` + `CODEOWNERS` + a PR template so multiple people can work on signal research / backtesting / post-analysis and add helpers cleanly.

**Architecture:** Only package `__init__.py` exports change (no logic); deep imports keep working, short package imports become available. A `test_public_api.py` pins the surface. Docs are added at the repo root and `.github/`.

**Tech Stack:** Python 3.11 package imports; pytest; Markdown.

---

## Task 1: Public package APIs + guard test

**Files:**
- Modify: `src/mt5_trader/backtest/__init__.py`
- Modify: `src/mt5_trader/features/__init__.py`
- Modify: `src/mt5_trader/data/__init__.py`
- Test: `tests/test_public_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_public_api.py
"""Guard the curated public package surface (contributor-facing imports)."""


def test_backtest_public_api():
    from mt5_trader.backtest import (
        BTConfig, BTResult, run, sharpe, sortino, max_drawdown, summary,
        attribute, format_report, load_steps, gap_bounds, segmented_signal,
    )
    assert callable(run) and callable(summary)


def test_features_public_api():
    from mt5_trader.features import (
        ema, zscore, rsi, atr, realized_vol, add_sessions,
        efficiency_ratio, regime_series, RANGE, TREND_UP, TREND_DOWN,
        with_micro_features, vol_spike_mask,
    )
    assert callable(regime_series)
    assert RANGE != TREND_UP


def test_data_public_api():
    from mt5_trader.data import load_ticks, time_bars, tick_bars
    assert callable(load_ticks) and callable(time_bars)


def test_research_public_api():
    from mt5_trader.research import (
        load_1s, cost_bp, fwd_returns, dedupe, aligned_fwd, sweep_events,
        report, verdict,
    )
    assert callable(cost_bp)


def test_strategies_registry_api():
    from mt5_trader.strategies import REGISTRY, register
    assert "asian_sweep" in REGISTRY and callable(register)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_public_api.py -q`
Expected: FAIL — `ImportError: cannot import name 'run' from 'mt5_trader.backtest'`
(the `backtest`/`features`/`data` `__init__.py` are empty).

- [ ] **Step 3: Populate `backtest/__init__.py`**

```python
# src/mt5_trader/backtest/__init__.py
"""Backtesting: signal -> sized P&L (engine), performance metrics, per-book
attribution, and gap-aware segmented signals. Public API for contributors."""
from .attribution import attribute, format_report, load_steps
from .engine import BTConfig, BTResult, run
from .metrics import max_drawdown, sharpe, sortino, summary
from .segment import gap_bounds, segmented_signal

__all__ = [
    "BTConfig", "BTResult", "run",
    "sharpe", "sortino", "max_drawdown", "summary",
    "attribute", "format_report", "load_steps",
    "gap_bounds", "segmented_signal",
]
```

- [ ] **Step 4: Populate `features/__init__.py`**

```python
# src/mt5_trader/features/__init__.py
"""Feature engineering: technicals, regime classification, microstructure.
Public API for contributors."""
from .microstructure import vol_spike_mask, with_micro_features
from .regime import RANGE, TREND_DOWN, TREND_UP, efficiency_ratio, regime_series
from .technicals import add_sessions, atr, ema, realized_vol, rsi, zscore

__all__ = [
    "ema", "zscore", "rsi", "atr", "realized_vol", "add_sessions",
    "efficiency_ratio", "regime_series", "RANGE", "TREND_UP", "TREND_DOWN",
    "with_micro_features", "vol_spike_mask",
]
```

- [ ] **Step 5: Populate `data/__init__.py`**

```python
# src/mt5_trader/data/__init__.py
"""Data: tick ingestion + tick->bar aggregation. Public API for contributors."""
from .bars import tick_bars, time_bars
from .ingest import load_ticks

__all__ = ["load_ticks", "time_bars", "tick_bars"]
```

- [ ] **Step 6: Run the guard test + full suite**

Run: `uv run pytest tests/test_public_api.py -q`
Expected: PASS (5 passed).

Run: `uv run pytest -q`
Expected: PASS (all green — confirms no import cycles introduced by the new exports).

- [ ] **Step 7: Commit**

```bash
git add src/mt5_trader/backtest/__init__.py src/mt5_trader/features/__init__.py src/mt5_trader/data/__init__.py tests/test_public_api.py
git commit -m "feat(api): curated public package exports for backtest/features/data"
```

---

## Task 2: `CONTRIBUTING.md`

**Files:**
- Create: `CONTRIBUTING.md` (repo root)

- [ ] **Step 1: Write the file**

```markdown
# Contributing

This repo splits into three parallel workstreams. Each owns a layer and talks to
the others only through stable seams, so you can work without stepping on others.

| Workstream | You own | Your contract |
|---|---|---|
| Signal research | `src/mt5_trader/strategies/`, `features/`, `research.py`, `notebooks/signal_research.ipynb` | produce a `Strategy` (`.signal(bars) -> np.ndarray`) |
| Backtesting | `src/mt5_trader/backtest/`, `scripts/run_backtest.py`, `scripts/validate_books.py` | consume a `Strategy`, produce metrics |
| Post-analysis | `backtest/attribution.py`, `scripts/pnl_report.py`, `dashboard/`, the `logs/*.jsonl` schema | consume logs/results, produce reports |

## Setup

```
uv sync
uv run pytest -q
```

## Add a strategy (signal research)

1. Copy `src/mt5_trader/strategies/strategy_template.py` to a new file.
2. Implement `signal(bars: pl.DataFrame) -> np.ndarray` returning a target position
   in `[-1, 1]` per bar. **No lookahead** — use only data up to the current bar
   (the engine shifts execution by one bar; `tests/test_strategies.py::test_no_lookahead`
   enforces this for every registered strategy).
3. Add a unit test in `tests/`.
4. Register it: add the module to the import line in `strategies/__init__.py`.
5. Backtest: `uv run scripts/run_backtest.py --strategy <name> --symbol XAUUSD --data data/real`.
6. Clear the **evidence gate** (below) before adding it to `config/portfolio.yaml`.

## Add a research helper / feature / metric

- Research/event-study helper → `src/mt5_trader/research.py` (+ a golden-value test).
- Feature → `src/mt5_trader/features/`, export it in `features/__init__.py`, add a test.
- Metric → `src/mt5_trader/backtest/metrics.py`, export it in `backtest/__init__.py`,
  add a test.

Import from the package surface, not deep modules:
`from mt5_trader.backtest import run, BTConfig` /
`from mt5_trader.research import cost_bp, sweep_events`.

## The evidence gate (before sizing any edge)

See `docs/WORKFLOW.md` and `docs/LESSONS.md`. In short: compute cost first; require
a positive edge after **realistic** cost; check **weekly splits** (consistent, not
one lucky week) and a **parameter plateau** (not a lone hot cell). A new strategy
only enters `portfolio.yaml` after it clears this.

## Branching & review

- Branch off `main`; open a PR with the template; **no direct pushes to `main`**.
- One review (CODEOWNERS auto-requests the right person).
- Tests must pass.

## Live deploy

The shared VPS / trading account is **gated to the live-deploy owner**. Research,
backtesting, and post-analysis are all offline and safe to run in parallel; never
change `config/portfolio.yaml` or `config/risk.yaml` without the live owner's sign-off.
```

- [ ] **Step 2: Verify the referenced paths exist**

Run: `ls src/mt5_trader/strategies/strategy_template.py docs/WORKFLOW.md docs/LESSONS.md config/portfolio.yaml`
Expected: all four paths exist (no broken references in the doc).

- [ ] **Step 3: Commit**

```bash
git add CONTRIBUTING.md
git commit -m "docs: CONTRIBUTING guide for parallel signal/backtest/post-analysis work"
```

---

## Task 3: CODEOWNERS + PR template

**Files:**
- Create: `.github/CODEOWNERS`
- Create: `.github/PULL_REQUEST_TEMPLATE.md`

- [ ] **Step 1: Write `.github/CODEOWNERS`**

```
# Review routing by area. Replace the placeholder @handles with real GitHub users.
# Syntax: <path glob>   <owner...>

# Signal research
/src/mt5_trader/strategies/   @signal-owner
/src/mt5_trader/features/     @signal-owner
/src/mt5_trader/research.py   @signal-owner

# Backtesting
/src/mt5_trader/backtest/     @backtest-owner
/scripts/run_backtest.py      @backtest-owner
/scripts/validate_books.py    @backtest-owner

# Post-analysis
/src/mt5_trader/backtest/attribution.py  @postanalysis-owner
/scripts/pnl_report.py                   @postanalysis-owner
/dashboard/                              @postanalysis-owner

# Live deploy (gated)
/src/mt5_trader/live/   @live-owner
/config/                @live-owner
```

- [ ] **Step 2: Write `.github/PULL_REQUEST_TEMPLATE.md`**

```markdown
## What & why

<!-- one or two lines -->

## Checklist

- [ ] `uv run pytest -q` passes
- [ ] New strategy cleared the evidence gate (weekly splits + realistic-cost stress + parameter plateau) — see docs/WORKFLOW.md
- [ ] No change to `config/portfolio.yaml` / `config/risk.yaml` without live-deploy owner sign-off
- [ ] Public API changes exported in the package `__init__.py` (+ a `tests/test_public_api.py` entry)
- [ ] Docs updated if behaviour/usage changed
```

- [ ] **Step 3: Commit**

```bash
git add .github/CODEOWNERS .github/PULL_REQUEST_TEMPLATE.md
git commit -m "docs(github): CODEOWNERS review routing + PR template"
```

---

## Self-review notes

- **Spec coverage:** public `__init__` exports (Task 1) + guard test (Task 1);
  CONTRIBUTING (Task 2); CODEOWNERS + PR template (Task 3). All spec components covered.
- **Symbols verified** against the modules: backtest `BTConfig/BTResult/run`,
  `sharpe/sortino/max_drawdown/summary`, `attribute/format_report/load_steps`,
  `gap_bounds/segmented_signal`; features `ema/zscore/rsi/atr/realized_vol/add_sessions`,
  `efficiency_ratio/regime_series/RANGE/TREND_UP/TREND_DOWN`,
  `with_micro_features/vol_spike_mask`; data `load_ticks/time_bars/tick_bars`.
- **No cycles:** none of these packages import `strategies` or `live`; `backtest`
  internally uses `metrics` only. Full suite in Task 1 step 6 confirms.
- **Placeholders:** CODEOWNERS `@*-owner` handles are intentional template values the
  user fills with real GitHub usernames — flagged in the file comment.
