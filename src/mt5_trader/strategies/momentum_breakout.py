"""Donchian channel breakout with EMA trend filter.

Long when close breaks the prior entry_n-bar high and is above the trend EMA;
exit when close falls below the prior exit_n-bar low. Short is symmetric.
Defaults assume 1-minute bars (240 = 4h, 1440 = 1 day).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from .base import register
from ..backtest.retest import simulate_retest_entry


@register
@dataclass
class DonchianBreakout:
    entry_n: int = 240
    exit_n: int = 110   # grid search 2026-06-24: 110 best IS (Sh 2.10) and tied OOS (Sh 12.19); plateau 100-120
    ema_n: int = 1440

    name = "donchian"

    def levels(self, bars: pl.DataFrame) -> dict[str, np.ndarray]:
        df = bars.with_columns(
            entry_hi=pl.col("close").rolling_max(self.entry_n).shift(1),
            entry_lo=pl.col("close").rolling_min(self.entry_n).shift(1),
            exit_hi=pl.col("close").rolling_max(self.exit_n).shift(1),
            exit_lo=pl.col("close").rolling_min(self.exit_n).shift(1),
            ema=pl.col("close").ewm_mean(span=self.ema_n),
        )
        return {k: df[k].to_numpy() for k in
                ("entry_hi", "entry_lo", "exit_hi", "exit_lo", "ema")}

    def signal(self, bars: pl.DataFrame) -> np.ndarray:
        lv = self.levels(bars)
        close = bars["close"].to_numpy()
        entry_hi, entry_lo = lv["entry_hi"], lv["entry_lo"]
        exit_hi, exit_lo = lv["exit_hi"], lv["exit_lo"]
        ema = lv["ema"]

        pos = np.zeros(len(close))
        state = 0.0
        for t in range(len(close)):
            if np.isnan(entry_hi[t]):
                pos[t] = 0.0
                continue
            if state == 0.0:
                if close[t] > entry_hi[t] and close[t] > ema[t]:
                    state = 1.0
                elif close[t] < entry_lo[t] and close[t] < ema[t]:
                    state = -1.0
            elif state == 1.0 and close[t] < exit_lo[t]:
                state = 0.0
            elif state == -1.0 and close[t] > exit_hi[t]:
                state = 0.0
            pos[t] = state
        return pos


@register
@dataclass
class DonchianRetest:
    """Donchian breakout that waits for a pullback (retest) to the broken channel
    edge before entering, instead of buying the breakout at market. See
    backtest/retest.py. signal() returns the delayed-entry position series; the
    precise entry price (L) is measured by scripts/research_donchian_retest.py."""
    entry_n: int = 240
    exit_n: int = 60
    ema_n: int = 1440
    max_wait: int = 24      # M5 bars to wait for the retest (~2h)
    fallback: bool = False  # True = market entry if no retest by max_wait; False = skip

    name = "donchian_retest"

    def signal(self, bars: pl.DataFrame) -> np.ndarray:
        base = DonchianBreakout(self.entry_n, self.exit_n, self.ema_n)
        raw = base.signal(bars)
        lv = base.levels(bars)
        fill = simulate_retest_entry(
            raw, lv["entry_hi"], lv["entry_lo"],
            bars["high"].to_numpy(), bars["low"].to_numpy(),
            bars["close"].to_numpy(), self.max_wait, self.fallback)
        return fill.pos
