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


def make_ohlc(rows):
    """rows: list of (open-irrelevant close, high, low). close drives ret."""
    t0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    return pl.DataFrame({
        "ts": [t0 + timedelta(minutes=i) for i in range(len(rows))],
        "close": [float(c) for c, _, _ in rows],
        "high": [float(h) for _, h, _ in rows],
        "low": [float(low) for _, _, low in rows],
        "spread_mean": [0.0] * len(rows),
    })


def test_intrabar_stop_caps_bar_loss():
    # sig turns on at bar 1 -> entry at bar 2 (pos[t]=sig[t-1]); ATR at the
    # entry-decision bar (bar 1) is TR=4, so the stop sits 2*ATR=8 below the
    # entry price 100 -> 92. Bar 3 wicks to low 91 (< 92) then closes 99.5; the
    # stop must realize -8% (100->92), not the -0.5% close-to-close.
    rows = [(100, 102, 98), (100, 102, 98), (100, 100, 100), (99.5, 100, 91)]
    bars = make_ohlc(rows)
    sig = np.array([0.0, 1.0, 1.0, 1.0])
    stopped = run(bars, sig, BTConfig(slippage_bps=0.0, sl_atr_mult=2.0,
                                      tp_atr_mult=0.0, atr_window=1))
    plain = run(bars, sig, BTConfig(slippage_bps=0.0))
    assert abs(stopped.bars["bar_ret"][3] - (-0.08)) < 1e-9   # capped at the stop
    assert abs(plain.bars["bar_ret"][3] - (-0.005)) < 1e-9    # full close-to-close
    assert stopped.metrics["total_turnover"] > plain.metrics["total_turnover"]


def test_stops_default_off_is_unchanged():
    rows = [(100, 100, 100), (100, 101, 90), (110, 110, 100)]
    bars = make_ohlc(rows)
    sig = np.ones(len(rows))
    a = run(bars, sig, BTConfig(slippage_bps=0.0))
    b = run(bars, sig, BTConfig(slippage_bps=0.0, sl_atr_mult=0.0, tp_atr_mult=0.0))
    assert a.metrics["total_return"] == b.metrics["total_return"]
