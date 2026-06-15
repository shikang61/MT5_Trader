# MT5_Trader

Quant research + live trading pipeline for the **Model to Market** competition
(Jun 15–27, 2026). Gold + FX, tick data with 5-level depth, MT5 execution.

## Layout

```
src/mt5_trader/
  data/        ingestion (CSV dump → parquet), canonical schema, bars
  features/    microstructure (OFI, imbalance) + technicals
  backtest/    vectorized engine + metrics (no-lookahead by construction)
  strategies/  donchian, vwap_trend, london_orb, sweep_fade, ratio_mr +
               benched (regime_switch, ofi, fast_mr); registry-based — see
               strategies/README.md for the file↔name map
  risk/        round-aware vol targeting + kill switch (config/risk.yaml)
  live/        MT5 gateway + runner loop (Windows VPS only)
config/        instruments, risk budgets, dump schema map
scripts/       ingest_dump.py, run_backtest.py, run_live.py
docs/          STRATEGY_PLAYBOOK, COMPETITION_PLAN, ARCHITECTURE
```

## Quickstart (research, macOS)

```bash
uv sync --extra dev
uv run pytest                      # full suite

# when the historical dump arrives:
#   1. edit config/schema_map.yaml to match the real CSV columns
#   2. drop CSVs into data/raw/
uv run scripts/ingest_dump.py data/raw
uv run scripts/run_backtest.py --strategy donchian --symbol XAUUSD
uv run scripts/run_backtest.py --strategy ratio_mr --symbol XAUUSD --symbol2 XAGUSD
```

## Live (Windows VPS)

MT5 terminal + this repo on the VPS; credentials via env vars (`MT5_LOGIN`,
`MT5_PASSWORD`, `MT5_SERVER`):

```
pip install -e .
python scripts/run_live.py --strategy donchian --symbol XAUUSD
```

**New here? Start with `docs/ONBOARDING.md`.** Then see
`docs/COMPETITION_PLAN.md` for the day-by-day checklist and
`docs/STRATEGY_PLAYBOOK.md` for the strategy lineup per round.
