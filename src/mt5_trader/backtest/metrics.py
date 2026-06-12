"""Performance metrics on per-bar fractional returns."""
from __future__ import annotations

import math

import numpy as np


def sharpe(returns: np.ndarray, periods_per_year: float) -> float:
    sd = returns.std()
    if sd == 0:
        return 0.0
    return float(returns.mean() / sd * math.sqrt(periods_per_year))


def sortino(returns: np.ndarray, periods_per_year: float) -> float:
    downside = returns[returns < 0]
    if len(downside) == 0 or downside.std() == 0:
        return float("inf") if returns.mean() > 0 else 0.0
    return float(returns.mean() / downside.std() * math.sqrt(periods_per_year))


def max_drawdown(equity: np.ndarray) -> float:
    """Most negative peak-to-trough fraction (e.g. -0.12 = 12% drawdown)."""
    peak = np.maximum.accumulate(equity)
    return float((equity / peak - 1).min())


def summary(returns: np.ndarray, equity: np.ndarray,
            periods_per_year: float, turnover: np.ndarray | None = None) -> dict:
    active = returns != 0
    out = {
        "total_return": float(equity[-1] / equity[0] - 1),
        "sharpe": sharpe(returns, periods_per_year),
        "sortino": sortino(returns, periods_per_year),
        "max_drawdown": max_drawdown(equity),
        "hit_rate": float((returns[active] > 0).mean()) if active.any() else 0.0,
        "active_bars": int(active.sum()),
        "n_bars": len(returns),
    }
    if turnover is not None:
        out["total_turnover"] = float(turnover.sum())
    return out
