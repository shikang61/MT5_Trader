from mt5_trader.stops import scaleout_remaining


def test_below_trigger_full_size():
    assert scaleout_remaining(1.0, trigger_T=2.0, close_frac=0.5) == 1.0


def test_at_or_above_trigger_reduces():
    assert scaleout_remaining(2.0, 2.0, 0.5) == 0.5
    assert scaleout_remaining(9.0, 2.0, 0.25) == 0.75


def test_disabled_zero_frac():
    assert scaleout_remaining(9.0, 2.0, 0.0) == 1.0


def test_disabled_zero_trigger():
    assert scaleout_remaining(9.0, 0.0, 0.5) == 1.0


import numpy as np
import polars as pl

from mt5_trader.backtest.engine import BTConfig, run


def _bars(closes):
    n = len(closes)
    return pl.DataFrame({
        "ts": [f"2026-06-01T00:{i:02d}:00Z" for i in range(n)],
        "open": closes, "high": [c + 0.5 for c in closes],
        "low": [c - 0.5 for c in closes], "close": closes,
        "spread_mean": [0.1] * n,
    }), np.array([0.0, 0.0, 0.0] + [1.0] * (n - 3))


def test_engine_scaleout_disabled_matches_default():
    bars, sig = _bars([100, 101, 102, 103, 104, 105, 106, 107, 108])
    a = run(bars, sig, BTConfig(sl_atr_mult=8.0, atr_window=3, scaleout_frac=0.0))
    b = run(bars, sig, BTConfig(sl_atr_mult=8.0, atr_window=3))
    assert np.array_equal(a.bars["pnl"].to_numpy(), b.bars["pnl"].to_numpy())


def test_engine_scaleout_caps_clean_ramp_and_adds_turnover():
    # strong steady up-ramp, no pullback -> scaling out leaves less on the table
    bars, sig = _bars([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112])
    base = run(bars, sig, BTConfig(sl_atr_mult=8.0, atr_window=3))
    so = run(bars, sig, BTConfig(sl_atr_mult=8.0, atr_window=3,
                                 scaleout_trigger=2.0, scaleout_frac=0.5))
    assert so.metrics["total_return"] < base.metrics["total_return"]      # tail capped
    assert so.metrics["total_turnover"] > base.metrics["total_turnover"]  # partial close costs


def test_engine_scaleout_locks_profit_on_round_trip():
    # up to a peak then back near entry: banking half ends positive vs ~flat baseline
    bars, sig = _bars([100, 101, 102, 103, 104, 105, 106, 107, 108, 107, 106, 105, 104, 103])
    base = run(bars, sig, BTConfig(sl_atr_mult=8.0, atr_window=3))
    so = run(bars, sig, BTConfig(sl_atr_mult=8.0, atr_window=3,
                                 scaleout_trigger=2.0, scaleout_frac=0.5))
    assert so.metrics["total_return"] > base.metrics["total_return"]      # locked-in gain
