#!/usr/bin/env python
"""RESEARCH (proxy): does a breakout armed at the US data-drop time pay on gold?

O1 (event straddle) can't be backtested directly: the calendar holds only
forward events (Jun 17 FOMC) and the price data ends Jun 16, so there is zero
event x price overlap, and 1 month of gold has too few events to gate anyway.

Proxy: most gold-moving US releases land at a FIXED time, 12:30 UTC (08:30 ET:
NFP, CPI, PPI, retail, claims). Arm an opening-range breakout around 12:30 and
compare its per-event payoff against the SAME logic at other arm-times (00:00
reopen, 06:00 London, 14:00, 18:00 FOMC-ish). If 12:30 stands out net of the
(wide) event-time spread, the straddle thesis has legs and the calendar+history
lift is worth it. If 12:30 looks like any other hour, O1 is likely dead on gold.

This is structurally london_orb (session_vol.LondonBreakout) retimed to the
minute and held for a fixed horizon instead of to flat_hour. Close-based breaks,
no lookahead (pre-range uses only [arm-pre, arm) bars).

    uv run scripts/research_event_breakout.py
"""
from __future__ import annotations

import numpy as np
import polars as pl

from mt5_trader.pipeline.data import build_bars

ARM_TIMES = ["00:00", "06:00", "12:30", "14:00", "18:00"]  # UTC; 12:30 = US data drop
PRE_MIN = 30        # pre-range window length (min) before the arm time
ARM_MIN = 30        # scan this many min after the arm for the first break
HOLD_MIN = 60       # hold this many min after entry, then flat
BUFFER_BPS = 0.0    # require the break to clear the range edge by this much
SLIP_BPS = 0.5      # one-way slippage on top of half-spread


def hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def run_arm(df: pl.DataFrame, arm: int, hold: int = HOLD_MIN, slip: float = SLIP_BPS) -> dict:
    """One arm-time. Per day: build the pre-range, take the first close-break in
    the arm window, hold `hold` min, book the net return. Returns aggregate stats."""
    date = df["date"].to_numpy()
    tod = df["tod"].to_numpy()
    week = df["week"].to_numpy()
    high, low, close = (df[c].to_numpy().astype(float) for c in ("high", "low", "close"))
    spf = df["spf"].to_numpy().astype(float)

    rets, sides, weeks = [], [], []
    n_days = 0
    for d in np.unique(date):
        day = np.where(date == d)[0]
        t = tod[day]
        pre = day[(t >= arm - PRE_MIN) & (t < arm)]
        win = day[(t >= arm) & (t < arm + ARM_MIN)]
        if pre.size < PRE_MIN // 2 or win.size == 0:
            continue
        n_days += 1
        hi, lo = high[pre].max(), low[pre].min()
        up, dn = hi * (1 + BUFFER_BPS * 1e-4), lo * (1 - BUFFER_BPS * 1e-4)
        side = entry = 0
        for i in win:
            if close[i] > up:
                side, entry = 1, i
                break
            if close[i] < dn:
                side, entry = -1, i
                break
        if side == 0:
            continue
        exit_i = min(entry + hold, day[-1])
        gross = side * (close[exit_i] / close[entry] - 1.0)
        cost = spf[entry] / 2 + spf[exit_i] / 2 + 2 * slip * 1e-4
        rets.append(gross - cost)
        sides.append(side)
        weeks.append(week[entry])

    r = np.array(rets)
    if r.size == 0:
        return {"n_days": n_days, "n": 0}
    wk = {int(w): float(np.array(rets)[np.array(weeks) == w].sum() * 1e4) for w in np.unique(weeks)}
    return {
        "n_days": n_days, "n": r.size,
        "avg_bps": r.mean() * 1e4, "hit": (r > 0).mean(),
        "sum_bps": r.sum() * 1e4, "long_frac": np.mean(np.array(sides) > 0),
        "wk_pos": sum(v > 0 for v in wk.values()), "wk_tot": len(wk),
        "max_share": np.abs(r).max() / np.abs(r).sum() if r.sum() != 0 else float("nan"),
        "rets": r, "sides": np.array(sides), "weeks": np.array(weeks),
    }


def main() -> None:
    bars = build_bars("XAUUSD", "1m").sort("ts")
    df = bars.with_columns(
        date=pl.col("ts").dt.date(),
        tod=pl.col("ts").dt.hour().cast(pl.Int32) * 60 + pl.col("ts").dt.minute().cast(pl.Int32),
        week=pl.col("ts").dt.week().cast(pl.Int32),
        spf=pl.col("spread_mean").fill_null(0) / pl.col("close"),
    )
    print(f"XAUUSD 1m  pre={PRE_MIN} arm={ARM_MIN} hold={HOLD_MIN} buf={BUFFER_BPS}bp slip={SLIP_BPS}bp")
    print(f"{'arm(UTC)':<10}{'days':>5}{'trades':>7}{'avg_bps':>9}{'hit':>6}{'sum_bps':>9}{'long%':>7}{'wk+':>6}")
    for s in ARM_TIMES:
        a = run_arm(df, hm(s))
        if a["n"] == 0:
            print(f"{s:<10}{a['n_days']:>5}{'0':>7}{'-':>9}{'-':>6}{'-':>9}{'-':>7}{'-':>6}")
            continue
        tag = "  <- US data" if s == "12:30" else ""
        print(f"{s:<10}{a['n_days']:>5}{a['n']:>7}{a['avg_bps']:>+9.2f}{a['hit']:>6.0%}"
              f"{a['sum_bps']:>+9.1f}{a['long_frac']:>7.0%}{a['wk_pos']:>3}/{a['wk_tot']}{tag}")
    print("\navg_bps = mean net return/trade; sum_bps = total; wk+ = positive weeks.")
    print("Signal = 12:30 pays materially more than the other hours. ~1 month, thin.")

    print("\n-- 12:30 hold plateau @ slip=2bp (plateau = real; lone spike = mirage) --")
    print(f"{'hold':>5}{'trades':>7}{'avg_bps':>9}{'hit':>6}{'sum_bps':>9}{'wk+':>6}{'max_share':>10}")
    for hold in (30, 60, 90, 120, 150, 180):
        a = run_arm(df, hm("12:30"), hold, 2.0)
        if a["n"] == 0:
            continue
        print(f"{hold:>5}{a['n']:>7}{a['avg_bps']:>+9.2f}{a['hit']:>6.0%}{a['sum_bps']:>+9.1f}"
              f"{a['wk_pos']:>3}/{a['wk_tot']}{a['max_share']:>10.0%}")

    print("\n-- 12:30 hold=120 slip=2bp: per-trade distribution (1-event domination?) --")
    a = run_arm(df, hm("12:30"), 120, 2.0)
    order = np.argsort(a["rets"])[::-1]
    print("  wk/side/bps:  " + "  ".join(
        f"w{a['weeks'][i]}{'L' if a['sides'][i] > 0 else 'S'}{a['rets'][i]*1e4:+.0f}" for i in order))
    print(f"  top trade = {a['max_share']:.0%} of total; positive {(a['rets']>0).sum()}/{a['n']}")
    print("\nCost-stress: event-time fills are worse than spread_mean; want survival at 2-4bp.")


if __name__ == "__main__":
    main()
