# `mt5_trader` — package overview

Quant research + MT5 live-trading pipeline for the Model to Market competition.

Standard Python **src-layout**: this package lives at `src/mt5_trader/` and is
imported as `import mt5_trader` (the `src/` wrapper keeps the package off
`sys.path` during dev so packaging bugs surface early). Build target is set in
`pyproject.toml` → `[tool.hatch.build.targets.wheel] packages = ["src/mt5_trader"]`.

## Folder map

| Folder | Role |
|--------|------|
| [`data/`](data/) | Ingest vendor dumps → canonical parquet; aggregate ticks → bars. |
| [`features/`](features/) | Derived signals from bars/ticks: regime, technicals, microstructure. |
| [`strategies/`](strategies/) | Registered trading books (`bars → target position`). |
| [`backtest/`](backtest/) | Vectorized backtester, performance metrics, per-book attribution. |
| [`risk/`](risk/) | Vol-targeted sizing + round-aware loss kill switch. |
| [`live/`](live/) | MT5 gateway + live trading loop for the Windows VPS. |

## Dataflow

```
data (ingest → bars) → features → strategies → backtest (engine → metrics/attribution)
                                       │
                                       └──→ risk (sizing/kill) → live (gateway → runner) → MT5
```

Strategy/research narrative lives in `docs/` (`STRATEGY_PLAYBOOK.md`,
`WORKFLOW.md`, `LESSONS.md`); operational config in `config/` (`portfolio.yaml`,
`risk.yaml`, `instruments.yaml`).
