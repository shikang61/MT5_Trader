"""Fast mean reversion: fade short-term deviations from a rolling mean.

The idea the operator asked for — catch short-term dips and rises. Price drops
far below its rolling mean (z <= -z_entry) -> go long (expect a bounce); spikes
above (z >= +z_entry) -> go short (expect a pullback); flatten near the mean
(|z| <= z_exit). State machine so it holds the fade until the mean is recovered,
not flip every bar.

Defaults assume M1 bars (z_n=60 = 1h). This is a TAKER fade — the same family
that lost ~3-6%/month after costs in the regime_switch research (see its
fade_size note). Registered for backtesting, NOT in portfolio.yaml: run it
through run_backtest.py at --slippage-bps 0 vs 2 to see how fast costs eat it.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from .base import register


@register
@dataclass
class FastMeanRev:
    z_n: int = 60          # rolling window (60 M1 bars = 1h)
    z_entry: float = 2.0   # |z| to open a fade
    z_exit: float = 0.5    # |z| to close back toward the mean

    name = "fast_mr"

    def signal(self, bars: pl.DataFrame) -> np.ndarray:
        c = pl.col("close")
        z = bars.select(
            ((c - c.rolling_mean(self.z_n)) / c.rolling_std(self.z_n)).alias("z")
        )["z"].to_numpy()

        pos = np.zeros(len(z))
        state = 0.0
        for t in range(len(z)):
            if np.isnan(z[t]):
                pos[t] = 0.0
                continue
            if state == 0.0:
                if z[t] >= self.z_entry:
                    state = -1.0          # stretched high -> fade short
                elif z[t] <= -self.z_entry:
                    state = 1.0           # stretched low -> fade long
            elif abs(z[t]) <= self.z_exit:
                state = 0.0               # back near mean -> flat
            pos[t] = state
        return pos
