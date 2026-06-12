"""London open range breakout.

Record the high/low of the opening range (default 06:00-07:00 UTC), then take
the first breakout in either direction and hold until flat_hour. One trade per
day per direction; flat outside the trading window. Hours are UTC (BST-1).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from .base import register


@register
@dataclass
class LondonBreakout:
    range_start: int = 6
    range_end: int = 7
    flat_hour: int = 16

    name = "london_orb"

    def signal(self, bars: pl.DataFrame) -> np.ndarray:
        close = bars["close"].to_numpy()
        high = bars["high"].to_numpy()
        low = bars["low"].to_numpy()
        hours = bars["ts"].dt.hour().to_numpy()
        dates = bars["ts"].dt.date().to_numpy()

        pos = np.zeros(len(close))
        day = None
        rng_hi = rng_lo = None
        state = 0.0
        for t in range(len(close)):
            if dates[t] != day:
                day, rng_hi, rng_lo, state = dates[t], None, None, 0.0
            h = hours[t]
            if self.range_start <= h < self.range_end:
                rng_hi = high[t] if rng_hi is None else max(rng_hi, high[t])
                rng_lo = low[t] if rng_lo is None else min(rng_lo, low[t])
            elif self.range_end <= h < self.flat_hour and rng_hi is not None:
                if state == 0.0:
                    if close[t] > rng_hi:
                        state = 1.0
                    elif close[t] < rng_lo:
                        state = -1.0
            else:
                state = 0.0
            pos[t] = state
        return pos
