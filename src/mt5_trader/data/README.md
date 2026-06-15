# `data/`

Data ingestion and bar construction. Turns raw vendor dumps into a canonical
per-symbol parquet store (`data/processed/<SYMBOL>/`), then aggregates ticks into
time bars for features/backtest.

| File | Description |
|------|-------------|
| `schema.py` | Canonical tick/depth schema (column names + dtypes) shared across ingest and loaders. |
| `ingest.py` | Vendor CSV dump → canonical per-symbol parquet. `load_ticks(dir, symbol)` reads the processed store. |
| `bars.py` | Tick → bar aggregation. `time_bars(ticks, every, extra=...)` builds OHLC + volume/spread/custom columns at a given interval. |
