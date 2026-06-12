"""Vectorized bar-level backtester.

Conventions (no lookahead by construction):
  - signal[t] is the target position in [-1, 1], decided on bar t's CLOSE
    using only data up to and including bar t.
  - The position is held during bar t+1 (i.e. filled at ~bar t close /
    bar t+1 open) and earns bar t+1's close-to-close return.
  - Costs: each unit of turnover pays half the bar's mean spread plus
    slippage_bps. Calibrate slippage_bps against paper-trading fills.

Limitations: no intrabar stops (model stops as signal logic on bar closes),
single instrument per run (pairs are backtested as a synthetic spread series).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import polars as pl

from .metrics import summary


@dataclass
class BTConfig:
    leverage: float = 1.0
    slippage_bps: float = 0.5
    init_equity: float = 1_000_000.0
    bars_per_year: float = 365 * 24 * 60  # 1-minute bars, ~24h markets


@dataclass
class BTResult:
    bars: pl.DataFrame
    metrics: dict = field(default_factory=dict)


def run(bars: pl.DataFrame, signal: np.ndarray, cfg: BTConfig | None = None) -> BTResult:
    cfg = cfg or BTConfig()
    n = len(bars)
    if len(signal) != n:
        raise ValueError(f"signal length {len(signal)} != bars length {n}")

    close = bars["close"].to_numpy().astype(float)
    if "spread_mean" in bars.columns:
        spread_frac = (bars["spread_mean"].fill_null(0).to_numpy().astype(float)
                       / np.where(close == 0, 1.0, close))
    else:
        spread_frac = np.zeros(n)

    sig = np.clip(np.nan_to_num(signal.astype(float)), -1.0, 1.0)
    pos = np.empty(n)
    pos[0] = 0.0
    pos[1:] = sig[:-1]  # executed one bar after the signal

    ret = np.zeros(n)
    ret[1:] = close[1:] / close[:-1] - 1.0

    turnover = np.abs(np.diff(pos, prepend=0.0))
    cost = turnover * (spread_frac / 2 + cfg.slippage_bps * 1e-4)

    pnl = cfg.leverage * (pos * ret - cost)
    equity = cfg.init_equity * np.cumprod(1.0 + pnl)

    out = bars.with_columns(
        pos=pl.Series(pos),
        bar_ret=pl.Series(ret),
        pnl=pl.Series(pnl),
        equity=pl.Series(equity),
    )
    return BTResult(bars=out, metrics=summary(pnl, equity, cfg.bars_per_year, turnover))
