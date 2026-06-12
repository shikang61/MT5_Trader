import math

from mt5_trader.live.runner import protective_levels


def test_long_sl_below_tp_above():
    sl, tp = protective_levels(side=1.0, price=2400.0, atr_value=5.0,
                               sl_mult=3.0, tp_mult=6.0)
    assert sl == 2400.0 - 15.0
    assert tp == 2400.0 + 30.0


def test_short_mirrored():
    sl, tp = protective_levels(side=-1.0, price=2400.0, atr_value=5.0,
                               sl_mult=3.0, tp_mult=6.0)
    assert sl == 2400.0 + 15.0
    assert tp == 2400.0 - 30.0


def test_zero_mult_means_off():
    sl, tp = protective_levels(1.0, 2400.0, 5.0, sl_mult=3.0, tp_mult=0.0)
    assert tp == 0.0 and sl > 0


def test_flat_or_bad_atr_means_off():
    assert protective_levels(0.0, 2400.0, 5.0, 3.0, 3.0) == (0.0, 0.0)
    assert protective_levels(1.0, 2400.0, 0.0, 3.0, 3.0) == (0.0, 0.0)
    assert protective_levels(1.0, 2400.0, math.nan, 3.0, 3.0) == (0.0, 0.0)
