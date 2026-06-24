import numpy as np

from mt5_trader.execution import apply_deadband, deadband_series


def test_below_band_holds():
    assert apply_deadband(0.05, 0.0, 0.1) == 0.0


def test_above_band_moves():
    assert apply_deadband(0.2, 0.0, 0.1) == 0.2


def test_exact_boundary_holds():
    # |0.1 - 0| == band -> not strictly greater -> hold
    assert apply_deadband(0.1, 0.0, 0.1) == 0.0


def test_band_zero_passthrough():
    assert apply_deadband(0.03, 0.0, 0.0) == 0.03


def test_negative_band_passthrough():
    assert apply_deadband(0.03, 0.0, -1.0) == 0.03


def test_sign_flip_moves():
    assert apply_deadband(-0.5, 0.5, 0.1) == -0.5


def test_series_holds_subband_wiggle_then_breaks_out():
    pos = np.array([0.0, 0.05, -0.04, 0.06, 0.5, 0.52])
    out = deadband_series(pos, 0.1)
    assert list(out) == [0.0, 0.0, 0.0, 0.0, 0.5, 0.5]
    # turnover (sum |diff|) strictly lower than the raw series
    raw_turn = np.abs(np.diff(pos, prepend=0.0)).sum()
    db_turn = np.abs(np.diff(out, prepend=0.0)).sum()
    assert db_turn < raw_turn


def test_series_band_zero_is_identity():
    pos = np.array([0.0, 0.3, -0.2])
    assert np.array_equal(deadband_series(pos, 0.0), pos)


import polars as pl

from mt5_trader.backtest.engine import BTConfig, run


def _bars(closes):
    return pl.DataFrame({
        "ts": [f"2026-06-01T00:{i:02d}:00Z" for i in range(len(closes))],
        "open": closes, "high": closes, "low": closes, "close": closes,
        "spread_mean": [0.1] * len(closes),
    })


def test_engine_deadband_reduces_turnover():
    # signal wiggles sub-band around a level then makes a real move
    sig = np.array([0.0, 0.05, -0.04, 0.06, -0.03, 1.0, 0.97, 1.0])
    bars = _bars([100.0 + i * 0.1 for i in range(len(sig))])
    base = run(bars, sig, BTConfig(deadband=0.0))
    band = run(bars, sig, BTConfig(deadband=0.2))
    assert band.metrics["total_turnover"] < base.metrics["total_turnover"]


def test_engine_deadband_zero_matches_default():
    sig = np.array([0.0, 0.3, -0.2, 0.5])
    bars = _bars([100.0, 100.1, 99.9, 100.2])
    a = run(bars, sig, BTConfig(deadband=0.0))
    b = run(bars, sig, BTConfig())
    assert np.array_equal(a.bars["pos"].to_numpy(), b.bars["pos"].to_numpy())
