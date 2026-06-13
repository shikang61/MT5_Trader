"""Prior-day high/low sweep fade ("liquidity sweep" reversal).

Research finding (scripts/research_sweep.py, May 12 - Jun 11 2026 XAUUSD):
price takes out the prior day's high or low, then closes back inside within
a few bars (stop-run snapback) → fading the sweep made +8 to +11.5bp/event
over the next 10-15 min, 5/5 positive weeks, n≈20, plateau across
quiet/confirm/horizon wiggle. Same event on EURUSD/GBPUSD/EURGBP fails
after costs — gold-specific, like every other edge in this book.

Defaults assume 1-minute bars. Sweep = first cross of the prior-day extreme
after quiet_bars on one side; classified confirm_bars later: close back
inside = enter the fade, hold hold_bars, then flat.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from .base import register


@register
@dataclass
class SweepFade:
    quiet_bars: int = 60
    confirm_bars: int = 3
    hold_bars: int = 10

    name = "sweep_fade"

    def signal(self, bars: pl.DataFrame) -> np.ndarray:
        daily = (bars.with_columns(date=pl.col("ts").dt.date())
                 .group_by("date")
                 .agg(pdh=pl.col("high").max(), pdl=pl.col("low").min())
                 .sort("date")
                 .with_columns(pl.col("pdh", "pdl").shift(1)))
        df = bars.with_columns(date=pl.col("ts").dt.date()).join(
            daily, on="date", how="left")
        high, low = df["high"].to_numpy(), df["low"].to_numpy()
        close = df["close"].to_numpy()
        pdh, pdl = df["pdh"].to_numpy(), df["pdl"].to_numpy()
        dates = df["date"].to_numpy()

        pos = np.zeros(len(close))
        day = None
        big = 10**9
        last_hi = last_lo = -big          # last bar beyond the level
        pend_hi = pend_lo = -1            # bar at which to classify the sweep
        short_left = long_left = 0
        for t in range(len(close)):
            if dates[t] != day:
                day = dates[t]
                last_hi = last_lo = -big
                pend_hi = pend_lo = -1
            if not np.isnan(pdh[t]):
                if high[t] > pdh[t]:
                    if t - last_hi > self.quiet_bars and pend_hi < 0:
                        pend_hi = t + self.confirm_bars
                    last_hi = t
                if low[t] < pdl[t]:
                    if t - last_lo > self.quiet_bars and pend_lo < 0:
                        pend_lo = t + self.confirm_bars
                    last_lo = t
                if t == pend_hi:
                    if close[t] < pdh[t]:
                        short_left = self.hold_bars
                    pend_hi = -1
                if t == pend_lo:
                    if close[t] > pdl[t]:
                        long_left = self.hold_bars
                    pend_lo = -1
            if short_left > 0:
                pos[t] -= 1.0
                short_left -= 1
            if long_left > 0:
                pos[t] += 1.0
                long_left -= 1
        return pos
