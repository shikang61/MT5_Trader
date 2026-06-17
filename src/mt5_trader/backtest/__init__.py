"""Backtesting: signal -> sized P&L (engine), performance metrics, per-book
attribution, and gap-aware segmented signals. Public API for contributors."""
from .attribution import attribute, format_report, load_steps
from .engine import BTConfig, BTResult, run
from .metrics import max_drawdown, sharpe, sortino, summary
from .segment import gap_bounds, segmented_signal

__all__ = [
    "BTConfig", "BTResult", "run",
    "sharpe", "sortino", "max_drawdown", "summary",
    "attribute", "format_report", "load_steps",
    "gap_bounds", "segmented_signal",
]
