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
