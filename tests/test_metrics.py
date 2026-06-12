import numpy as np

from mt5_trader.backtest.metrics import max_drawdown, sharpe, summary


def test_sharpe_constant_zero_std():
    assert sharpe(np.zeros(100), 252) == 0.0


def test_sharpe_sign():
    rng = np.random.default_rng(0)
    r = rng.normal(0.001, 0.01, 10_000)
    assert sharpe(r, 252) > 0
    assert sharpe(-r, 252) < 0


def test_max_drawdown_monotonic():
    assert max_drawdown(np.array([1.0, 2.0, 3.0])) == 0.0


def test_max_drawdown_known():
    eq = np.array([100.0, 200.0, 100.0, 150.0])
    assert max_drawdown(eq) == -0.5


def test_summary_keys():
    eq = np.array([1.0, 1.01, 1.02])
    r = np.array([0.0, 0.01, 0.0099])
    s = summary(r, eq, 252)
    assert {"total_return", "sharpe", "max_drawdown", "hit_rate"} <= s.keys()
