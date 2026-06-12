"""Regime-conditional strategy for Gold.

Trend regime (efficiency ratio high): ride the direction at full size.
Range regime: fade the extremes — short near the ceiling, long near the
floor, exit at the range midpoint. Bar-level approximation of range
scalping; the live passive (limit-order) variant is research-gated, see
STRATEGY_PLAYBOOK.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from ..features.regime import RANGE, regime_series
from .base import register


@register
@dataclass
class RegimeSwitch:
    er_n: int = 16           # regime window: 16 coarse points (~4h)
    enter_trend: float = 0.40
    exit_trend: float = 0.30
    coarsen: int = 15        # compute ER on every 15th bar (15m on M1 feeds)
    range_n: int = 240       # window defining ceiling/floor
    fade_entry: float = 0.8  # |z| from mid (in half-range units) to enter a fade
    fade_stop: float = 1.3   # |z| beyond which the range is broken -> cut the fade
    fade_size: float = 0.0   # range fades OFF by default: taker-style fading lost
                             # ~3-6%/month after costs on May-Jun 2026 XAUUSD ticks
                             # (both with and without stops). Revisit as a passive
                             # limit-order strategy when L2 dump data allows a
                             # realistic fill model. Trend leg was ~breakeven+.
    trend_size: float = 1.0  # size when riding a trend

    name = "regime_switch"

    def signal(self, bars: pl.DataFrame) -> np.ndarray:
        regime = regime_series(bars, self.er_n, self.enter_trend, self.exit_trend,
                               self.coarsen)
        df = bars.with_columns(
            hi=pl.col("close").rolling_max(self.range_n).shift(1),
            lo=pl.col("close").rolling_min(self.range_n).shift(1),
        )
        close = df["close"].to_numpy()
        hi, lo = df["hi"].to_numpy(), df["lo"].to_numpy()

        pos = np.zeros(len(close))
        fade = 0.0
        for t in range(len(close)):
            if regime[t] != RANGE:
                fade = 0.0
                pos[t] = regime[t] * self.trend_size
                continue
            if np.isnan(hi[t]) or hi[t] <= lo[t]:
                pos[t] = 0.0
                continue
            mid = (hi[t] + lo[t]) / 2
            z = (close[t] - mid) / ((hi[t] - lo[t]) / 2)
            if fade == 0.0:
                if self.fade_entry <= z < self.fade_stop:
                    fade = -self.fade_size       # at the ceiling -> short
                elif -self.fade_stop < z <= -self.fade_entry:
                    fade = self.fade_size        # at the floor -> long
            elif (fade < 0 and z <= 0) or (fade > 0 and z >= 0):
                fade = 0.0                       # back at mid -> take profit
            elif abs(z) >= self.fade_stop:
                fade = 0.0                       # range broke -> stop out
            pos[t] = fade
        return pos
