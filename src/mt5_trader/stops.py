"""Stop/position helpers shared by the backtester and the live runner (parity).
Pure: no MT5, no I/O."""
from __future__ import annotations


def scaleout_remaining(open_profit_atr: float, trigger_T: float, close_frac: float) -> float:
    """Fraction of the original position still held after partial profit-banking:
    1.0 below `trigger_T` ATRs of open profit, `(1 - close_frac)` at/above. Disabled
    (returns 1.0) when `close_frac <= 0` or `trigger_T <= 0`."""
    if close_frac <= 0 or trigger_T <= 0:
        return 1.0
    if open_profit_atr < trigger_T:
        return 1.0
    return 1.0 - close_frac
