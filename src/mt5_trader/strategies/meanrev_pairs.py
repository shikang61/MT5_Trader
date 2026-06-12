"""Mean reversion on a synthetic spread (e.g. Gold/Silver ratio, FX triangle).

Build the spread series with `spread_bars(bars_a, bars_b, beta)`, then run
RatioMeanRev on it through the normal backtest engine. A long spread position
means long A / short B scaled by beta; the live runner must place both legs.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from .base import register


def spread_bars(bars_a: pl.DataFrame, bars_b: pl.DataFrame, beta: float = 1.0) -> pl.DataFrame:
    """Synthetic close = exp(log A - beta * log B), so engine returns are
    ~ ret_A - beta * ret_B. spread_mean encodes the combined two-leg cost so
    the engine's spread_mean/close recovers the summed fractional spread."""
    a = bars_a.select("ts", "close", "spread_mean")
    b = bars_b.select("ts", "close", "spread_mean")
    df = a.join(b, on="ts", suffix="_b").sort("ts")
    df = df.with_columns(
        synth=(pl.col("close").log() - beta * pl.col("close_b").log()).exp(),
        cost_frac=(pl.col("spread_mean") / pl.col("close")
                   + abs(beta) * pl.col("spread_mean_b") / pl.col("close_b")),
    )
    return df.select(
        "ts",
        close=pl.col("synth"),
        spread_mean=pl.col("cost_frac") * pl.col("synth"),
    )


@register
@dataclass
class RatioMeanRev:
    z_n: int = 1440
    z_entry: float = 2.0
    z_exit: float = 0.5

    name = "ratio_mr"

    def signal(self, bars: pl.DataFrame) -> np.ndarray:
        x = pl.col("close").log()
        z = bars.select(
            ((x - x.rolling_mean(self.z_n)) / x.rolling_std(self.z_n)).alias("z")
        )["z"].to_numpy()

        pos = np.zeros(len(z))
        state = 0.0
        for t in range(len(z)):
            if np.isnan(z[t]):
                pos[t] = 0.0
                continue
            if state == 0.0:
                if z[t] >= self.z_entry:
                    state = -1.0
                elif z[t] <= -self.z_entry:
                    state = 1.0
            elif abs(z[t]) <= self.z_exit:
                state = 0.0
            pos[t] = state
        return pos
