"""Retest-entry fill model for breakout strategies (backtest-only).

Delays each breakout entry to a pullback of the broken channel edge: when the
raw {-1,0,1} signal opens a new nonzero episode, an anchor L is captured at the
breakout bar (entry_hi for a long, entry_lo for a short) and a resting limit at
L is simulated. A buy-limit fills the first LATER bar whose low<=L (a sell-limit
high>=L), priced at L. If the strategy's own exit fires before a fill, the trade
is skipped; if max_wait bars pass with no fill, it is either skipped
(fallback=False) or entered at market (fallback=True).

The output position is held only while the raw signal is nonzero, so EXITS match
the baseline exactly — only the entry changes. Causal by construction: bar t uses
data up to and including bar t (the limit rests at the breakout bar's close, so
the earliest possible fill is the next bar).

Optimistic-fill caveat: a bar whose low touches L is assumed filled at L; real
fills depend on queue position and wick depth (confirm later via tick replay).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class RetestFill:
    pos: np.ndarray        # transformed position series {-1,0,1}; entry delayed to fill bar
    entry_px: np.ndarray   # L at limit fills, close at market fallbacks, NaN elsewhere
    n_armed: int           # breakout episodes seen
    n_filled: int          # entered by the resting limit at the anchor
    n_fallback: int        # entered at market after max_wait (fallback=True only)
    n_skipped: int         # never entered (exit fired first, or wait expired under skip)


def simulate_retest_entry(raw_signal, entry_hi, entry_lo, high, low, close,
                          max_wait: int, fallback: bool) -> RetestFill:
    raw = np.asarray(raw_signal, dtype=float)
    n = len(raw)
    pos = np.zeros(n)
    entry_px = np.full(n, np.nan)
    n_armed = n_filled = n_fallback = n_skipped = 0
    in_pos = armed = resolved = False
    side = L = 0.0
    wait = 0
    for t in range(n):
        s = raw[t]
        if s == 0.0:                       # flat: episode (if any) is over
            if armed:                      # was still waiting -> exit beat the fill
                n_skipped += 1
            in_pos = armed = resolved = False
            continue
        if in_pos:                         # already filled -> hold to the exit
            pos[t] = side
            continue
        if resolved:                       # decided (skipped) -> wait for flat, no re-arm
            continue
        if not armed:                      # new breakout episode at this bar
            armed, side, wait = True, s, 0
            L = entry_hi[t] if s > 0 else entry_lo[t]
            n_armed += 1
            continue                       # causal: earliest fill is the NEXT bar
        wait += 1                          # armed and waiting for the retest
        hit = low[t] <= L if side > 0 else high[t] >= L
        if hit:
            in_pos, armed = True, False
            pos[t], entry_px[t] = side, L
            n_filled += 1
        elif wait >= max_wait:
            armed, resolved = False, True
            if fallback:
                in_pos = True
                pos[t], entry_px[t] = side, close[t]
                n_fallback += 1
            else:
                n_skipped += 1
    return RetestFill(pos, entry_px, n_armed, n_filled, n_fallback, n_skipped)
