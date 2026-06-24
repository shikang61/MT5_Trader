"""A trailing disaster stop whose anchor (extreme) is stale can land on the wrong
side of the current market after price bounces far off the extreme. The gateway
then clamps it to ~spread from market — an ultra-tight stop that instant-triggers
and churns the book open->stop->open (observed live 2026-06-24, ~30 round-trips in
45 min, each paying spread). `widen_breached_stop` pushes only such a *breached*
stop a full disaster-distance (sl_mult*atr) from the current price so a re-attach
stays wide; a normally-trailing stop is left untouched.
"""
import pytest

from mt5_trader.live.runner import widen_breached_stop

# sl_mult=8, atr=2.0 -> disaster distance = 16.0


def test_breached_short_pushed_above_price():
    # short stop BELOW market (price bounced up past the trailed stop) -> breached;
    # widened to price + sl_mult*atr above the market.
    assert widen_breached_stop(-1.0, sl=4017.97, price=4030.0, atr=2.0,
                               sl_mult=8.0) == pytest.approx(4046.0)


def test_breached_long_pushed_below_price():
    # long stop ABOVE market -> breached; widened to price - sl_mult*atr below.
    assert widen_breached_stop(1.0, sl=4030.0, price=4017.0, atr=2.0,
                               sl_mult=8.0) == pytest.approx(4001.0)


def test_normal_short_stop_unchanged():
    # short stop ABOVE market (normal trailing near the lows) -> not breached, kept.
    assert widen_breached_stop(-1.0, sl=4019.0, price=4001.0, atr=2.0,
                               sl_mult=8.0) == 4019.0


def test_normal_long_stop_unchanged():
    # long stop BELOW market (normal trailing) -> not breached, kept.
    assert widen_breached_stop(1.0, sl=3990.0, price=4001.0, atr=2.0,
                               sl_mult=8.0) == 3990.0


def test_zero_stop_passes_through():
    # 0 = no stop -> never invent one.
    assert widen_breached_stop(-1.0, sl=0.0, price=4030.0, atr=2.0, sl_mult=8.0) == 0.0


def test_flat_or_no_atr_unchanged():
    assert widen_breached_stop(0.0, sl=4017.97, price=4030.0, atr=2.0, sl_mult=8.0) == 4017.97
    assert widen_breached_stop(-1.0, sl=4017.97, price=4030.0, atr=0.0, sl_mult=8.0) == 4017.97
