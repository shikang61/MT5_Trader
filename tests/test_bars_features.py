from datetime import datetime, timedelta, timezone

import polars as pl

from mt5_trader.data.bars import time_bars
from mt5_trader.features.microstructure import with_micro_features


def make_ticks(quotes):
    """quotes: list of (sec_offset, bid, ask, bid_sz, ask_sz)."""
    t0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    return pl.DataFrame({
        "ts": [t0 + timedelta(seconds=q[0]) for q in quotes],
        "symbol": ["XAUUSD"] * len(quotes),
        "bid": [float(q[1]) for q in quotes],
        "ask": [float(q[2]) for q in quotes],
        "bid_sz": [float(q[3]) for q in quotes],
        "ask_sz": [float(q[4]) for q in quotes],
    })


def test_time_bars_ohlc():
    ticks = make_ticks([
        (0, 100, 102, 1, 1),    # mid 101
        (10, 104, 106, 1, 1),   # mid 105 (high)
        (20, 98, 100, 1, 1),    # mid 99  (low)
        (50, 101, 103, 1, 1),   # mid 102 (close)
        (70, 200, 202, 1, 1),   # next bar
    ])
    bars = time_bars(ticks, "1m")
    assert len(bars) == 2
    first = bars.row(0, named=True)
    assert (first["open"], first["high"], first["low"], first["close"]) == (101, 105, 99, 102)
    assert first["n_ticks"] == 4


def test_ofi_hand_computed():
    ticks = make_ticks([
        (0, 10, 11, 5, 5),
        (1, 10, 11, 7, 4),  # bid same: +2, ask same: 5-4=+1 -> 3
        (2, 11, 12, 2, 3),  # bid up: +2, ask up: +prev ask sz 4 -> 6
    ])
    out = with_micro_features(ticks)
    assert out["ofi"].to_list() == [0.0, 3.0, 6.0]


def test_microprice_between_quotes():
    ticks = make_ticks([(0, 10, 11, 9, 1)])
    mp = with_micro_features(ticks)["microprice"][0]
    assert 10 < mp < 11
    assert mp > 10.5  # heavy bid -> microprice near ask
