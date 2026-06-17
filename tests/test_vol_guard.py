from datetime import datetime, timedelta, timezone

import numpy as np
import polars as pl

from mt5_trader.features.vol_guard import VolGuardCfg, vol_spike_mask
from mt5_trader.risk.manager import RiskManager, Posture


def _bars(closes, highs=None, lows=None):
    n = len(closes)
    ts = [datetime(2026, 5, 12, tzinfo=timezone.utc) + timedelta(minutes=5 * i)
          for i in range(n)]
    cols = {"ts": ts, "close": [float(c) for c in closes]}
    if highs is not None:
        cols["high"] = [float(h) for h in highs]
        cols["low"] = [float(x) for x in lows]
    return pl.DataFrame(cols)


def test_disabled_is_noop():
    closes = np.linspace(2000, 2000, 50).tolist()
    closes[25] = 2200  # huge jump
    m = vol_spike_mask(_bars(closes), VolGuardCfg(enabled=False))
    assert not m.any()


def test_return_gate_trips_on_jump():
    closes = [2000.0] * 50
    for i in range(30, 50):
        closes[i] = 2000.0 * 1.01  # +1% step up then hold: only bar 30 is the jump
    cfg = VolGuardCfg(enabled=True, ret_limit_bps=50.0)
    m = vol_spike_mask(_bars(closes), cfg)
    assert m[30] and not m[29] and not m[31]


def test_return_gate_ignores_normal_moves():
    rng = np.random.default_rng(0)
    closes = 2000 * np.cumprod(1 + rng.normal(0, 0.0005, 500))  # ~5bp/bar noise
    m = vol_spike_mask(_bars(closes.tolist()), VolGuardCfg(enabled=True, ret_limit_bps=50.0))
    assert not m.any()


def test_atr_gate_trips_on_range_expansion():
    n = 400
    base = [2000.0] * n
    # tight true range (~1) everywhere, then a sustained cluster of ~10-wide bars.
    # ATR(14) smooths, so the gate catches the sustained expansion, not a lone bar.
    highs = [c + 0.5 for c in base]
    lows = [c - 0.5 for c in base]
    for i in range(350, 380):
        highs[i], lows[i] = 2005.0, 1995.0  # TR ~10 vs ~1 baseline
    cfg = VolGuardCfg(enabled=True, atr_n=14, atr_base_n=288, atr_mult=3.0,
                      ret_limit_bps=1e9)  # disable return gate to isolate ATR gate
    m = vol_spike_mask(_bars(base, highs, lows), cfg)
    assert m[370] and not m[340]  # inside the cluster trips; before it does not


def test_close_only_frame_uses_return_gate_only():
    # no high/low (synthetic-spread shape): must not crash, return gate still works
    closes = [2000.0] * 40
    closes[20] = 2000.0 * 1.02
    m = vol_spike_mask(_bars(closes), VolGuardCfg(enabled=True, ret_limit_bps=50.0))
    assert m[20]


def test_incremental_equals_batch():
    """Live recomputes mask[-1] on a rolling window each bar; backtest computes
    the whole array once. The last value must match the batch value at that bar
    or live and backtest drift."""
    rng = np.random.default_rng(1)
    rets = rng.normal(0, 0.0005, 600)
    rets[400] = 0.012  # a spike somewhere
    closes = (2000 * np.cumprod(1 + rets))
    highs = (closes * 1.0008).tolist()
    lows = (closes * 0.9992).tolist()
    bars = _bars(closes.tolist(), highs, lows)
    cfg = VolGuardCfg(enabled=True, atr_n=14, atr_base_n=288, atr_mult=3.0)
    full = vol_spike_mask(bars, cfg)
    for t in (390, 400, 401, 500, 599):  # all have >= atr_base_n history
        last = vol_spike_mask(bars[: t + 1], cfg)[-1]
        assert last == full[t], f"drift at bar {t}"


def _rm(vg: VolGuardCfg) -> RiskManager:
    p = Posture(max_leverage=2, loss_limit=0.04, target_vol=0.3)
    return RiskManager(p, vol_guard=vg)


def test_vol_halt_reflects_last_bar():
    closes = [2000.0] * 40
    closes[-1] = 2000.0 * 1.02  # spike on the latest bar
    assert _rm(VolGuardCfg(enabled=True)).vol_halt(_bars(closes))
    assert not _rm(VolGuardCfg(enabled=False)).vol_halt(_bars(closes))
    calm = [2000.0 + 0.1 * i for i in range(40)]
    assert not _rm(VolGuardCfg(enabled=True)).vol_halt(_bars(calm))
