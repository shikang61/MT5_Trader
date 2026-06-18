from datetime import datetime, timedelta, timezone

import polars as pl

from mt5_trader.features.regime import RANGE, TREND_DOWN, TREND_UP
from mt5_trader.live.passive_limits import (
    PassiveCfg, PassiveLimitRunner, consolidation_levels, desired_sides,
    limit_params, restable,
)


def test_limit_params_buy_support():
    price, sl, tp = limit_params(1.0, 4000.0, tp_bp=10.0, sl_bp=20.0)
    assert price == 4000.0
    assert round(tp, 4) == 4004.0      # +10bp
    assert round(sl, 4) == 3992.0      # -20bp


def test_limit_params_sell_resistance():
    price, sl, tp = limit_params(-1.0, 4000.0, tp_bp=10.0, sl_bp=20.0)
    assert price == 4000.0
    assert round(tp, 4) == 3996.0      # -10bp (bounce down)
    assert round(sl, 4) == 4008.0      # +20bp (break up)


def test_consolidation_only_trades_in_range():
    # consolidation bounds only mean-revert inside a range; outside -> cancel
    assert set(desired_sides(RANGE)) == {1.0, -1.0}
    assert desired_sides(TREND_UP) == ()
    assert desired_sides(TREND_DOWN) == ()


def test_broke_out_cancels_outside_band():
    # band: ceiling 4350, floor 4300; breakout_bp=15 -> breakout margin 0.15%
    r = PassiveLimitRunner(None, PassiveCfg(breakout_bp=15.0))
    assert r._broke_out(4360.0, 4350.0, 4300.0)        # above ceiling+margin -> broke up
    assert r._broke_out(4290.0, 4350.0, 4300.0)        # below floor-margin -> broke down
    assert not r._broke_out(4355.0, 4350.0, 4300.0)    # within margin of ceiling -> inside
    assert not r._broke_out(4325.0, 4350.0, 4300.0)    # mid-band -> inside


def test_broke_out_independent_of_sl_bp():
    # breakout-cancel is driven by breakout_bp, NOT the (wider) position stop sl_bp.
    r = PassiveLimitRunner(None, PassiveCfg(breakout_bp=15.0, sl_bp=25.0))
    # 20bp above ceiling 4350 = 4358.7: past the 15bp cancel margin (4356.5) -> broke,
    # even though it is inside the 25bp stop (4360.9). Proves the decoupling.
    assert r._broke_out(4358.7, 4350.0, 4300.0)
    assert not r._broke_out(4355.0, 4350.0, 4300.0)    # within 15bp margin -> not broken


def test_consolidation_levels_rolling_minmax():
    n = 60
    ts = [datetime(2026, 5, 12, tzinfo=timezone.utc) + timedelta(minutes=5 * i)
          for i in range(n)]
    close = [4000.0 + (i % 10) for i in range(n)]  # oscillates 4000..4009
    bars = pl.DataFrame({"ts": ts, "close": close})
    ceiling, floor = consolidation_levels(bars, range_n=20)
    assert ceiling == 4009.0 and floor == 4000.0


def test_consolidation_levels_none_until_window_full():
    ts = [datetime(2026, 5, 12, tzinfo=timezone.utc) + timedelta(minutes=5 * i)
          for i in range(5)]
    bars = pl.DataFrame({"ts": ts, "close": [4000.0] * 5})
    assert consolidation_levels(bars, range_n=20) is None


def test_restable_skips_breached_levels():
    bid, ask = 4321.41, 4321.50
    # buy limit must sit below ask; sell limit above bid
    assert restable(1.0, 4300.0, bid, ask)        # buy support below market -> ok
    assert not restable(1.0, 4330.0, bid, ask)    # buy level above market -> breached
    assert restable(-1.0, 4340.0, bid, ask)       # sell resistance above market -> ok
    assert not restable(-1.0, 4310.0, bid, ask)   # sell level below market -> breached


from datetime import date

from mt5_trader.live.passive_limits import daily_halt


def test_daily_halt_trips_on_loss_past_cap():
    today = date(2026, 6, 17)
    active, hd = daily_halt(realized_today=-150.0, cap=150.0, halted_day=None, today=today)
    assert active is True and hd == today
    # just under the cap does not trip
    active2, hd2 = daily_halt(-149.99, 150.0, None, today)
    assert active2 is False and hd2 is None


def test_daily_halt_stays_halted_rest_of_day():
    today = date(2026, 6, 17)
    # already halted earlier today; realized has since recovered above the cap
    active, hd = daily_halt(realized_today=-10.0, cap=150.0, halted_day=today, today=today)
    assert active is True and hd == today


def test_daily_halt_resumes_next_day():
    yesterday, today = date(2026, 6, 16), date(2026, 6, 17)
    active, hd = daily_halt(realized_today=-5.0, cap=150.0, halted_day=yesterday, today=today)
    assert active is False and hd == yesterday   # past halt cleared -> resume


def test_daily_halt_off_when_cap_zero():
    today = date(2026, 6, 17)
    active, hd = daily_halt(realized_today=-9999.0, cap=0.0, halted_day=None, today=today)
    assert active is False and hd is None


def test_passive_cfg_has_daily_loss_cap_default():
    assert PassiveCfg().daily_loss_cap == 150.0


def test_runner_initializes_halt_state():
    r = PassiveLimitRunner(None, PassiveCfg())
    assert r._halted_day is None
