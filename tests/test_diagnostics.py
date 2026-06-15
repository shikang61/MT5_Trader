import numpy as np

from mt5_trader.pipeline import diagnostics
from mt5_trader.pipeline.data import synthetic_bars


def test_ic_sign():
    bars = synthetic_bars(n=2000, seed=3)
    fwd = diagnostics.forward_return(bars, 1)
    perfect = np.nan_to_num(fwd)  # a signal equal to the forward return
    assert diagnostics.information_coefficient(perfect, fwd) > 0.99
    assert diagnostics.information_coefficient(-perfect, fwd) < -0.99
    assert diagnostics.rank_ic(perfect, fwd) > 0.9


def test_ic_zero_for_constant_signal():
    bars = synthetic_bars(n=1000, seed=0)
    fwd = diagnostics.forward_return(bars, 1)
    assert diagnostics.information_coefficient(np.ones(len(fwd)), fwd) == 0.0


def test_turnover_counts_flips():
    sig = np.array([0, 1, 1, -1, 0, 1.0])  # |1|+|0|+|2|+|1|+|1| = 5
    assert diagnostics.turnover(sig) == 5.0


def test_decay_near_horizon_strongest():
    bars = synthetic_bars(n=3000, seed=1)
    sig = np.nan_to_num(diagnostics.forward_return(bars, 1))  # predicts next bar
    dec = diagnostics.signal_decay(bars, sig, horizons=(1, 2, 4, 8, 16))
    ics = dec["ic_by_horizon"]
    assert abs(ics[1]) > abs(ics[16])      # edge decays with horizon
    assert dec["half_life"] is not None


def test_diagnose_report_keys():
    bars = synthetic_bars(n=1500, seed=2)
    sig = bars["close"].diff().sign().fill_null(0).to_numpy()  # toy momentum
    rep = diagnostics.diagnose(bars, sig)
    assert set(rep) == {"ic", "rank_ic", "turnover", "active_frac", "decay", "by_regime"}
    assert set(rep["by_regime"]) == {"trend_up", "range", "trend_down"}
