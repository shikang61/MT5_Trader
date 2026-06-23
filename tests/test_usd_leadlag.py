from datetime import datetime, timedelta, timezone
import numpy as np
import polars as pl
from mt5_trader.strategies.usd_leadlag import UsdLeadLag, attach_eur

T0 = datetime(2026, 5, 12, tzinfo=timezone.utc)
def _bars(closes, col="close"):
    return pl.DataFrame({"ts": [T0 + timedelta(minutes=i) for i in range(len(closes))],
                         col: [float(c) for c in closes]})

def test_attach_eur_joins_and_ffills():
    gold = _bars([4000, 4001, 4002])
    eur = pl.DataFrame({"ts": [T0, T0 + timedelta(minutes=2)], "close": [1.17, 1.18]})
    out = attach_eur(gold, eur)
    assert out["eur_close"].to_list() == [1.17, 1.17, 1.18]   # minute 1 forward-filled

def test_long_when_eur_rises():
    # eur jumps up at bar 3 over lead_w=2 -> gold long for hold_h=2 bars
    eur = [1.0, 1.0, 1.0, 1.02, 1.02, 1.02, 1.02]
    bars = _bars([4000]*7).with_columns(eur_close=pl.Series([float(x) for x in eur]))
    sig = UsdLeadLag(lead_w=2, hold_h=2, theta_bp=0.0).signal(bars)
    assert sig[3] == 1.0 and sig[4] == 1.0   # gold follows eur up
    assert sig[6] == 0.0                      # hold expired

def test_short_when_eur_falls():
    eur = [1.0, 1.0, 1.0, 0.98, 0.98, 0.98, 0.98]
    bars = _bars([4000]*7).with_columns(eur_close=pl.Series([float(x) for x in eur]))
    sig = UsdLeadLag(lead_w=2, hold_h=2, theta_bp=0.0).signal(bars)
    assert sig[3] == -1.0 and sig[4] == -1.0

def test_theta_gate_blocks_small_moves():
    eur = [1.0, 1.0, 1.0, 1.00005, 1.00005, 1.00005]   # ~0.5bp move
    bars = _bars([4000]*6).with_columns(eur_close=pl.Series([float(x) for x in eur]))
    sig = UsdLeadLag(lead_w=2, hold_h=2, theta_bp=5.0).signal(bars)   # 5bp gate
    assert np.all(sig == 0.0)

def test_no_lookahead_prefix_stable():
    eur = [1.0, 1.0, 1.01, 1.01, 1.01, 1.0, 1.0]
    bars = _bars([4000]*7).with_columns(eur_close=pl.Series([float(x) for x in eur]))
    s = UsdLeadLag(lead_w=1, hold_h=2)
    full = s.signal(bars); trunc = s.signal(bars[:4])
    assert np.allclose(full[:4], trunc)
