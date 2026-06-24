import numpy as np
import polars as pl

from mt5_trader.features.regime import cusum_regime


def _bars(closes):
    return pl.DataFrame({"close": closes})


def test_persistent_up_drift_flags_trend_up():
    # steady rise + tiny alternating noise (nonzero std) -> consistent +z -> TREND_UP
    closes = [100.0 + i + (0.1 if i % 2 else -0.1) for i in range(120)]
    r = cusum_regime(_bars(closes), drift_k=0.5, threshold_h=3.0, vol_window=5,
                     hold=30, coarsen=1)
    assert (r[20:] == 1).mean() > 0.8


def test_zero_drift_chop_flags_range():
    # +1/-1 oscillation, no net drift -> accumulators bleed -> RANGE
    closes = [100.0 + (1.0 if i % 2 else 0.0) for i in range(120)]
    r = cusum_regime(_bars(closes), drift_k=0.5, threshold_h=3.0, vol_window=5,
                     hold=30, coarsen=1)
    assert (r[20:] == 0).mean() > 0.8


def test_persistent_down_drift_flags_trend_down():
    closes = [100.0 - i - (0.1 if i % 2 else -0.1) for i in range(120)]
    r = cusum_regime(_bars(closes), drift_k=0.5, threshold_h=3.0, vol_window=5,
                     hold=30, coarsen=1)
    assert (r[20:] == -1).mean() > 0.8


def test_causal_prefix_stable():
    # regime at past bars must not change when more bars are appended
    closes = [100.0 + i + (0.1 if i % 2 else -0.1) for i in range(120)]
    full = cusum_regime(_bars(closes), 0.5, 3.0, 5, 30, 1)
    prefix = cusum_regime(_bars(closes[:80]), 0.5, 3.0, 5, 30, 1)
    assert np.array_equal(full[:80], prefix)


def test_disabled_threshold_is_passthrough():
    closes = [100.0 + (1.0 if i % 2 else 0.0) for i in range(50)]
    r = cusum_regime(_bars(closes), threshold_h=0.0)
    assert np.all(r == 1)
