from mt5_trader.stops import breakeven_floor


def test_below_trigger_unchanged():
    # 1 ATR profit, trigger 2 -> not armed -> disaster sl unchanged
    assert breakeven_floor(92.0, 1.0, 100.0, 1.0, 1.0, trigger=2.0, buffer=0.1) == 92.0


def test_armed_long_floors_up_to_entry_plus_buffer():
    # disaster sl 92 below entry; armed (5>=2) -> floor up to 100 + 0.1*1
    assert breakeven_floor(92.0, 1.0, 100.0, 1.0, 5.0, 2.0, 0.1) == 100.1


def test_armed_short_floors_down_to_entry_minus_buffer():
    assert breakeven_floor(108.0, -1.0, 100.0, 1.0, 5.0, 2.0, 0.1) == 99.9


def test_disabled_when_trigger_nonpositive():
    assert breakeven_floor(92.0, 1.0, 100.0, 1.0, 5.0, trigger=0.0, buffer=0.1) == 92.0


def test_deep_winner_disaster_already_past_entry_is_noop():
    # disaster sl 105 already above entry -> breakeven floor does not lower it
    assert breakeven_floor(105.0, 1.0, 100.0, 1.0, 9.0, 2.0, 0.1) == 105.0


def test_zero_buffer_floors_at_entry():
    assert breakeven_floor(92.0, 1.0, 100.0, 1.0, 5.0, 2.0, 0.0) == 100.0


import numpy as np
import polars as pl

from mt5_trader.backtest.engine import BTConfig, run


def _ramp_then_through_entry():
    # 3 warmup bars (sig 0) so ATR(3) is defined at entry; modest ramp (shallow
    # winner: disaster stop stays below entry) then a retrace through entry and down.
    closes = [100, 101, 102, 103, 104, 105, 106, 107, 105, 102, 99]
    n = len(closes)
    bars = pl.DataFrame({
        "ts": [f"2026-06-01T00:{i:02d}:00Z" for i in range(n)],
        "open": closes, "high": [c + 0.5 for c in closes],
        "low": [c - 0.5 for c in closes], "close": closes,
        "spread_mean": [0.1] * n, "n_ticks": [10.0] * n,
    })
    sig = np.array([0.0, 0.0, 0.0] + [1.0] * (n - 3))
    return bars, sig


def test_engine_breakeven_disabled_matches_default():
    bars, sig = _ramp_then_through_entry()
    cfg = dict(sl_atr_mult=8.0, atr_window=3)
    a = run(bars, sig, BTConfig(breakeven_trigger=0.0, **cfg))
    b = run(bars, sig, BTConfig(**cfg))
    assert np.array_equal(a.bars["pnl"].to_numpy(), b.bars["pnl"].to_numpy())


def test_engine_breakeven_avoids_the_loss():
    bars, sig = _ramp_then_through_entry()
    base = run(bars, sig, BTConfig(sl_atr_mult=8.0, atr_window=3))
    be = run(bars, sig, BTConfig(sl_atr_mult=8.0, atr_window=3,
                                 breakeven_trigger=2.0, breakeven_buffer=0.1))
    # breakeven exits near entry on the retrace; baseline rides the wide stop into loss
    assert be.metrics["total_return"] > base.metrics["total_return"]
