import numpy as np
import polars as pl

from mt5_trader.features.regime import RANGE, regime_series
from mt5_trader.live.runner import pair_leg_lots
from mt5_trader.strategies.meanrev_pairs import RatioMeanRev


def test_long_spread_is_long_a_short_b():
    lots_a, lots_b = pair_leg_lots(
        target_frac=0.5, beta=1.0, equity=1_000_000,
        price_a=2400.0, price_b=30.0, contract_a=100, contract_b=5000,
    )
    assert lots_a > 0 and lots_b < 0
    # each leg notional = frac * equity (beta=1)
    assert abs(lots_a * 100 * 2400.0 - 500_000) < 1e-6
    assert abs(-lots_b * 5000 * 30.0 - 500_000) < 1e-6


def test_beta_scales_leg_b_only():
    _, b1 = pair_leg_lots(1.0, 1.0, 1e6, 100, 100, 1, 1)
    a2, b2 = pair_leg_lots(1.0, 2.0, 1e6, 100, 100, 1, 1)
    assert b2 == 2 * b1
    assert a2 == pair_leg_lots(1.0, 1.0, 1e6, 100, 100, 1, 1)[0]


def test_short_spread_flips_both():
    la, lb = pair_leg_lots(-1.0, 1.0, 1e6, 100, 100, 1, 1)
    assert la < 0 and lb > 0


def test_zero_signal_zero_lots():
    assert pair_leg_lots(0.0, 1.0, 1e6, 100, 100, 1, 1) == (0.0, 0.0)


def test_ratio_mr_regime_filter_only_trades_in_range():
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.normal(0, 0.5, 3000))  # random walk: mixed regimes
    bars = pl.DataFrame({"close": close})
    on = RatioMeanRev(z_n=240, regime_filter=True).signal(bars)
    off = RatioMeanRev(z_n=240, regime_filter=False).signal(bars)
    reg = regime_series(bars)
    assert (reg == RANGE).any() and (reg != RANGE).any()   # filter is exercised
    assert np.all((on != 0) <= (reg == RANGE))             # positions only in range
    assert np.all((on != 0) <= (off != 0))                 # never adds a position
