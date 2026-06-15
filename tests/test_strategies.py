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


def test_no_lookahead():
    """Causality guard: appending future bars must not change past signals.
    signal(bars)[:k] must equal signal(bars[:k]) for every registered strategy.
    A strategy that peeks at future data (.shift(-1), a centered window, a
    non-causal groupby) breaks this — the cardinal sin base.py warns about and
    the bug class that silently inflates every backtest."""
    bars = make_bars(n=2000, seed=1)
    k = 1500  # well past every default warmup window
    for name, cls in REGISTRY.items():
        full = cls().signal(bars)
        trunc = cls().signal(bars[:k])
        assert np.allclose(full[:k], trunc, equal_nan=True), \
            f"{name}: signal[:{k}] changed when future bars appended — lookahead"


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
