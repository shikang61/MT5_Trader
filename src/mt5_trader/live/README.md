# `live/`

Live execution against MetaTrader 5 on the Windows VPS. Driven by
`scripts/run_portfolio.py` (all books, one process, netted orders) or
`scripts/run_live.py` (single book). Heartbeats + fills are logged to
`logs/portfolio.jsonl` (consumed by `scripts/watchdog.py` and `pnl_report.py`).

| File | Description |
|------|-------------|
| `mt5_gateway.py` | Thin wrapper around the `MetaTrader5` package: connect, fetch bars/prices, read positions, place/close orders. Isolates the broker API from strategy logic. |
| `runner.py` | Live trading loop: each bar, pull data → strategy signal → risk sizing → net targets across books → reconcile to broker positions → emit `STEP` log. Honors the kill switch and flatten-on-kill. |
