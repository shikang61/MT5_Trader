from datetime import datetime, timedelta, timezone

import numpy as np
import polars as pl

from mt5_trader.features.regime import RANGE, TREND_DOWN, TREND_UP, regime_series
from mt5_trader.strategies import REGISTRY


def make_bars(close):
    t0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    return pl.DataFrame({
        "ts": [t0 + timedelta(minutes=i) for i in range(len(close))],
        "close": np.asarray(close, dtype=float),
        "spread_mean": np.full(len(close), 0.01),
    })


def trend_up(n=2000):
    rng = np.random.default_rng(0)
    return 100 + np.arange(n) * 0.02 + rng.normal(0, 0.01, n)


def sideways(n=2000):
    rng = np.random.default_rng(1)
    return 100 + np.sin(np.arange(n) / 30) + rng.normal(0, 0.02, n)


def test_uptrend_detected():
    r = regime_series(make_bars(trend_up()))
    assert r[-1] == TREND_UP
    assert (r[500:] == TREND_UP).mean() > 0.9


def test_downtrend_detected():
    r = regime_series(make_bars(trend_up()[::-1]))
    assert r[-1] == TREND_DOWN


def test_range_detected():
    r = regime_series(make_bars(sideways()))
    assert (r[500:] == RANGE).mean() > 0.9


def test_switch_rides_trend():
    sig = REGISTRY["regime_switch"]().signal(make_bars(trend_up()))
    assert sig[-1] == 1.0


def test_switch_fades_range_extremes():
    bars = make_bars(sideways())
    strat = REGISTRY["regime_switch"](fade_size=0.7)  # off by default
    sig = strat.signal(bars)
    close = bars["close"].to_numpy()
    active = sig[500:][sig[500:] != 0]
    assert len(active) > 0
    # fades must oppose displacement from the recent mean
    idx = np.nonzero(sig[500:])[0] + 500
    mid = np.array([close[max(0, i - 240):i].mean() for i in idx])
    corr = np.corrcoef(np.sign(close[idx] - mid), np.sign(sig[idx]))[0, 1]
    assert corr < -0.5
