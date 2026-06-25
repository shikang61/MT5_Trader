"""Donchian channel breakout with EMA trend filter.

Long when close breaks the prior entry_n-bar high and is above the trend EMA;
exit when close falls below the prior exit_n-bar low. Short is symmetric.
Defaults assume 1-minute bars (240 = 4h, 1440 = 1 day).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from .base import register


@register
@dataclass
class DonchianBreakout:
    entry_n: int = 240
    exit_n: int = 60
    ema_n: int = 1440

    name = "donchian"

    def levels(self, bars: pl.DataFrame) -> dict[str, np.ndarray]:
        df = bars.with_columns(
            entry_hi=pl.col("close").rolling_max(self.entry_n).shift(1),
            entry_lo=pl.col("close").rolling_min(self.entry_n).shift(1),
            exit_hi=pl.col("close").rolling_max(self.exit_n).shift(1),
            exit_lo=pl.col("close").rolling_min(self.exit_n).shift(1),
            ema=pl.col("close").ewm_mean(span=self.ema_n),
        )
        return {k: df[k].to_numpy() for k in
                ("entry_hi", "entry_lo", "exit_hi", "exit_lo", "ema")}

    def signal(self, bars: pl.DataFrame) -> np.ndarray:
        lv = self.levels(bars)
        close = bars["close"].to_numpy()
        entry_hi, entry_lo = lv["entry_hi"], lv["entry_lo"]
        exit_hi, exit_lo = lv["exit_hi"], lv["exit_lo"]
        ema = lv["ema"]

        pos = np.zeros(len(close))
        state = 0.0
        for t in range(len(close)):
            if np.isnan(entry_hi[t]):
                pos[t] = 0.0
                continue
            if state == 0.0:
                if close[t] > entry_hi[t] and close[t] > ema[t]:
                    state = 1.0
                elif close[t] < entry_lo[t] and close[t] < ema[t]:
                    state = -1.0
            elif state == 1.0 and close[t] < exit_lo[t]:
                state = 0.0
            elif state == -1.0 and close[t] > exit_hi[t]:
                state = 0.0
            pos[t] = state
        return pos
