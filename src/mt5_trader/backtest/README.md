# `backtest/`

Vectorized, bar-level simulation and reporting. Strategy signals are shifted one
bar before execution; costs come from per-bar spread plus a configurable
slippage. Used by `scripts/run_backtest.py` and `scripts/validate_books.py`.

| File | Description |
|------|-------------|
| `engine.py` | Vectorized bar-level backtester. `run(bars, signal, BTConfig) -> BTResult` (`.bars` frame with per-bar `pnl`/`equity` columns + a `.metrics` dict). `BTConfig`: `leverage`, `slippage_bps`, `init_equity`, `bars_per_year`. |
| `metrics.py` | Performance metrics on per-bar fractional returns: `summary()` → total return, Sharpe, max drawdown, turnover, hit rate. |
| `attribution.py` | Per-book P&L attribution from live portfolio runner logs (`portfolio.jsonl`): `load_steps`, `attribute`, `format_report`. Backs `scripts/pnl_report.py`. |
