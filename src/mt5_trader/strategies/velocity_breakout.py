"""US-session opening-range breakout, gated by break velocity (Kaufman ER).

Arm at the US data-drop times (12:30, 14:00 UTC). For each arm, record the
pre-range (closes in [arm - pre_min, arm)); take the FIRST close beyond it within
[arm, arm + arm_min) only if the break is clean — efficiency ratio over er_n bars
>= er_min (a churn break fades, study A/diagnostic 2026-06-18). Hold hold_bars,
then flat. push_min (break-bar |ret|/ATR) is an optional secondary gate, off by
default. Backtest-only; not in portfolio.yaml. See the design doc for evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import polars as pl

from .base import register
from ..features.regime import efficiency_ratio
from ..features.technicals import atr


def _to_minutes(hhmm: str) -> int:
    """'12:30' -> 750 (minute of the UTC day)."""
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


@register
@dataclass
class VelocityBreakout:
    arm_times: list[str] = field(default_factory=lambda: ["12:30", "14:00"])
    pre_min: int = 30
    arm_min: int = 30
    er_n: int = 10
    er_min: float = 0.5
    push_min: float = 0.0
    hold_bars: int = 120

    name = "velocity_breakout"

    def signal(self, bars: pl.DataFrame) -> np.ndarray:
        n = len(bars)
        close = bars["close"].to_numpy()
        minute_of_day = (bars["ts"].dt.hour().cast(pl.Int32) * 60
                         + bars["ts"].dt.minute()).to_numpy()
        dates = bars["ts"].dt.date().to_numpy()
        er = bars.select(er=efficiency_ratio(self.er_n))["er"].to_numpy()
        push = bars.select(p=(pl.col("close").diff().abs() / atr(14)))["p"].to_numpy()
        arms = [_to_minutes(a) for a in self.arm_times]

        # per-arm state, reset each UTC day
        pre_hi = {a: None for a in arms}
        pre_lo = {a: None for a in arms}
        entered = {a: False for a in arms}
        apos = {a: 0.0 for a in arms}     # this arm's current held position
        hold = {a: 0 for a in arms}       # bars remaining to hold

        pos = np.zeros(n)
        day = None
        for t in range(n):
            if dates[t] != day:
                day = dates[t]
                for a in arms:
                    pre_hi[a] = pre_lo[a] = None
                    entered[a] = False
                    apos[a] = 0.0
                    hold[a] = 0
            mt = minute_of_day[t]
            total = 0.0
            for a in arms:
                if hold[a] > 0:                      # age the open position
                    hold[a] -= 1
                    if hold[a] == 0:
                        apos[a] = 0.0
                if a - self.pre_min <= mt < a:       # accumulate the pre-range
                    c = close[t]
                    pre_hi[a] = c if pre_hi[a] is None else max(pre_hi[a], c)
                    pre_lo[a] = c if pre_lo[a] is None else min(pre_lo[a], c)
                elif (a <= mt < a + self.arm_min and not entered[a]
                        and pre_hi[a] is not None and apos[a] == 0.0):
                    c = close[t]
                    direction = 1.0 if c > pre_hi[a] else -1.0 if c < pre_lo[a] else 0.0
                    if direction != 0.0:             # first break consumes the arm
                        entered[a] = True
                        clean = (not np.isnan(er[t])) and er[t] >= self.er_min
                        fast = self.push_min <= 0.0 or (
                            not np.isnan(push[t]) and push[t] >= self.push_min)
                        if clean and fast:
                            apos[a] = direction
                            hold[a] = self.hold_bars
                total += apos[a]
            pos[t] = max(-1.0, min(1.0, total))      # net the arms, clip to [-1, 1]
        return pos
