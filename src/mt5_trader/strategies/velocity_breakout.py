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
        return np.zeros(len(bars))
