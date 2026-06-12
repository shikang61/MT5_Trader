"""Strategy interface: bars in, target position series out.

A strategy is any object with `.signal(bars) -> np.ndarray` where the output
is the target position in [-1, 1] decided on each bar's close. The backtest
engine (and live runner) shift execution by one bar, so signals must only use
data up to the current bar — never `.shift(-k)`.
"""
from __future__ import annotations

from typing import Protocol

import numpy as np
import polars as pl

REGISTRY: dict[str, type] = {}


class Strategy(Protocol):
    name: str

    def signal(self, bars: pl.DataFrame) -> np.ndarray: ...


def register(cls):
    REGISTRY[cls.name] = cls
    return cls
