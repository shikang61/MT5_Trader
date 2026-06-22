"""Weekend / market-closed filter. The competition broker's feed goes stale Fri
18:00 UTC (copy_ticks loops its last hour through the weekend — XAUUSD+XAGUSD both
froze at 18:00 on 2026-06-19) and reopens Sun 22:00 UTC. drop_market_closed strips
that window so replayed weekend ticks don't pollute bars/backtests."""
from datetime import datetime, timezone

import polars as pl

from mt5_trader.data.ingest import drop_market_closed


def _df(*hours):
    # hours: list of (month_day, hour) on 2026-06; build UTC ts rows
    return pl.DataFrame({"ts": [datetime(2026, 6, d, h, tzinfo=timezone.utc) for d, h in hours]})


def test_drops_friday_after_close_keeps_before():
    # 2026-06-19 = Friday. Real broker close 21:00 UTC (default).
    out = drop_market_closed(_df((19, 20), (19, 21), (19, 23)))
    assert out["ts"].dt.hour().to_list() == [20]  # 21:00 + 23:00 dropped


def test_drops_all_saturday():
    out = drop_market_closed(_df((20, 0), (20, 12), (20, 23)))  # 2026-06-20 = Sat
    assert out.height == 0


def test_drops_sunday_before_open_keeps_after():
    # 2026-06-21 = Sunday. reopen 22:00 UTC.
    out = drop_market_closed(_df((21, 21), (21, 22), (21, 23)))
    assert out["ts"].dt.hour().to_list() == [22, 23]


def test_keeps_weekday():
    out = drop_market_closed(_df((17, 0), (17, 18), (17, 23)))  # 2026-06-17 = Wed
    assert out.height == 3


def test_hours_overridable_for_other_feeds():
    # Override earlier when a feed/terminal stalls before the real close (e.g. the
    # 2026-06-19 18:00 capture failure): fri_close_hour=18 drops Fri 18:00+.
    out = drop_market_closed(_df((19, 17), (19, 18), (19, 20)), fri_close_hour=18)
    assert out["ts"].dt.hour().to_list() == [17]


def test_works_on_lazyframe():
    lf = _df((19, 17), (20, 12), (21, 23)).lazy()
    out = drop_market_closed(lf).collect()
    assert out["ts"].dt.hour().to_list() == [17, 23]
