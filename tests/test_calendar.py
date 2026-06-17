from datetime import datetime, timedelta, timezone

import polars as pl

from mt5_trader.features.calendar import BlackoutCfg, blackout_mask
from mt5_trader.risk.manager import RiskManager, Posture


def _events():
    return pl.DataFrame({
        "ts": [datetime(2026, 5, 12, 12, 30, tzinfo=timezone.utc),   # high USD (CPI)
               datetime(2026, 5, 12, 14, 0, tzinfo=timezone.utc)],   # medium EUR
        "impact": ["high", "medium"],
        "currency": ["USD", "EUR"],
        "title": ["CPI", "ECB speak"],
    })


def _ts(start, n, step_min=5):
    return [start + timedelta(minutes=step_min * i) for i in range(n)]


def test_mask_marks_window_only():
    cfg = BlackoutCfg(enabled=True, before_min=15, after_min=15,
                      min_impact="high", currencies=["USD"])
    ts = _ts(datetime(2026, 5, 12, 11, 30, tzinfo=timezone.utc), 36)  # 11:30..14:25
    m = blackout_mask(ts, _events(), cfg)
    inwin = [t for t, b in zip(ts, m) if b]
    assert min(inwin) == datetime(2026, 5, 12, 12, 15, tzinfo=timezone.utc)
    assert max(inwin) == datetime(2026, 5, 12, 12, 45, tzinfo=timezone.utc)


def test_disabled_is_noop():
    cfg = BlackoutCfg(enabled=False)
    ts = _ts(datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc), 12)
    assert not blackout_mask(ts, _events(), cfg).any()


def test_impact_and_currency_filter():
    ts = [datetime(2026, 5, 12, 14, 0, tzinfo=timezone.utc)]  # the medium EUR event
    high_usd = BlackoutCfg(enabled=True, min_impact="high", currencies=["USD"])
    assert not blackout_mask(ts, _events(), high_usd)[0]      # filtered out
    med_eur = BlackoutCfg(enabled=True, min_impact="medium", currencies=["EUR"])
    assert blackout_mask(ts, _events(), med_eur)[0]           # now caught


def _rm():
    p = Posture(max_leverage=2, loss_limit=0.04, target_vol=0.3)
    cfg = BlackoutCfg(enabled=True, before_min=15, after_min=15,
                      min_impact="high", currencies=["USD"])
    return RiskManager(p, cfg, _events())


def test_size_forced_flat_in_blackout():
    rm = _rm()
    assert rm.size(1.0, 0.3, datetime(2026, 5, 12, 12, 30, tzinfo=timezone.utc)) == 0.0
    assert rm.size(1.0, 0.3, datetime(2026, 5, 12, 18, 0, tzinfo=timezone.utc)) != 0.0
