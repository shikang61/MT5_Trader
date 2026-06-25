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


def test_vwap_trend_regime_filter_off_is_default():
    """The gate is opt-in: default construction must equal regime_filter=False."""
    bars = make_bars(n=2000, seed=0)
    cls = REGISTRY["vwap_trend"]
    assert np.array_equal(cls().signal(bars), cls(regime_filter=False).signal(bars))


def test_vwap_trend_regime_gate_masks_range_bars():
    """regime_filter=True zeroes the position on every RANGE bar and leaves
    the position untouched on TREND bars."""
    from mt5_trader.features.regime import RANGE, regime_series
    bars = make_bars(n=2000, seed=0)
    cls = REGISTRY["vwap_trend"]
    ungated = cls(regime_filter=False).signal(bars)
    gated = cls(regime_filter=True, regime_coarsen=4).signal(bars)
    regime = regime_series(bars, coarsen=4)
    # non-vacuous: the strategy actually trades and both regimes occur
    assert (ungated != 0).any()
    assert (regime == RANGE).any() and (regime != RANGE).any()
    # the gate itself
    assert np.all(gated[regime == RANGE] == 0.0)
    assert np.array_equal(gated[regime != RANGE], ungated[regime != RANGE])


def test_vwap_trend_regime_gate_no_lookahead():
    """Gated signal stays causal: appending future bars must not change past
    signals (regime_series is anchored to the start, so this must hold)."""
    bars = make_bars(n=2000, seed=1)
    k = 1500
    cls = REGISTRY["vwap_trend"]
    full = cls(regime_filter=True, regime_coarsen=4).signal(bars)
    trunc = cls(regime_filter=True, regime_coarsen=4).signal(bars[:k])
    assert np.allclose(full[:k], trunc, equal_nan=True)


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


def test_donchian_retest_registered_and_delays_entry():
    from mt5_trader.strategies.momentum_breakout import DonchianBreakout
    bars = make_bars(n=4000, seed=3)
    assert "donchian_retest" in REGISTRY
    base = DonchianBreakout().signal(bars)
    retest = REGISTRY["donchian_retest"]().signal(bars)
    # exits unchanged: a held retest bar always coincides with a held baseline bar
    held = retest != 0
    assert held.any()                                   # non-vacuous
    assert np.array_equal(retest[held], base[held])
    # entry is delayed: the retest is in the market on fewer (or equal) bars
    assert (retest != 0).sum() <= (base != 0).sum()


def test_donchian_retest_fallback_enters_at_least_as_often():
    bars = make_bars(n=4000, seed=3)
    skip = REGISTRY["donchian_retest"](fallback=False).signal(bars)
    fb = REGISTRY["donchian_retest"](fallback=True).signal(bars)
    assert (fb != 0).sum() >= (skip != 0).sum()
