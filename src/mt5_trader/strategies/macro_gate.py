"""Macro-direction gate: veto trend-book trades that fight the slow USD trend.

A slow EURUSD EMA-slope gives a macro direction (EURUSD up -> USD down ->
gold-supportive). macro_gate zeros any book position that disagrees; it never
opens or flips a position, so gated turnover <= ungated. Backtest-only filter on
donchian/london_orb. See docs/superpowers/specs/2026-06-23-macro-gate-design.md.
"""
from __future__ import annotations

import numpy as np
import polars as pl


def macro_signal(bars: pl.DataFrame, trend_span: int = 480,
                 slope_lag: int = 120, band: float = 0.0) -> np.ndarray:
    """Slow EURUSD trend direction in {-1,0,+1} (needs bars["eur_close"]).
    +1 = EURUSD rising (USD down, gold-supportive); -1 = falling; 0 = within the
    neutral band (|fractional slope| <= band). Causal: EMA + backward shift only."""
    ema = bars.select(pl.col("eur_close").ewm_mean(span=trend_span))["eur_close"].to_numpy()
    slope = np.full(len(ema), np.nan)
    if len(ema) > slope_lag:
        slope[slope_lag:] = ema[slope_lag:] / ema[:-slope_lag] - 1.0
    out = np.zeros(len(ema))
    out[slope > band] = 1.0
    out[slope < -band] = -1.0
    return out


def macro_gate(book_signal: np.ndarray, macro_dir: np.ndarray) -> np.ndarray:
    """Zero any book position that disagrees with macro_dir. macro_dir>0 permits
    longs (vetoes shorts), <0 permits shorts (vetoes longs), ==0 permits both.
    Only ever zeros a position — never opens or flips one."""
    book = np.asarray(book_signal, dtype=float)
    md = np.asarray(macro_dir, dtype=float)
    veto = ((md > 0) & (book < 0)) | ((md < 0) & (book > 0))
    return np.where(veto, 0.0, book)
