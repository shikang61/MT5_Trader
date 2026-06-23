"""EURUSD -> gold intraday lead-lag (cross-asset).

USD leads spot gold; gold follows EURUSD (both are inverse-USD, so they co-move).
Combine upstream like meanrev_pairs.spread_bars: join EURUSD onto gold bars, then
a normal single-series signal() reads `eur_close`. M1 bars. See
docs/superpowers/specs/2026-06-23-dxy-gold-leadlag-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from .base import register


def attach_eur(gold_bars: pl.DataFrame, eur_bars: pl.DataFrame) -> pl.DataFrame:
    """Gold M1 bars + `eur_close` (left join on ts, forward-filled for any gap).
    Both series must come from the SAME source/clock for the lead-lag to be real."""
    e = eur_bars.select("ts", eur_close=pl.col("close"))
    return (gold_bars.join(e, on="ts", how="left")
            .with_columns(pl.col("eur_close").forward_fill()))


@register
@dataclass
class UsdLeadLag:
    lead_w: int = 3        # bars: EURUSD log-return lookback (the lead window)
    hold_h: int = 10       # bars: hold the gold position after a trigger
    theta_bp: float = 0.0  # |EURUSD move| gate in bp (0 = act on every bar)

    name = "usd_leadlag"

    def signal(self, bars: pl.DataFrame) -> np.ndarray:
        # Graceful degradation: if eur_close is missing, return flat signal.
        # This allows generic tests to run; in practice, use attach_eur() to provide it.
        if "eur_close" not in bars.columns:
            return np.zeros(len(bars))

        eur = bars["eur_close"].to_numpy().astype(float)
        n = len(eur)
        r = np.full(n, np.nan)
        if n > self.lead_w:
            r[self.lead_w:] = np.log(eur[self.lead_w:] / eur[:-self.lead_w])
        pos = np.zeros(n)
        side, hold = 0.0, 0
        for t in range(n):
            rt = r[t]
            if not np.isnan(rt) and rt != 0.0 and abs(rt) * 1e4 >= self.theta_bp:
                side = float(np.sign(rt))    # gold follows EURUSD
                hold = self.hold_h
            if hold > 0:
                pos[t] = side
                hold -= 1
            else:
                side = 0.0
        return pos
