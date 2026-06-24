import json
from datetime import datetime, timedelta, timezone

import numpy as np
import polars as pl

from mt5_trader.live.runner import Book, PortfolioRunner, RunnerCfg, cap_lots
from mt5_trader.risk.manager import Posture, RiskManager

NOW = datetime(2026, 6, 18, 0, 0, tzinfo=timezone.utc)

# At equity 100_000, contract 100, price 4000: 1.0 frac == 0.25 lots
# (0.25 * 100 * 4000 / 100_000 = 1.0).

def test_above_cap_clipped_long():
    lots, bound = cap_lots(0.25, 100.0, 4000.0, 100_000.0, 0.5)
    assert bound is True
    assert abs(lots - 0.125) < 1e-9          # 0.5 frac -> 0.125 lots
    assert lots > 0                          # sign preserved (long)

def test_above_cap_clipped_short():
    lots, bound = cap_lots(-0.25, 100.0, 4000.0, 100_000.0, 0.5)
    assert bound is True
    assert abs(lots + 0.125) < 1e-9          # clipped to -0.125 lots
    assert lots < 0                          # sign preserved (short)

def test_clipped_frac_equals_cap():
    lots, bound = cap_lots(0.40, 100.0, 4000.0, 100_000.0, 0.5)
    frac = lots * 100.0 * 4000.0 / 100_000.0
    assert bound is True
    assert abs(abs(frac) - 0.5) < 1e-9       # exposure sits exactly at the cap

def test_below_cap_unchanged():
    lots, bound = cap_lots(0.10, 100.0, 4000.0, 100_000.0, 0.5)
    assert bound is False
    assert lots == 0.10                      # 0.4 frac < 0.5 -> untouched

def test_zero_lots_untouched():
    # vol_halt zeroes lots before the cap runs -> halt must win, cap a no-op.
    assert cap_lots(0.0, 100.0, 4000.0, 100_000.0, 0.5) == (0.0, False)

def test_cap_zero_disables():
    lots, bound = cap_lots(5.0, 100.0, 4000.0, 100_000.0, 0.0)
    assert bound is False
    assert lots == 5.0

def test_nonpositive_equity_or_price_safe():
    assert cap_lots(5.0, 100.0, 4000.0, 0.0, 0.5) == (5.0, False)
    assert cap_lots(5.0, 100.0, 0.0, 100_000.0, 0.5) == (5.0, False)


class _Strat:
    """Constant full-long signal so the netted book exposure is large."""
    def signal(self, bars):
        return np.ones(len(bars))


class CapFakeGW:
    """Minimal gateway: one symbol, fixed bars/spec/price, records reconcile lots."""
    def __init__(self, equity, price=4000.0, contract=100.0):
        self._eq, self._price, self._contract = equity, price, contract
        self.reconciled = {}

    def equity(self):
        return self._eq

    def symbol_spec(self, symbol):
        return {"contract_size": self._contract, "lot_step": 0.01,
                "lot_min": 0.01, "lot_max": 100.0}

    def bars(self, symbol, timeframe="M1", n=3000):
        # 300 constant-price bars: > the 240-bar vol window so realized_vol is
        # defined (== 0 on a flat price), which makes risk.size's leverage hit the
        # max_leverage=2 cap deterministically -> a known, over-cap netted exposure.
        ts = [NOW + timedelta(minutes=i) for i in range(300)]
        return pl.DataFrame({
            "ts": ts,
            "open": [self._price] * 300, "high": [self._price] * 300,
            "low": [self._price] * 300, "close": [self._price] * 300,
            "spread_mean": [0.1] * 300, "tick_volume": [1.0] * 300,
        })

    def reconcile(self, symbol, lots, sl=0.0, tp=0.0, magic=None):
        self.reconciled[symbol] = lots
        return lots

    def position_lots(self, symbol, magic=None):
        return 0.0

    def flatten(self, symbol, magic=None):
        pass


def _runner(gw, cap, tmp_path):
    posture = Posture(max_leverage=2.0, loss_limit=0.05, target_vol=0.3)
    rm = RiskManager(posture)
    # two full-long books on XAUUSD -> netted exposure stacks well past the cap
    books = [Book(strategy=_Strat(), symbol="XAUUSD", weight=0.5),
             Book(strategy=_Strat(), symbol="XAUUSD", weight=0.5)]
    cfg = RunnerCfg(symbol="PORTFOLIO", exposure_cap=cap,
                    sl_atr_mult=0.0, log_path=tmp_path / "p.jsonl")
    r = PortfolioRunner(gw, books, rm, cfg)
    rm.roll_day(NOW, gw.equity())
    return r


def _last_step(path):
    steps = [json.loads(ln) for ln in path.read_text().splitlines()
             if '"STEP"' in ln]
    return steps[-1]


def test_step_clips_over_aligned_net(tmp_path):
    gw = CapFakeGW(equity=100_000.0)
    _runner(gw, cap=0.5, tmp_path=tmp_path).step(now=NOW)  # fixed now: avoid the 21:58-22:03 reopen-guard window
    # net exposure is clipped: reconcile sees |lots*contract*price/equity| == 0.5
    frac = gw.reconciled["XAUUSD"] * 100.0 * 4000.0 / 100_000.0
    assert abs(abs(frac) - 0.5) < 1e-6
    assert _last_step(tmp_path / "p.jsonl")["fills"]["XAUUSD"]["cap"] is True


def test_step_cap_off_does_not_clip(tmp_path):
    gw = CapFakeGW(equity=100_000.0)
    _runner(gw, cap=0.0, tmp_path=tmp_path).step(now=NOW)  # fixed now: avoid the 21:58-22:03 reopen-guard window
    assert _last_step(tmp_path / "p.jsonl")["fills"]["XAUUSD"]["cap"] is False
