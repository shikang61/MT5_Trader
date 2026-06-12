from datetime import datetime, timedelta, timezone

import numpy as np
import polars as pl

from mt5_trader.backtest.engine import BTConfig, run


def make_bars(closes, spread=0.0):
    t0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    return pl.DataFrame({
        "ts": [t0 + timedelta(minutes=i) for i in range(len(closes))],
        "close": [float(c) for c in closes],
        "spread_mean": [spread] * len(closes),
    })


def test_no_lookahead():
    """A signal fired on the jump bar must not capture the jump itself."""
    k = 5
    closes = [100.0] * k + [110.0] * 5  # 10% jump at bar k
    bars = make_bars(closes)
    sig = np.zeros(len(closes))
    sig[k:] = 1.0  # signal first appears on the jump bar
    res = run(bars, sig, BTConfig(slippage_bps=0.0))
    # position during the jump bar comes from sig[k-1] == 0 -> no jump pnl
    assert res.bars["pnl"][k] == 0.0
    assert res.metrics["total_return"] == 0.0


def test_captures_move_after_signal():
    closes = [100.0, 100.0, 110.0]
    sig = np.array([1.0, 1.0, 1.0])
    res = run(make_bars(closes), sig, BTConfig(slippage_bps=0.0))
    assert abs(res.metrics["total_return"] - 0.10) < 1e-12


def test_costs_reduce_pnl():
    closes = [100.0] * 10
    sig = np.tile([1.0, -1.0], 5)  # churn
    res = run(make_bars(closes, spread=0.1), sig, BTConfig(slippage_bps=1.0))
    assert res.metrics["total_return"] < 0
    assert res.metrics["total_turnover"] > 0


def test_leverage_scales_returns():
    closes = [100.0, 100.0, 101.0]
    sig = np.ones(3)
    r1 = run(make_bars(closes), sig, BTConfig(leverage=1.0, slippage_bps=0.0))
    r3 = run(make_bars(closes), sig, BTConfig(leverage=3.0, slippage_bps=0.0))
    assert abs(r3.metrics["total_return"] - 3 * r1.metrics["total_return"]) < 1e-9
