"""Order-flow imbalance momentum.

Needs bars built with an `ofi` column (per-bar sum of tick-level OFI from
features.microstructure). Enters in the direction of an OFI z-score spike
and holds for `hold` bars, re-arming on fresh spikes.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from .base import register


@register
@dataclass
class OFIMomentum:
    z_n: int = 240
    z_entry: float = 2.0
    hold: int = 30

    name = "ofi"

    def signal(self, bars: pl.DataFrame) -> np.ndarray:
        if "ofi" not in bars.columns:
            raise ValueError("bars need an 'ofi' column — aggregate tick OFI when building bars")
        x = pl.col("ofi")
        z = bars.select(
            ((x - x.rolling_mean(self.z_n)) / x.rolling_std(self.z_n)).alias("z")
        )["z"].to_numpy()

        pos = np.zeros(len(z))
        state, remaining = 0.0, 0
        for t in range(len(z)):
            if not np.isnan(z[t]) and abs(z[t]) >= self.z_entry:
                state, remaining = float(np.sign(z[t])), self.hold
            elif remaining > 0:
                remaining -= 1
                if remaining == 0:
                    state = 0.0
            pos[t] = state
        return pos
