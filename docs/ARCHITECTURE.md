# Architecture

Two-box design: research happens on macOS, execution on a Windows VPS
(the `MetaTrader5` Python package is Windows-only and requires a local
MT5 terminal).

```
┌────────────── macOS (research) ──────────────┐
│ vendor CSV dump                              │
│   └─ ingest.py ──► parquet (canonical ticks) │
│        └─ microstructure features (OFI, …)   │
│             └─ bars.py (time/tick bars)      │
│                  └─ strategies ──► engine.py │
│                       backtests   metrics    │
└──────────────┬───────────────────────────────┘
               │  git (code + frozen configs)
┌──────────────▼──── Windows VPS (live) ───────┐
│ MT5 terminal ◄── IPC ── MT5Gateway           │
│                          └─ LiveRunner       │
│  every bar: bars → signal → RiskManager      │
│  (vol-target + exposure cap + kill switch)   │
│  → net across books → reconcile → audit log  │
└──────────────────────────────────────────────┘
```

## Components

- **Data** (`data/`): vendor-schema-agnostic ingestion (`schema_map.yaml`
  isolates the unknown dump format), canonical tick schema with 5-level
  depth, time/tick bar builders with pluggable aggregations.
- **Features** (`features/`): tick-level microstructure (microprice, L1/L5
  imbalance, Cont–Kukanov–Stoikov OFI, tick-rule signs) and bar-level
  technicals (EMA/ATR/RSI/zscore/realized vol/session tags).
- **Backtest** (`backtest/`): vectorized engine with a strict
  signal-on-close, fill-next-bar convention (no lookahead by construction —
  enforced by `tests/test_engine.py::test_no_lookahead`), spread + slippage
  cost model, annualized metrics.
- **Strategies** (`strategies/`): registry of interchangeable
  `signal(bars) -> positions` objects; same code path in backtest and live.
- **Risk** (`risk/`): vol targeting, net exposure cap, and a daily loss kill
  switch driven by `config/risk.yaml` — a single standing posture (no
  competition rounds), config not code. See `docs/POSITION_PIPELINE.md`.
- **Live** (`live/`): thin MT5 gateway (connect, bars, reconcile-to-target,
  flatten) and a crash-tolerant runner loop with JSONL audit logging.

## Design choices

- **One signal convention everywhere.** Strategy code is identical in
  research and production; only the data source and the executor differ.
- **Reconcile, don't order.** The runner computes a target position and
  trades the delta; restarts are idempotent and manual intervention is safe.
- **Risk as config.** Leverage caps, vol target, and kill thresholds live in
  YAML, reviewable and diffable — a single always-on posture.
- **Fail-safe loop.** Exceptions are logged and the loop continues; the only
  deliberate halt is the kill switch, which flattens first.
- **Polars + parquet** for tick-scale data (one month of tick + depth across
  6 instruments) on a laptop, without a database to operate.
