from datetime import datetime, timedelta, timezone

import numpy as np
import polars as pl

from mt5_trader.backtest.segment import gap_bounds, segmented_signal

T0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
STEP = timedelta(minutes=5)


def _bars(deltas):
    return pl.DataFrame({"ts": [T0 + d for d in deltas], "close": [1.0] * len(deltas)})


def test_continuous_is_one_segment():
    assert gap_bounds(_bars([STEP * i for i in range(10)])) == [(0, 10)]


def test_weekend_gap_kept():
    # ~2 days < the 3-day default -> not split
    bars = _bars([STEP * 0, STEP * 1, timedelta(days=2) + STEP * 2])
    assert gap_bounds(bars) == [(0, 3)]


def test_multi_day_hole_splits():
    bars = _bars([STEP * 0, STEP * 1, STEP * 2,
                  timedelta(days=5) + STEP * 3, timedelta(days=5) + STEP * 4])
    assert gap_bounds(bars) == [(0, 3), (3, 5)]


class _AlwaysLong:
    def signal(self, bars):
        return np.ones(len(bars))


def test_segmented_signal_flat_at_each_seam():
    bars = _bars([STEP * 0, STEP * 1, STEP * 2,
                  timedelta(days=5) + STEP * 3, timedelta(days=5) + STEP * 4])
    # last bar of each segment forced flat so the cross-gap move earns nothing
    assert list(segmented_signal(bars, _AlwaysLong())) == [1.0, 1.0, 0.0, 1.0, 0.0]
