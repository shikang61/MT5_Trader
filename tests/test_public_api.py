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
