"""When the broker has flattened the net position (e.g. the disaster stop fired)
but the strategy signal is unchanged, the runner must DROP its stale chandelier
trail so the re-entry anchors a fresh stop at the current price. Otherwise it
re-enters into an already-breached stop and churns (open -> instant stop-out loop
seen live 2026-06-24: 59 deals, sl stuck below a rallying short)."""
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import polars as pl

from mt5_trader.live.runner import Book, PortfolioRunner, RunnerCfg
from mt5_trader.risk.manager import Posture, RiskManager

NOW = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)   # outside the reopen-guard window
PRICE, TR = 4000.0, 2.0                                    # constant true range -> ATR ~ 2


class _Short:
    def signal(self, bars):
        return -np.ones(len(bars))   # constant short


class FakeGW:
    def __init__(self, flat: bool, equity=100_000.0, contract=100.0):
        self._flat, self._eq, self._contract = flat, equity, contract
        self.reconciled = {}

    def equity(self):
        return self._eq

    def symbol_spec(self, s):
        return {"contract_size": self._contract, "lot_step": 0.01,
                "lot_min": 0.01, "lot_max": 100.0}

    def position_lots(self, s, magic=None):
        return 0.0 if self._flat else -0.07

    def bars(self, s, timeframe="M5", n=3000):
        m = 300
        ts = [NOW - timedelta(minutes=5 * (m - i)) for i in range(m)]
        c = [PRICE] * m
        return pl.DataFrame({"ts": ts, "open": c, "high": [PRICE + 1] * m,
                             "low": [PRICE - 1] * m, "close": c,
                             "spread_mean": [0.1] * m, "tick_volume": [1.0] * m})

    def reconcile(self, s, lots, sl=0.0, tp=0.0, magic=None):
        self.reconciled[s] = {"lots": lots, "sl": sl}
        return lots

    def flatten(self, s, magic=None):
        pass


def _runner(gw, tmp_path):
    rm = RiskManager(Posture(max_leverage=2.0, loss_limit=0.05, target_vol=0.3))
    books = [Book(strategy=_Short(), symbol="XAUUSD", weight=1.0, timeframe="M5")]
    cfg = RunnerCfg(symbol="PORTFOLIO", exposure_cap=0.0, sl_atr_mult=8.0,
                    atr_window=14, log_path=tmp_path / "p.jsonl")
    r = PortfolioRunner(gw, books, rm, cfg)
    rm.roll_day(NOW, gw.equity())
    return r


def _sl(tmp_path):
    steps = [json.loads(ln) for ln in (tmp_path / "p.jsonl").read_text().splitlines()
             if '"STEP"' in ln]
    return steps[-1]["fills"]["XAUUSD"]["sl"]


def test_flat_broker_reanchors_stale_trail(tmp_path):
    gw = FakeGW(flat=True)                       # broker flat (stopped out)
    r = _runner(gw, tmp_path)
    # stale short trail anchored low -> stale stop 3996 sits BELOW price 4000 (breached)
    r._trail["XAUUSD"] = (-1.0, TR, 3990.0, 3980.0)
    r.step(now=NOW)
    sl = _sl(tmp_path)
    # re-anchored fresh: a short's stop must sit ABOVE price (= price + 8*ATR), not 3996
    assert sl > PRICE
    assert abs(sl - (PRICE + 8 * TR)) < 0.6      # ~4016


def test_in_position_widens_breached_trail(tmp_path):
    # Broker still holding, so the trail STATE is kept (extreme 3980, not reset).
    # But that stale anchor yields a stop of 3980 + 16 = 3996 — BELOW price 4000, a
    # breached short stop the gateway would clamp to ~spread and churn against. The
    # runner widens the *attached* stop off the breach to price + 8*ATR (~4016) so a
    # re-attach stays wide. (Re-anchor-on-flat alone missed this: a re-open beats the
    # flat check, so the position reads "held" while the trail is breached.)
    gw = FakeGW(flat=False)
    r = _runner(gw, tmp_path)
    r._trail["XAUUSD"] = (-1.0, TR, 3990.0, 3980.0)
    r.step(now=NOW)
    sl = _sl(tmp_path)
    assert sl > PRICE                            # valid: short stop above market
    assert abs(sl - (PRICE + 8 * TR)) < 0.6      # widened off the breach -> ~4016
