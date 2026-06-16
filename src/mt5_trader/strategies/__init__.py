from .base import REGISTRY, register
from . import asian_sweep, fast_meanrev, momentum_breakout, orderflow_imbalance, meanrev_pairs, regime_switch, session_vol, sweep_fade, vwap_trend  # noqa: F401 — populate registry

__all__ = ["REGISTRY", "register"]
