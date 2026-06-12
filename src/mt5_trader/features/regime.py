"""Regime detection: trend up / trend down / range.

Kaufman efficiency ratio ER = |net move| / sum(|bar moves|) over a window.
Trending markets cover distance efficiently (ER near 1); ranges travel a lot
of path but end up nowhere (ER near 0). Hysteresis (enter/exit thresholds)
prevents flip-flopping at the boundary.
"""
from __future__ import annotations

import numpy as np
import polars as pl

TREND_UP, RANGE, TREND_DOWN = 1, 0, -1


def efficiency_ratio(n: int) -> pl.Expr:
    c = pl.col("close")
    net = (c - c.shift(n)).abs()
    path = c.diff().abs().rolling_sum(n)
    return net / path


def regime_series(bars: pl.DataFrame, er_n: int = 16,
                  enter_trend: float = 0.40, exit_trend: float = 0.30,
                  coarsen: int = 15) -> np.ndarray:
    """Per-bar regime: +1 trend up, -1 trend down, 0 range.

    ER is computed on every `coarsen`-th close (M1 noise inflates the path
    denominator so finely-sampled ER never signals trend — on one month of
    real XAUUSD M1, max ER(240) was 0.28). Defaults = 16 x 15m = 4h window;
    thresholds sit at ~p80/p65 of the observed 15m ER distribution.
    Enters a trend when ER >= enter_trend, exits when ER <= exit_trend."""
    n = len(bars)
    close = bars["close"].to_numpy()
    anchors = np.arange((n - 1) % coarsen, n, coarsen)  # last bar always included
    c = close[anchors]

    er = pl.DataFrame({"close": c}).select(er=efficiency_ratio(er_n))["er"].to_numpy()
    dirn = np.full(len(c), np.nan)
    dirn[er_n:] = np.sign(c[er_n:] - c[:-er_n])

    states = np.zeros(len(c))
    state = RANGE
    for t in range(len(c)):
        if not np.isnan(er[t]):
            if state == RANGE:
                if er[t] >= enter_trend and dirn[t] != 0:
                    state = int(dirn[t])
            else:
                if er[t] <= exit_trend:
                    state = RANGE
                elif er[t] >= enter_trend and dirn[t] != 0 and int(dirn[t]) != state:
                    state = int(dirn[t])  # direct trend reversal
        states[t] = state

    # each bar takes the state of the latest anchor at or before it
    pos = np.searchsorted(anchors, np.arange(n), side="right") - 1
    return np.where(pos >= 0, states[np.clip(pos, 0, None)], RANGE)
