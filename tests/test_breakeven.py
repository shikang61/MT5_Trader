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
