from datetime import datetime, timedelta, timezone

import polars as pl

from mt5_trader.features.regime import RANGE, TREND_DOWN, TREND_UP
from mt5_trader.live.passive_limits import (
    consolidation_levels, desired_sides, limit_params, prior_day_levels,
    sides_for_regime,
)


def _bars(rows):
    # rows: (ts, high, low)
    return pl.DataFrame({"ts": [r[0] for r in rows],
                         "high": [r[1] for r in rows],
                         "low": [r[2] for r in rows]})


def test_prior_day_levels_uses_prior_complete_day():
    d1 = datetime(2026, 5, 12, 12, tzinfo=timezone.utc)
    d2 = datetime(2026, 5, 13, 12, tzinfo=timezone.utc)
    bars = _bars([
        (d1, 4800.0, 4700.0),          # day 1 high/low
        (d1 + timedelta(hours=1), 4820.0, 4690.0),
        (d2, 4810.0, 4750.0),          # day 2 (current) — must be ignored
    ])
    pdh, pdl = prior_day_levels(bars)
    assert pdh == 4820.0 and pdl == 4690.0  # day-1 extremes, not day-2


def test_prior_day_levels_none_with_one_day():
    d1 = datetime(2026, 5, 12, 12, tzinfo=timezone.utc)
    assert prior_day_levels(_bars([(d1, 4800.0, 4700.0)])) is None


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


def test_sides_for_regime():
    assert sides_for_regime(TREND_UP) == (1.0,)      # buy support only
    assert sides_for_regime(TREND_DOWN) == (-1.0,)   # sell resistance only
    assert set(sides_for_regime(RANGE)) == {1.0, -1.0}  # both


def test_consolidation_only_trades_in_range():
    # consolidation bounds only mean-revert inside a range; outside -> cancel
    assert set(desired_sides("consolidation", RANGE, True)) == {1.0, -1.0}
    assert desired_sides("consolidation", TREND_UP, True) == ()
    assert desired_sides("consolidation", TREND_DOWN, True) == ()


def test_pdhl_uses_regime_gate():
    assert desired_sides("pdhl", TREND_UP, True) == (1.0,)
    assert set(desired_sides("pdhl", TREND_UP, False)) == {1.0, -1.0}  # gate off


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
