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
