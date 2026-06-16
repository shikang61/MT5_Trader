"""Mean reversion on a synthetic spread (e.g. Gold/Silver ratio, FX triangle).

Build the spread series with `spread_bars(bars_a, bars_b, beta)`, then run
RatioMeanRev on it through the normal backtest engine. A long spread position
means long A / short B scaled by beta; the live runner must place both legs.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from ..features.regime import RANGE, regime_series
from .base import register


def spread_bars(bars_a: pl.DataFrame, bars_b: pl.DataFrame, beta: float = 1.0,
                beta_n: int | None = None) -> pl.DataFrame:
    """Synthetic close = exp(log A - beta * log B), so engine returns are
    ~ ret_A - beta * ret_B. spread_mean encodes the combined two-leg cost so
    the engine's spread_mean/close recovers the summed fractional spread.

    beta_n: if set, beta is a rolling OLS hedge ratio cov(logA,logB)/var(logB)
    over the last beta_n bars instead of the constant `beta`, so a drifting
    relationship can't masquerade as a tradable spread trend. Warmup bars (and
    any degenerate window) fall back to the constant `beta`. Use a window well
    longer than z_n so beta drifts slowly (a fast beta injects spurious synth
    returns into the engine's mark-to-market)."""
    a = bars_a.select("ts", "close", "spread_mean")
    b = bars_b.select("ts", "close", "spread_mean")
    df = a.join(b, on="ts", suffix="_b").sort("ts")
    la, lb = pl.col("close").log(), pl.col("close_b").log()
    if beta_n:
        mean_a, mean_b = la.rolling_mean(beta_n), lb.rolling_mean(beta_n)
        cov = (la * lb).rolling_mean(beta_n) - mean_a * mean_b
        var = (lb * lb).rolling_mean(beta_n) - mean_b * mean_b
        beta_col = (cov / var).fill_nan(beta).fill_null(beta)
    else:
        beta_col = pl.lit(float(beta))
    df = df.with_columns(
        synth=(la - beta_col * lb).exp(),
        cost_frac=(pl.col("spread_mean") / pl.col("close")
                   + beta_col.abs() * pl.col("spread_mean_b") / pl.col("close_b")),
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
    z_stop: float = 0.0          # 0 = off. >0: bail when |z| reaches it (the
                                 # spread is running away, not reverting) and
                                 # stay flat until |z| falls back inside z_exit,
                                 # so we don't immediately re-enter the runaway.
    regime_filter: bool = True   # only fade the spread in a RANGE regime

    name = "ratio_mr"

    def signal(self, bars: pl.DataFrame) -> np.ndarray:
        x = pl.col("close").log()
        z = bars.select(
            ((x - x.rolling_mean(self.z_n)) / x.rolling_std(self.z_n)).alias("z")
        )["z"].to_numpy()

        pos = np.zeros(len(z))
        state = 0.0
        locked = False               # stopped out; wait for z to reset
        for t in range(len(z)):
            zt = z[t]
            if np.isnan(zt):
                pos[t] = 0.0
                continue
            if state == 0.0:
                if locked:
                    if abs(zt) <= self.z_exit:
                        locked = False   # spread came home; re-arm next bar
                elif zt >= self.z_entry:
                    state = -1.0
                elif zt <= -self.z_entry:
                    state = 1.0
            elif self.z_stop and abs(zt) >= self.z_stop:
                state, locked = 0.0, True
            elif abs(zt) <= self.z_exit:
                state = 0.0
            pos[t] = state
        # Pairs blow up when the spread trends; fade only when it ranges.
        if self.regime_filter:
            pos = np.where(regime_series(bars) == RANGE, pos, 0.0)
        return pos
