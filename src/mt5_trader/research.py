"""Reusable research / event-study primitives.

Consolidates helpers that were duplicated across scripts/research_*.py so
notebooks and scripts share one tested implementation. All pure functions
except load_1s (IO via load_ticks).
"""
from __future__ import annotations

import numpy as np
import polars as pl

from .data.ingest import load_ticks
from .pipeline import trials

SLIP_BP = 0.5

# Each report()/verdict() call scores one event-study cell = one tested
# hypothesis. Logging it grows research/trials.jsonl so validate.py's deflated-
# Sharpe haircut counts the REAL search (the count was stuck at 2 while ~24
# sweep scripts bypassed the log). Tests flip this off (see test_research.py).
LOG_TRIALS = True


def cost_bp(df: pl.DataFrame, spread_col: str = "spread_mean",
            mid_col: str = "close", slip_bp: float = SLIP_BP) -> float:
    """Round-trip cost in bp: mean(spread/mid)*1e4 + slip. Bars use the defaults
    (spread_mean/close); 1s grids pass spread_col='spread', mid_col='mid'."""
    return float((df[spread_col] / df[mid_col]).mean() * 1e4 + slip_bp)


def fwd_returns(close: np.ndarray, horizon: int) -> np.ndarray:
    """Forward log return over `horizon` bars, entry at the next bar (no
    lookahead). NaN in the final horizon+1 slots."""
    fwd = np.full(len(close), np.nan)
    fwd[: len(close) - horizon - 1] = (
        np.log(close[horizon + 1:]) - np.log(close[1: len(close) - horizon]))
    return fwd


def dedupe(idx: np.ndarray, min_gap: int) -> np.ndarray:
    """Greedily keep indices at least `min_gap` apart."""
    keep, last = [], -10**12
    for i in idx:
        if i - last >= min_gap:
            keep.append(i)
            last = i
    return np.array(keep, dtype=int)


def aligned_fwd(sig: np.ndarray, fwd: np.ndarray,
                events: np.ndarray) -> tuple[float, float, int]:
    """(mean signed forward bp, hit rate, n) over event indices. Returns
    (0.0, 0.0, 0) if no event has a finite forward return."""
    s = np.sign(sig[events])
    f = fwd[events]
    ok = ~np.isnan(f)
    if ok.sum() == 0:
        return 0.0, 0.0, 0
    pnl = s[ok] * f[ok] * 1e4
    return float(pnl.mean()), float((pnl > 0).mean()), int(ok.sum())


def sweep_events(bars: pl.DataFrame, levels: np.ndarray, side: int,
                 quiet: int = 60, confirm: int = 5) -> tuple[np.ndarray, np.ndarray]:
    """First bar where price crosses per-bar `levels` after sitting on one side
    for `quiet` bars. side=+1 up through a high-side level (buy stops above),
    side=-1 down through a low-side level. Returns (event bar indices, reclaim
    flags at `confirm` bars later)."""
    high, low = bars["high"].to_numpy(), bars["low"].to_numpy()
    close = bars["close"].to_numpy()
    beyond = high > levels if side > 0 else low < levels
    idx, reclaim = [], []
    last = -10**9
    for t in np.flatnonzero(beyond):
        if np.isnan(levels[t]) or t - last < quiet or t + confirm >= len(close):
            continue
        s = max(0, t - quiet)
        prior = beyond[s:t] & (levels[s:t] == levels[t])
        if prior.any():
            continue
        idx.append(t)
        c = close[t + confirm]
        reclaim.append(c < levels[t] if side > 0 else c > levels[t])
        last = t
    return np.array(idx, dtype=int), np.array(reclaim, dtype=bool)


def _grid_1s(ticks: pl.DataFrame) -> pl.DataFrame:
    """1s grid of last mid + mean spread, forward-filled inside gaps."""
    return (
        ticks.with_columns(mid=(pl.col("bid") + pl.col("ask")) / 2,
                           spread=pl.col("ask") - pl.col("bid"))
        .group_by_dynamic("ts", every="1s")
        .agg(mid=pl.col("mid").last(), spread=pl.col("spread").mean())
        .upsample(time_column="ts", every="1s")
        .with_columns(pl.col("mid", "spread").forward_fill())
        .drop_nulls()
    )


def load_1s(symbol: str, sources=("data/real", "data/ticks")) -> pl.DataFrame:
    """1s mid/spread grid for `symbol` from one or more tick source dirs."""
    return _grid_1s(load_ticks(list(sources), symbol))


def report(label: str, signed_fwd_bp: np.ndarray, hurdle: float) -> None:
    """Print a PASS/FAIL event-study line (research_sweep format)."""
    ok = ~np.isnan(signed_fwd_bp)
    if ok.sum() == 0:
        print(f"  {label}: no events")
        return
    e = signed_fwd_bp[ok]
    status = "PASS" if e.mean() > hurdle else "FAIL"
    print(f"  {label}: {status} edge {e.mean():+.2f} bp (cost {hurdle:.2f}), "
          f"n={ok.sum()}, hit {np.mean(e > 0):.1%}")
    if LOG_TRIALS:
        trials.log_trial(label, verdict=status)


def verdict(name: str, edge_bp: float, hurdle_bp: float, n: int, hit: float) -> None:
    """Print a PASS/FAIL verdict line (research_hf format)."""
    status = "PASS" if edge_bp > hurdle_bp else "FAIL"
    print(f"  -> {status}: edge {edge_bp:+.2f} bp/trade vs cost {hurdle_bp:.2f} bp "
          f"({n} events, hit {hit:.1%})\n")
    if LOG_TRIALS:
        trials.log_trial(name, verdict=status)
