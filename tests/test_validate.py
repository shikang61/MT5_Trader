from datetime import datetime, timedelta, timezone

import numpy as np
import polars as pl

from mt5_trader.pipeline import validate
from mt5_trader.pipeline.data import synthetic_bars

GATES = {"week_by_week", "cost_stress", "param_wiggle", "turnover",
         "walk_forward", "deflated_sharpe"}


def _trend_bars(n=8000, slope=0.00005):
    close = 2000.0 * (1 + np.arange(n) * slope)
    t0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    return pl.DataFrame({
        "ts": [t0 + timedelta(minutes=5 * i) for i in range(n)],
        "open": close, "high": close * 1.0001, "low": close * 0.9999,
        "close": close, "spread_mean": np.full(n, 0.02),
        "n_ticks": np.full(n, 50, dtype=np.int64), "ofi": np.zeros(n)})


def test_verdict_structure_and_overall():
    bars = synthetic_bars(n=6000, seed=2)
    v = validate.validate_candidate("donchian", bars, "5m",
                                    wiggle=("entry_n", [180, 240, 300]), n_trials=5)
    assert GATES <= set(v["gates"])
    for g in v["gates"].values():
        assert isinstance(g["pass"], bool)
    assert v["overall"] == all(g["pass"] for g in v["gates"].values())


def test_passes_on_clean_uptrend():
    """A steady uptrend: donchian holds long, low turnover, every walk-forward
    fold positive — the cost-survival and walk-forward gates must pass."""
    v = validate.validate_candidate("donchian", _trend_bars(), "5m",
                                    wiggle=("entry_n", [180, 240, 300]))
    assert v["gates"]["walk_forward"]["pass"] is True
    assert v["gates"]["cost_stress"]["pass"] is True


def test_walk_forward_folds_and_pass_on_uptrend():
    """Sequential OOS folds: a steady uptrend keeps donchian long, so the
    majority (here all) of folds are positive and pos_frac clears 0.5."""
    wf = validate.walk_forward("donchian", _trend_bars(), "5m", n_folds=5)
    assert len(wf["folds"]) == 5
    assert all({"ret", "sharpe", "n_bars"} <= set(f) for f in wf["folds"])
    assert sum(f["n_bars"] for f in wf["folds"]) == len(_trend_bars())
    assert wf["pos_frac"] >= 0.5


def test_deflated_sharpe_haircut_grows_with_trials():
    rng = np.random.default_rng(0)
    pnl = rng.normal(0.0001, 0.001, 5000)
    _, d1 = validate._deflated_sharpe(pnl, 1)
    _, d100 = validate._deflated_sharpe(pnl, 100)
    assert d100 < d1  # more trials tried -> bigger multiple-testing haircut
