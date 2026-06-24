"""Per-symbol reconcile isolation in the portfolio step.

The whole step runs under resilient_loop's single try/except, and reconcile is
called inline in the per-symbol loop. So a broker error on one symbol used to
abort the entire cycle: every symbol ordered after it was skipped and the STEP
log line was never written (no audit / no attribution for the cycle). Each
symbol's reconcile must be isolated — one symbol's failure records an error on
that fill and the cycle carries on.
"""
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import polars as pl

from mt5_trader.live.mt5_gateway import MT5Error
from mt5_trader.live.runner import Book, PortfolioRunner, RunnerCfg
from mt5_trader.risk.manager import Posture, RiskManager

NOW = datetime(2026, 6, 18, 0, 0, tzinfo=timezone.utc)  # outside the reopen-guard window


class _Strat:
    def signal(self, bars):
        return np.ones(len(bars))


class FaultyGW:
    """Two symbols; reconcile raises for `bad_symbol`, succeeds for the rest."""
    def __init__(self, bad_symbol, equity=100_000.0, price=4000.0, contract=100.0):
        self.bad, self._eq, self._price, self._contract = bad_symbol, equity, price, contract
        self.reconciled = {}

    def equity(self):
        return self._eq

    def symbol_spec(self, symbol):
        return {"contract_size": self._contract, "lot_step": 0.01,
                "lot_min": 0.01, "lot_max": 100.0}

    def bars(self, symbol, timeframe="M1", n=3000):
        ts = [NOW + timedelta(minutes=i) for i in range(300)]
        return pl.DataFrame({
            "ts": ts,
            "open": [self._price] * 300, "high": [self._price] * 300,
            "low": [self._price] * 300, "close": [self._price] * 300,
            "spread_mean": [0.1] * 300, "tick_volume": [1.0] * 300,
        })

    def reconcile(self, symbol, lots, sl=0.0, tp=0.0, magic=None):
        if symbol == self.bad:
            raise MT5Error("order_send failed: 10018 Market closed")
        self.reconciled[symbol] = lots
        return lots

    def position_lots(self, symbol, magic=None):
        return 0.0

    def flatten(self, symbol, magic=None):
        pass


def _runner(gw, tmp_path):
    rm = RiskManager(Posture(max_leverage=2.0, loss_limit=0.05, target_vol=0.3))
    books = [Book(strategy=_Strat(), symbol="XAGUSD", weight=0.5),
             Book(strategy=_Strat(), symbol="XAUUSD", weight=0.5)]
    cfg = RunnerCfg(symbol="PORTFOLIO", exposure_cap=0.0, sl_atr_mult=0.0,
                    log_path=tmp_path / "p.jsonl")
    r = PortfolioRunner(gw, books, rm, cfg)
    rm.roll_day(NOW, gw.equity())
    return r


def _last_step(path):
    steps = [json.loads(ln) for ln in path.read_text().splitlines() if '"STEP"' in ln]
    return steps[-1]


def test_one_symbol_failure_does_not_skip_others(tmp_path):
    # XAGUSD sorts before XAUUSD, so it reconciles first. Its failure must not
    # stop XAUUSD (the later symbol) from trading.
    gw = FaultyGW(bad_symbol="XAGUSD")
    _runner(gw, tmp_path).step(now=NOW)            # must not raise
    assert "XAUUSD" in gw.reconciled               # later symbol still traded
    assert gw.reconciled["XAUUSD"] != 0.0


def test_failed_symbol_records_error_and_zero_fill(tmp_path):
    gw = FaultyGW(bad_symbol="XAGUSD")
    _runner(gw, tmp_path).step(now=NOW)
    fill = _last_step(tmp_path / "p.jsonl")["fills"]["XAGUSD"]
    assert fill["traded_lots"] == 0.0
    assert "10018" in fill["error"]


def test_step_logged_despite_failure(tmp_path):
    # The STEP audit line must still be written even when a symbol errored.
    gw = FaultyGW(bad_symbol="XAGUSD")
    _runner(gw, tmp_path).step(now=NOW)
    steps = [ln for ln in (tmp_path / "p.jsonl").read_text().splitlines() if '"STEP"' in ln]
    assert len(steps) == 1


def test_portfolio_runner_routes_gateway_events_to_log(tmp_path):
    gw = FaultyGW(bad_symbol="NONE")           # nothing fails; we only check wiring
    r = _runner(gw, tmp_path)
    assert gw.on_event == r._log
