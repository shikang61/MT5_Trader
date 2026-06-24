"""Stop-distance helpers shared by the backtester and the live runner (parity).
Pure: no MT5, no I/O."""
from __future__ import annotations


def ratchet_mult(open_profit_atr: float, sl_mult: float, trigger_T: float,
                 ratchet_R: float, ratchet_R_tight: float = 0.0,
                 volume_spike: bool = False) -> float:
    """Effective chandelier multiplier. Returns `sl_mult` (the wide disaster stop)
    until the position's favorable excursion reaches `trigger_T` ATRs, then the
    tighter `ratchet_R` — or `ratchet_R_tight` when `volume_spike` and
    `ratchet_R_tight > 0`. Disabled (returns `sl_mult`) when `ratchet_R <= 0` or
    `trigger_T <= 0`."""
    if ratchet_R <= 0 or trigger_T <= 0:
        return sl_mult
    if open_profit_atr < trigger_T:
        return sl_mult
    if volume_spike and ratchet_R_tight > 0:
        return ratchet_R_tight
    return ratchet_R
