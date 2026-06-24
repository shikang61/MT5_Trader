"""Position helpers shared by the backtester and the live runner so a turnover
change is identical in research and production. Pure: no MT5, no I/O."""
from __future__ import annotations

import numpy as np


def apply_deadband(target: float, held: float, band: float) -> float:
    """Hold `held` until `target` moves more than `band` away from it, then snap
    to `target`. `target`/`held` are normalized positions in [-1, 1]; `band` is in
    the same units. `band <= 0` disables (returns `target`)."""
    if band <= 0:
        return target
    return held if abs(target - held) <= band else target


def deadband_series(pos: np.ndarray, band: float) -> np.ndarray:
    """Apply `apply_deadband` sequentially over a position array, carrying the held
    position forward (starts flat). `band <= 0` returns `pos` unchanged."""
    if band <= 0:
        return pos
    out = np.empty_like(pos)
    held = 0.0
    for i in range(len(pos)):
        held = apply_deadband(float(pos[i]), held, band)
        out[i] = held
    return out
