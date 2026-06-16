from datetime import datetime, timezone

import numpy as np
import polars as pl

from mt5_trader.strategies.asian_sweep import AsianSweepFade


def _bars(rows):
    # rows: (datetime, high, low, close)
    return pl.DataFrame({
        "ts": [r[0] for r in rows],
        "high": [r[1] for r in rows],
        "low": [r[2] for r in rows],
        "close": [r[3] for r in rows],
    })


D = datetime(2026, 5, 12, tzinfo=timezone.utc)
def at(h, m=0):
    return D.replace(hour=h, minute=m)


# small params so test fixtures stay tiny
CFG = dict(session_end_hour=6, quiet_bars=2, confirm_bars=1, hold_bars=3)


def test_no_signal_before_session_end():
    # only Asian-window bars (hour < 6) -> never trade
    bars = _bars([(at(0), 100.0, 99.0, 99.5),
                  (at(1), 100.0, 99.2, 99.8),
                  (at(5), 99.9, 99.1, 99.5)])
    sig = AsianSweepFade(**CFG).signal(bars)
    assert np.all(sig == 0.0)


def test_short_on_high_sweep_reclaim():
    # Asian high=100 (set 00:00); after 06:00 sweep above then close back inside
    bars = _bars([
        (at(0), 100.0, 99.0, 99.5),       # 0  Asian window -> ah=100, al=99
        (at(6, 0), 100.5, 100.0, 100.4),  # 1  high>100 -> sweep up; pend at +1
        (at(6, 1), 100.2, 99.6, 99.8),    # 2  pend bar: close 99.8 < 100 -> SHORT, hold 3
        (at(6, 2), 99.9, 99.5, 99.7),     # 3  short
        (at(6, 3), 99.9, 99.5, 99.7),     # 4  short
        (at(6, 4), 99.9, 99.5, 99.7),     # 5  flat again
    ])
    sig = AsianSweepFade(**CFG).signal(bars)
    assert sig[2] == -1.0 and sig[3] == -1.0 and sig[4] == -1.0
    assert sig[5] == 0.0 and sig[0] == 0.0


def test_long_on_low_sweep_reclaim():
    bars = _bars([
        (at(0), 100.0, 99.0, 99.5),       # 0  ah=100, al=99
        (at(6, 0), 99.0, 98.5, 98.6),     # 1  low<99 -> sweep down; pend at +1
        (at(6, 1), 99.4, 98.8, 99.2),     # 2  pend bar: close 99.2 > 99 -> LONG, hold 3
        (at(6, 2), 99.5, 99.1, 99.3),     # 3  long
        (at(6, 3), 99.5, 99.1, 99.3),     # 4  long
        (at(6, 4), 99.5, 99.1, 99.3),     # 5  flat
    ])
    sig = AsianSweepFade(**CFG).signal(bars)
    assert sig[2] == 1.0 and sig[3] == 1.0 and sig[4] == 1.0
    assert sig[5] == 0.0


def test_no_entry_on_held_breakout():
    # sweep above but close STILL above the level at the confirm bar -> no fade
    bars = _bars([
        (at(0), 100.0, 99.0, 99.5),       # ah=100
        (at(6, 0), 100.5, 100.0, 100.4),  # sweep up
        (at(6, 1), 100.8, 100.2, 100.6),  # pend bar: close 100.6 > 100 -> held, no short
        (at(6, 2), 100.7, 100.3, 100.5),
    ])
    sig = AsianSweepFade(**CFG).signal(bars)
    assert np.all(sig == 0.0)


def test_asian_level_uses_window_only_not_later_high():
    # a higher high AFTER 06:00 must not raise the Asian level (else no sweep)
    bars = _bars([
        (at(0), 100.0, 99.0, 99.5),       # ah=100 from the window
        (at(6, 0), 100.5, 100.0, 100.4),  # high 100.5 > 100 -> sweep (fails if ah=100.5)
        (at(6, 1), 100.2, 99.6, 99.8),    # reclaim -> short
        (at(6, 2), 99.9, 99.5, 99.7),
        (at(6, 3), 99.9, 99.5, 99.7),
    ])
    sig = AsianSweepFade(**CFG).signal(bars)
    assert sig[2] == -1.0


def test_no_lookahead_prefix_stable():
    # appending future bars must not change earlier signals
    bars = _bars([
        (at(0), 100.0, 99.0, 99.5),
        (at(6, 0), 100.5, 100.0, 100.4),
        (at(6, 1), 100.2, 99.6, 99.8),
        (at(6, 2), 99.9, 99.5, 99.7),
        (at(6, 3), 99.9, 99.5, 99.7),
        (at(6, 4), 99.9, 99.5, 99.7),
    ])
    full = AsianSweepFade(**CFG).signal(bars)
    trunc = AsianSweepFade(**CFG).signal(bars[:4])
    assert np.allclose(full[:4], trunc)
