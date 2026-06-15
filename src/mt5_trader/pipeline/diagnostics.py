"""Stage-2 signal diagnostics: characterise a raw signal BEFORE trusting a
backtest of it. Pure functions on arrays/frames.

ANALYSIS ONLY — `forward_return` deliberately looks ahead (that is the point: we
measure whether signal[t] predicts the future). Never use it inside a strategy's
signal(); strategies must stay causal (see strategies/base.py).
"""
from __future__ import annotations

import numpy as np
import polars as pl

from ..features.regime import RANGE, TREND_DOWN, TREND_UP, regime_series


def forward_return(bars: pl.DataFrame, horizon: int = 1) -> np.ndarray:
    """close[t+horizon]/close[t] - 1, NaN in the last `horizon` slots."""
    close = bars["close"].to_numpy().astype(float)
    fwd = np.full(len(close), np.nan)
    fwd[:-horizon] = close[horizon:] / close[:-horizon] - 1.0
    return fwd


def _clean(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    m = np.isfinite(a) & np.isfinite(b)
    return a[m], b[m]


def information_coefficient(signal: np.ndarray, fwd: np.ndarray) -> float:
    """Pearson correlation of signal vs forward return (the linear IC)."""
    a, b = _clean(np.asarray(signal, float), np.asarray(fwd, float))
    if len(a) < 2 or a.std() == 0 or b.std() == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def rank_ic(signal: np.ndarray, fwd: np.ndarray) -> float:
    """Spearman rank IC = Pearson IC on the ranks (robust to outliers)."""
    a, b = _clean(np.asarray(signal, float), np.asarray(fwd, float))
    if len(a) < 2:
        return 0.0
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    return information_coefficient(ra, rb)


def turnover(signal: np.ndarray) -> float:
    """Total absolute position change — the cost driver."""
    s = np.nan_to_num(np.asarray(signal, float))
    return float(np.abs(np.diff(s, prepend=0.0)).sum())


def signal_decay(bars: pl.DataFrame, signal: np.ndarray,
                 horizons: tuple[int, ...] = (1, 2, 4, 8, 16, 32)) -> dict:
    """IC at each forward horizon + an estimated half-life (the horizon at which
    IC first falls below half its peak). Sets the natural holding period."""
    ics = {h: information_coefficient(signal, forward_return(bars, h)) for h in horizons}
    peak = max((abs(v) for v in ics.values()), default=0.0)
    half_life = None
    if peak > 0:
        for h in horizons:
            if abs(ics[h]) < peak / 2:
                half_life = h
                break
    return {"ic_by_horizon": ics, "half_life": half_life}


def by_regime(bars: pl.DataFrame, signal: np.ndarray, horizon: int = 1) -> dict:
    """IC sliced by Kaufman-efficiency-ratio regime — does the edge live in
    trend, range, or both?"""
    regime = regime_series(bars)
    fwd = forward_return(bars, horizon)
    sig = np.asarray(signal, float)
    labels = {TREND_UP: "trend_up", RANGE: "range", TREND_DOWN: "trend_down"}
    out = {}
    for code, label in labels.items():
        mask = regime == code
        out[label] = {
            "n": int(mask.sum()),
            "ic": information_coefficient(sig[mask], fwd[mask]) if mask.any() else 0.0,
        }
    return out


def diagnose(bars: pl.DataFrame, signal: np.ndarray, horizon: int = 1) -> dict:
    """Full Stage-2 report for a signal on these bars."""
    fwd = forward_return(bars, horizon)
    return {
        "ic": information_coefficient(signal, fwd),
        "rank_ic": rank_ic(signal, fwd),
        "turnover": turnover(signal),
        "active_frac": float((np.nan_to_num(signal) != 0).mean()),
        "decay": signal_decay(bars, signal),
        "by_regime": by_regime(bars, signal, horizon),
    }
