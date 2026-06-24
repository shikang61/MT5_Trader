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
    # Anchor the sampling grid to the START, not the end: appending bars only
    # adds anchors, never shifts existing ones, so the regime at any past bar is
    # independent of how many bars come after it (causal — backtest == the
    # incremental live path). The latest regime can lag up to coarsen-1 bars.
    anchors = np.arange(0, n, coarsen)
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


def cusum_regime(bars: pl.DataFrame, drift_k: float = 0.5, threshold_h: float = 5.0,
                 vol_window: int = 60, hold: int = 30, coarsen: int = 15) -> np.ndarray:
    """Per-bar regime {+1 trend up, 0 range, -1 trend down} from a causal two-sided
    CUSUM on volatility-standardized coarsened returns. Same shape/semantics as
    regime_series so a book can gate on `regime != RANGE` with either method.
    threshold_h <= 0 disables -> all TREND_UP (gate is a no-op)."""
    n = len(bars)
    if n == 0:
        return np.zeros(0)
    if threshold_h <= 0:
        return np.full(n, TREND_UP, dtype=float)
    close = bars["close"].to_numpy().astype(float)
    anchors = np.arange(0, n, coarsen)              # causal grid anchored to start
    c = close[anchors]
    ret = np.diff(c, prepend=c[0])                  # ret[0] = 0
    sd = pl.Series(ret).rolling_std(vol_window).to_numpy()

    states_a = np.zeros(len(c))
    state = RANGE
    gp = gn = 0.0
    since = 0
    for t in range(len(c)):
        s = sd[t]
        if s is None or s != s or s <= 0:           # undefined / zero vol -> hold state
            states_a[t] = state
            continue
        z = ret[t] / s
        gp = max(0.0, gp + z - drift_k)
        gn = max(0.0, gn - z - drift_k)
        if gp > threshold_h:
            state, gp, gn, since = TREND_UP, 0.0, 0.0, 0
        elif gn > threshold_h:
            state, gp, gn, since = TREND_DOWN, 0.0, 0.0, 0
        else:
            since += 1
            if hold > 0 and since >= hold:
                state = RANGE
        states_a[t] = state

    states = np.zeros(n)
    for i, a in enumerate(anchors):
        end = anchors[i + 1] if i + 1 < len(anchors) else n
        states[a:end] = states_a[i]
    return states
