"""Split bars at abnormal time gaps so no signal or return spans them.

A stitched dataset — real history then later live capture — can leave a multi-day
HOLE: two adjacent bars sit days and a large % apart. A rolling indicator
(donchian channel, vwap) reads that jump as a phantom breakout and whipsaws for
many bars after. Normal weekend closes (~2 days for XAU) are NOT split, so a
clean continuous month behaves exactly as before.
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import polars as pl

DEFAULT_MAX_GAP = timedelta(days=3)   # > a weekend, < a multi-day data hole


def gap_bounds(bars: pl.DataFrame, max_gap: timedelta = DEFAULT_MAX_GAP) -> list[tuple[int, int]]:
    """[(lo, hi), …] index bounds of contiguous runs, split where the bar-to-bar
    time delta exceeds max_gap."""
    dt = bars["ts"].diff().dt.total_seconds()
    thresh = max_gap.total_seconds()
    breaks = [i for i in range(1, len(dt)) if dt[i] is not None and dt[i] > thresh]
    edges = [0, *breaks, len(bars)]
    return [(edges[k], edges[k + 1]) for k in range(len(edges) - 1)]


def segmented_signal(bars: pl.DataFrame, strategy, max_gap: timedelta = DEFAULT_MAX_GAP) -> np.ndarray:
    """strategy.signal() computed per contiguous segment, so no rolling window
    spans a gap; forced flat on each segment's last bar so the cross-gap move
    earns no P&L (you don't hold across a multi-day data blackout)."""
    pos = np.zeros(len(bars))
    for lo, hi in gap_bounds(bars, max_gap):
        seg = np.asarray(strategy.signal(bars[lo:hi]), dtype=float).copy()
        if len(seg):
            seg[-1] = 0.0
            pos[lo:hi] = seg
    return pos
