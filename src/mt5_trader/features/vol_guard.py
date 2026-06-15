"""Fast volatility circuit breaker: mute trading during violent price spikes.

The inverse-vol sizer (risk.manager.size) already shrinks position as *realized*
vol rises — but realized_vol is a rolling std over a long window (240 bars), so
it reacts in hours, not minutes. Gold's sharp two-way spikes (news, stop-runs,
liquidity vacuums) blow through that lag: one bar spikes, fills at a blown-out
spread, then snaps back. This overlay is the *fast* gate — a per-bar boolean
"is this an abnormal-volatility bar", tripped by either a 1-bar return jump or a
short-ATR expansion relative to its own recent baseline.

Same shape as the calendar blackout (features/calendar.py): one mask primitive
feeds both the backtest (mask the signal array) and live
(`RiskManager.vol_halt(bars)`), so the two never drift. No lookahead — every
input (true range, close-to-close return, rolling baseline) uses only data up to
and including the bar being marked.

Tripped bars are forced flat. Like the trailing stop (left off because it churns
on normal pullbacks), this can flatten on a spike and re-enter after — so gate it
through scripts/eval_vol_guard.py before flipping `enabled: true`.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from .technicals import atr


@dataclass
class VolGuardCfg:
    """Fast vol circuit breaker. `enabled=False` => no-op (all-False mask).
    Bar-count windows; defaults assume M5 (the deployed timeframe)."""
    enabled: bool = False
    atr_n: int = 14              # short ATR = the "now" volatility
    atr_base_n: int = 288        # baseline window for the ATR median (~1 day on M5)
    atr_mult: float = 3.0        # trip if short ATR > atr_mult x its baseline median
    ret_limit_bps: float = 50.0  # trip if |1-bar log return| exceeds this (50bp = 0.5%)


def vol_spike_mask(bars: pl.DataFrame, cfg: VolGuardCfg) -> np.ndarray:
    """Boolean array over `bars`: True where the bar is an abnormal-vol spike.
    The ATR gate needs high/low; on close-only frames (e.g. synthetic spreads)
    it is skipped and only the return gate applies. Disabled cfg -> all False."""
    n = len(bars)
    if not cfg.enabled or n == 0:
        return np.zeros(n, dtype=bool)

    ret1 = bars.select(pl.col("close").log().diff().abs().alias("r"))["r"].to_numpy()
    mask = np.nan_to_num(ret1) > cfg.ret_limit_bps * 1e-4

    if {"high", "low"}.issubset(bars.columns):
        a = bars.select(atr(cfg.atr_n).alias("a"))["a"]
        ratio = (a / a.rolling_median(cfg.atr_base_n)).to_numpy()
        mask = mask | (np.nan_to_num(ratio) > cfg.atr_mult)
    return mask
