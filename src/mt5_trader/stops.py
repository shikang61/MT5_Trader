"""Stop-distance helpers shared by the backtester and the live runner (parity).
Pure: no MT5, no I/O."""
from __future__ import annotations


def breakeven_floor(sl: float, side: float, entry: float, atr_entry: float,
                    open_profit_atr: float, trigger: float, buffer: float) -> float:
    """Floor the chandelier stop at ~entry once open profit reaches `trigger` ATRs,
    so a winner cannot turn into a loss. Returns `sl` unchanged when `trigger <= 0`,
    not yet armed, or `atr_entry <= 0`. Long: raises sl up to `entry + buffer*atr`;
    short: lowers sl down to `entry - buffer*atr`. A deep winner whose disaster stop
    is already past entry is unaffected (the floor only ever moves sl toward entry,
    never loosens it)."""
    if trigger <= 0 or atr_entry <= 0 or open_profit_atr < trigger:
        return sl
    be = entry + (1.0 if side > 0 else -1.0) * buffer * atr_entry
    return max(sl, be) if side > 0 else min(sl, be)
