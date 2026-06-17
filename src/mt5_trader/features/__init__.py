"""Feature engineering: technicals, regime classification, microstructure.
Public API for contributors."""
from .microstructure import with_micro_features
from .regime import RANGE, TREND_DOWN, TREND_UP, efficiency_ratio, regime_series
from .technicals import add_sessions, atr, ema, realized_vol, rsi, zscore
from .vol_guard import vol_spike_mask

__all__ = [
    "ema", "zscore", "rsi", "atr", "realized_vol", "add_sessions",
    "efficiency_ratio", "regime_series", "RANGE", "TREND_UP", "TREND_DOWN",
    "with_micro_features", "vol_spike_mask",
]
