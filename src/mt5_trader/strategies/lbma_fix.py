"""LBMA Gold Fix AM-window reversal.

Research finding (scripts/research_lbma_fix.py, May 11 - Jun 19 2026 XAUUSD M5):
Gold drifts ~11 bp lower in the 60 min before the London AM Fix (10:30 BST /
09:30 UTC summer), then REVERSES sharply in the 30-120 min after the benchmark
is set — consistent with institutional pre-fix positioning being unwound.

Signal: at the AM Fix bar, read the direction of the prior `lookback` bars,
enter the OPPOSITE direction, hold `hold` bars, then flat.

Best config on the research window: lookback=12 (60 min), hold=24 (120 min).
  Sharpe 5.82 @ 1x cost (0.5 bp), 4.25 @ 4x cost. Max DD -1.13%.
  27 AM Fix events over 40 days — small sample; start at a low portfolio weight.

Defaults assume 5-minute bars and summer (BST) UTC offset.
For winter (GMT), set am_hour=10, am_min=30.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from .base import register


@register
@dataclass
class LbmaFixReversal:
    am_hour: int = 9     # UTC hour of AM Fix  (09:30 UTC = 10:30 BST summer)
    am_min: int = 30     # UTC minute of AM Fix
    lookback: int = 12   # bars to look back for pre-fix direction (12 × 5m = 60 min)
    hold: int = 24       # bars to hold after fix (24 × 5m = 120 min)

    name = "lbma_fix"

    def signal(self, bars: pl.DataFrame) -> np.ndarray:
        close = bars["close"].to_numpy()
        hour  = bars["ts"].dt.hour().to_numpy()
        minute = bars["ts"].dt.minute().to_numpy()
        n = len(close)

        pos = np.zeros(n)
        for t in range(self.lookback, n):
            if hour[t] != self.am_hour or minute[t] != self.am_min:
                continue
            # Pre-fix return over last `lookback` bars (causal: uses close[t])
            ret = np.log(close[t] / close[t - self.lookback])
            direction = -float(np.sign(ret))   # REVERSE the pre-fix drift
            if direction == 0.0:
                continue
            # Enter the bar after the fix, hold for `hold` bars
            end = min(t + 1 + self.hold, n)
            pos[t + 1 : end] = direction
        return pos
