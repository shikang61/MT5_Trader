"""Session-VWAP stretch continuation ("volume trend").

Research finding (May 12 - Jun 11 2026 XAUUSD ticks): price 2 sigma beyond
the session VWAP *continues* rather than reverts — fading it lost 3-5bp,
following it made +3.5 to +5.6bp/trade after costs across 70 deduped
episodes, positive 4 of 5 weeks. Enter with the stretch, exit when the
stretch normalizes or the session ends. Needs bars with n_ticks (VWAP
weights) — standard time_bars output.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from .base import register


@register
@dataclass
class VwapTrend:
    z_entry: float = 2.0
    z_exit: float = 0.5
    z_n: int = 240          # rolling window for stretch normalization
    warmup_min: int = 120   # skip the first 2h of each session (VWAP unstable)

    name = "vwap_trend"

    def signal(self, bars: pl.DataFrame) -> np.ndarray:
        # research bars carry n_ticks, live MT5 bars carry tick_volume
        w = ("n_ticks" if "n_ticks" in bars.columns
             else "tick_volume" if "tick_volume" in bars.columns else None)
        weight = pl.col(w) if w else pl.lit(1.0)
        df = bars.with_columns(
            date=pl.col("ts").dt.date(),
            minute=pl.col("ts").dt.hour() * 60 + pl.col("ts").dt.minute(),
        ).with_columns(
            vwap=(pl.col("close") * weight).cum_sum().over("date")
                 / weight.cum_sum().over("date"),
        ).with_columns(
            dist=(pl.col("close").log() - pl.col("vwap").log()),
        ).with_columns(
            z=pl.col("dist") / pl.col("dist").rolling_std(self.z_n),
        )

        z = df["z"].to_numpy()
        minute = df["minute"].to_numpy()
        dates = df["date"].to_numpy()

        pos = np.zeros(len(z))
        state = 0.0
        day = None
        for t in range(len(z)):
            if dates[t] != day:
                day, state = dates[t], 0.0  # session reset
            if np.isnan(z[t]):
                pos[t] = state
                continue
            if state == 0.0:
                if minute[t] > self.warmup_min and abs(z[t]) >= self.z_entry:
                    state = float(np.sign(z[t]))
            elif abs(z[t]) <= self.z_exit:
                state = 0.0
            pos[t] = state
        return pos
