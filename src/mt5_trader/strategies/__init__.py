from .base import REGISTRY, register
from . import momentum_breakout, orderflow_imbalance, meanrev_pairs, regime_switch, session_vol, vwap_trend  # noqa: F401 — populate registry

__all__ = ["REGISTRY", "register"]
