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
