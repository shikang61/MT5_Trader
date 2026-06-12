from datetime import datetime, timedelta, timezone

import numpy as np
import polars as pl

from mt5_trader.strategies import REGISTRY


def make_bars(n=2000, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.0005, n))
    t0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    return pl.DataFrame({
        "ts": [t0 + timedelta(minutes=i) for i in range(n)],
        "open": close,
        "high": close * 1.0002,
        "low": close * 0.9998,
        "close": close,
        "spread_mean": np.full(n, 0.01),
        "ofi": rng.normal(0, 100, n),
    })


def test_all_strategies_produce_valid_signals():
    bars = make_bars()
    for name, cls in REGISTRY.items():
        sig = cls().signal(bars)
        assert len(sig) == len(bars), name
        finite = sig[np.isfinite(sig)]
        assert len(finite) == len(sig), f"{name}: NaN in signal"
        assert np.all(np.abs(sig) <= 1.0), f"{name}: signal outside [-1, 1]"


def test_donchian_goes_long_in_uptrend():
    n = 3000
    t0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    close = 100 + np.arange(n) * 0.01  # steady uptrend
    bars = pl.DataFrame({
        "ts": [t0 + timedelta(minutes=i) for i in range(n)],
        "close": close,
    })
    sig = REGISTRY["donchian"]().signal(bars)
    assert sig[-1] == 1.0
    assert sig.min() >= 0.0  # never short an uptrend
