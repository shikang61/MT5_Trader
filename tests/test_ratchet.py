from mt5_trader.stops import ratchet_mult


def test_below_trigger_uses_disaster_mult():
    assert ratchet_mult(1.0, 8.0, trigger_T=2.0, ratchet_R=3.0) == 8.0


def test_at_or_above_trigger_uses_ratchet():
    assert ratchet_mult(2.0, 8.0, trigger_T=2.0, ratchet_R=3.0) == 3.0
    assert ratchet_mult(5.0, 8.0, trigger_T=2.0, ratchet_R=3.0) == 3.0


def test_disabled_when_ratchet_nonpositive():
    assert ratchet_mult(5.0, 8.0, trigger_T=2.0, ratchet_R=0.0) == 8.0


def test_disabled_when_trigger_nonpositive():
    assert ratchet_mult(5.0, 8.0, trigger_T=0.0, ratchet_R=3.0) == 8.0


def test_volume_spike_tightens_when_configured():
    assert ratchet_mult(5.0, 8.0, 2.0, 3.0, ratchet_R_tight=1.5, volume_spike=True) == 1.5


def test_volume_spike_ignored_when_tight_unset():
    assert ratchet_mult(5.0, 8.0, 2.0, 3.0, ratchet_R_tight=0.0, volume_spike=True) == 3.0


def test_armed_but_no_spike_uses_loose_ratchet():
    assert ratchet_mult(5.0, 8.0, 2.0, 3.0, ratchet_R_tight=1.5, volume_spike=False) == 3.0


import numpy as np
import polars as pl

from mt5_trader.backtest.engine import BTConfig, run


def _ramp_then_pullback():
    # long signal; price ramps up (profit), then a sharp pullback that a tight
    # ratchet would exit but the wide disaster stop would ride through.
    closes = [100, 101, 103, 106, 110, 115, 121, 128, 120, 119, 118]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    n = len(closes)
    bars = pl.DataFrame({
        "ts": [f"2026-06-01T00:{i:02d}:00Z" for i in range(n)],
        "open": closes, "high": highs, "low": lows, "close": closes,
        "spread_mean": [0.1] * n, "n_ticks": [10.0] * n,
    })
    # first 3 bars flat-signal so ATR(3) is defined before entry locks atr_entry
    sig = np.array([0.0, 0.0, 0.0] + [1.0] * (n - 3))
    return bars, sig


def test_engine_ratchet_disabled_matches_default():
    bars, sig = _ramp_then_pullback()
    cfg = dict(sl_atr_mult=8.0, atr_window=3)
    a = run(bars, sig, BTConfig(ratchet_r=0.0, **cfg))
    b = run(bars, sig, BTConfig(**cfg))
    assert np.array_equal(a.bars["pnl"].to_numpy(), b.bars["pnl"].to_numpy())


def test_engine_ratchet_exits_deep_winner_earlier():
    bars, sig = _ramp_then_pullback()
    base = run(bars, sig, BTConfig(sl_atr_mult=8.0, atr_window=3))
    rat = run(bars, sig, BTConfig(sl_atr_mult=8.0, atr_window=3,
                                  ratchet_trigger=2.0, ratchet_r=1.0))
    # the ratchet adds a stop round-trip (exit) the wide baseline does not take
    assert rat.metrics["total_turnover"] > base.metrics["total_turnover"]
