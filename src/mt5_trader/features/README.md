# `features/`

Derived inputs computed from bars or ticks, consumed by strategies and the
backtest. Pure functions over polars frames / numpy arrays — no I/O.

| File | Description |
|------|-------------|
| `regime.py` | Trend-up / trend-down / range detection via Kaufman efficiency ratio with hysteresis. `regime_series()`, `efficiency_ratio()`, constants `TREND_UP/RANGE/TREND_DOWN`. Used by `ratio_mr` and `regime_switch`. |
| `technicals.py` | Bar-level technical features (polars expressions / helpers). |
| `microstructure.py` | Tick-level microstructure features from quotes + 5-level depth (e.g. order-flow imbalance). `with_micro_features()` enriches ticks before bar aggregation. |
| `calendar.py` | Economic-calendar **blackout**: `load_events()`, `blackout_mask()`, `BlackoutCfg`. The defensive event mute (mute trading around scheduled high-impact releases); consumed by `RiskManager.in_blackout()` live and `scripts/eval_blackout.py` in backtest. |
