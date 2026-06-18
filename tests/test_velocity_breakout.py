from datetime import datetime, timedelta, timezone

import numpy as np
import polars as pl

from mt5_trader.strategies.velocity_breakout import VelocityBreakout


def make_day(date: str, closes, start_hour=12, start_min=0) -> pl.DataFrame:
    """1-min bars for one UTC day. OHLC all = close so ATR is well-defined and
    only close-based logic is exercised."""
    t0 = datetime.fromisoformat(f"{date} {start_hour:02d}:{start_min:02d}").replace(
        tzinfo=timezone.utc)
    ts = [t0 + timedelta(minutes=i) for i in range(len(closes))]
    c = pl.Series("close", [float(x) for x in closes])
    return pl.DataFrame({
        "ts": ts, "open": c, "high": c, "low": c, "close": c,
        "spread_mean": [0.0] * len(closes), "n_ticks": [1] * len(closes),
    })


# Small test params: single arm 12:30, er_n=5, hold 10 bars.
def strat():
    return VelocityBreakout(arm_times=["12:30"], pre_min=30, arm_min=30,
                            er_n=5, er_min=0.5, hold_bars=10)


def test_clean_break_enters_long_and_holds():
    # 30 flat pre-range bars (12:00-12:30) -> pre_hi=pre_lo=100; clean up-break.
    closes = [100.0] * 30 + [100.5] * 15
    pos = strat().signal(make_day("2026-01-05", closes))
    assert np.all(pos[:30] == 0.0)          # pre-range + before break
    assert np.all(pos[30:40] == 1.0)        # entry bar + 9 = hold_bars(10) bars long
    assert np.all(pos[40:] == 0.0)          # flat after the hold


def test_clean_break_down_enters_short():
    closes = [100.0] * 30 + [99.5] * 15
    pos = strat().signal(make_day("2026-01-05", closes))
    assert np.all(pos[30:40] == -1.0)
    assert np.all(pos[40:] == 0.0)


def test_churn_break_rejected():
    # pre_hi=101, pre_lo=99; the first close >101 arrives via a choppy path -> low ER.
    pre = [101.0, 99.0] + [100.0] * 28           # 30 bars, sets the range
    scan = [100.0, 101.0, 100.0, 101.0, 100.0, 101.2] + [100.0] * 9
    pos = strat().signal(make_day("2026-01-05", pre + scan))
    assert np.all(pos == 0.0)                     # churn break fails the ER gate


def test_one_entry_per_arm_per_day():
    # clean break, hold ends at idx 40, price dips then breaks again -> no re-entry.
    closes = [100.0] * 30 + [100.5] * 5 + [100.0] * 5 + [101.0] * 10
    pos = strat().signal(make_day("2026-01-05", closes))
    assert np.all(pos[30:40] == 1.0)
    assert np.all(pos[40:] == 0.0)               # arm consumed for the day


def test_no_entry_after_scan_window():
    # first break only at 13:00 (idx 60), past [12:30, 13:00) scan window.
    closes = [100.0] * 60 + [101.0] * 10
    pos = strat().signal(make_day("2026-01-05", closes))
    assert np.all(pos == 0.0)


def test_state_resets_across_days():
    day1 = make_day("2026-01-05", [100.0] * 30 + [100.5] * 15)
    day2 = make_day("2026-01-06", [100.0] * 30 + [100.5] * 15)
    pos = strat().signal(pl.concat([day1, day2]))
    assert np.all(pos[30:40] == 1.0)             # day 1 entry
    assert np.all(pos[45 + 30:45 + 40] == 1.0)   # day 2 entry (offset by day1 length 45)
