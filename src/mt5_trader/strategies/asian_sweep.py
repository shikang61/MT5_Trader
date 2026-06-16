"""Asian-session high/low sweep fade.

Research (scripts/research_sweep.py + null test, May 11-Jun 10 2026 XAUUSD):
fading the reclaim of the Asian-session (00:00-06:00 UTC) range during the
London session made +10.7bp/event at a 60-min hold (q60,c5), a plateau across
quiet/confirm; null test p=0.005 (sweep-specific, not session drift). Thin
(n~25, one month, edge only at 60m) -> backtest-only, NOT in portfolio.yaml.

Structurally SweepFade with a different level source: today's Asian range
instead of the prior day's high/low, and a longer (60-bar) hold. M1 bars.
Defaults assume 1-minute bars.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from .base import register


@register
@dataclass
class AsianSweepFade:
    session_end_hour: int = 6   # Asian window is [0, session_end_hour) UTC
    quiet_bars: int = 60
    confirm_bars: int = 5
    hold_bars: int = 60

    name = "asian_sweep"

    def signal(self, bars: pl.DataFrame) -> np.ndarray:
        df = bars.with_columns(date=pl.col("ts").dt.date(),
                               hour=pl.col("ts").dt.hour())
        # Asian-session H/L per day from the [0, session_end_hour) window only.
        # Those bars always precede the day's trade bars (hour >= end), so this
        # is causal: a trade bar never depends on a future bar.
        asia = (df.filter(pl.col("hour") < self.session_end_hour)
                .group_by("date")
                .agg(ah=pl.col("high").max(), al=pl.col("low").min()))
        df = df.join(asia, on="date", how="left")
        high, low, close = (df["high"].to_numpy(), df["low"].to_numpy(),
                            df["close"].to_numpy())
        ah, al = df["ah"].to_numpy(), df["al"].to_numpy()
        hour = df["hour"].to_numpy()
        dates = df["date"].to_numpy()

        pos = np.zeros(len(close))
        day = None
        big = 10**9
        last_hi = last_lo = -big        # last bar beyond the level
        pend_hi = pend_lo = -1          # bar at which to classify the sweep
        short_left = long_left = 0
        for t in range(len(close)):
            if dates[t] != day:
                day = dates[t]
                last_hi = last_lo = -big
                pend_hi = pend_lo = -1
            active = hour[t] >= self.session_end_hour and not np.isnan(ah[t])
            if active:
                if high[t] > ah[t]:
                    if t - last_hi > self.quiet_bars and pend_hi < 0:
                        pend_hi = t + self.confirm_bars
                    last_hi = t
                if low[t] < al[t]:
                    if t - last_lo > self.quiet_bars and pend_lo < 0:
                        pend_lo = t + self.confirm_bars
                    last_lo = t
                if t == pend_hi:
                    if close[t] < ah[t]:
                        short_left = self.hold_bars
                    pend_hi = -1
                if t == pend_lo:
                    if close[t] > al[t]:
                        long_left = self.hold_bars
                    pend_lo = -1
            if short_left > 0:
                pos[t] -= 1.0
                short_left -= 1
            if long_left > 0:
                pos[t] += 1.0
                long_left -= 1
        return pos
