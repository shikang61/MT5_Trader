"""Reopen disaster-stop guard: across the 22:00 UTC reopen window the portfolio
runner detaches its broker SL (the sub-second reopen wick must not sweep it)."""
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import polars as pl

from mt5_trader.live.runner import Book, PortfolioRunner, RunnerCfg, in_reopen_guard
from mt5_trader.risk.manager import Posture, RiskManager

W = ("21:58", "22:03")
NOW0 = datetime(2026, 6, 18, 0, 0, tzinfo=timezone.utc)


def _d(h, m):
    return datetime(2026, 6, 18, h, m, tzinfo=timezone.utc)


def test_in_reopen_guard_window():
    assert not in_reopen_guard(_d(21, 57), W)
    assert in_reopen_guard(_d(21, 58), W)        # inclusive start
    assert in_reopen_guard(_d(22, 0), W)
    assert in_reopen_guard(_d(22, 2), W)
    assert not in_reopen_guard(_d(22, 3), W)     # exclusive end
    assert not in_reopen_guard(_d(15, 0), W)


def test_in_reopen_guard_none_disables():
    assert not in_reopen_guard(_d(22, 0), None)


class _Strat:
    def signal(self, bars):
        return -np.ones(len(bars))               # constant short -> SL trails above


class GuardFakeGW:
    def __init__(self, equity=100_000.0, price=4000.0, contract=100.0):
        self._eq, self._price, self._contract = equity, price, contract
        self.reconciled = {}
        self.sltp_calls = []

    def equity(self):
        return self._eq

    def symbol_spec(self, s):
        return {"contract_size": self._contract, "lot_step": 0.01,
                "lot_min": 0.01, "lot_max": 100.0}

    def bars(self, s, timeframe="M1", n=3000):
        ts = [NOW0 + timedelta(minutes=i) for i in range(300)]
        px = [self._price + (i % 5) * 0.5 for i in range(300)]   # vary -> ATR>0
        return pl.DataFrame({"ts": ts, "open": px,
                             "high": [p + 1 for p in px], "low": [p - 1 for p in px],
                             "close": px, "spread_mean": [0.1] * 300,
                             "tick_volume": [1.0] * 300})

    def reconcile(self, s, lots, sl=0.0, tp=0.0, magic=None):
        self.reconciled[s] = (lots, sl)
        return lots

    def set_position_sltp(self, s, sl=0.0, tp=0.0, magic=None):
        self.sltp_calls.append((s, sl, tp))

    def position_lots(self, s, magic=None):
        return 0.0

    def flatten(self, s, magic=None):
        pass


def _runner(gw, tmp_path):
    rm = RiskManager(Posture(max_leverage=2.0, loss_limit=0.05, target_vol=0.3))
    books = [Book(strategy=_Strat(), symbol="XAUUSD", weight=1.0)]
    cfg = RunnerCfg(symbol="PORTFOLIO", sl_atr_mult=8.0, exposure_cap=0.0,
                    reopen_guard=W, log_path=tmp_path / "p.jsonl")
    r = PortfolioRunner(gw, books, rm, cfg)
    rm.roll_day(NOW0, gw.equity())
    return r


def _step_sl(path):
    steps = [json.loads(ln) for ln in path.read_text().splitlines() if '"STEP"' in ln]
    return steps[-1]["fills"]["XAUUSD"]["sl"]


def test_step_out_of_window_attaches_sl(tmp_path):
    gw = GuardFakeGW()
    _runner(gw, tmp_path).step(now=_d(15, 0))
    assert gw.reconciled["XAUUSD"][1] != 0.0     # chandelier SL passed to reconcile
    assert _step_sl(tmp_path / "p.jsonl") != 0.0
    assert gw.sltp_calls == []                   # no detach outside the window


def test_step_in_window_suppresses_and_detaches_sl(tmp_path):
    gw = GuardFakeGW()
    _runner(gw, tmp_path).step(now=_d(22, 0))
    assert gw.reconciled["XAUUSD"][1] == 0.0     # SL suppressed to reconcile
    assert _step_sl(tmp_path / "p.jsonl") == 0.0
    assert ("XAUUSD", 0.0, 0.0) in gw.sltp_calls  # parked broker SL detached
