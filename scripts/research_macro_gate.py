#!/usr/bin/env python
"""Macro-direction gate study: does a slow EURUSD-trend veto improve the trend books?

    uv run scripts/research_macro_gate.py sweep      # gated vs ungated grid, both books
    uv run scripts/research_macro_gate.py placebo     # random-veto placebo test
"""
from __future__ import annotations

import argparse

import numpy as np
import polars as pl

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.data.bars import time_bars
from mt5_trader.data.ingest import load_ticks
from mt5_trader.strategies.macro_gate import macro_gate, macro_signal
from mt5_trader.strategies.momentum_breakout import DonchianBreakout
from mt5_trader.strategies.session_vol import LondonBreakout
from mt5_trader.strategies.usd_leadlag import attach_eur

DATA = "data/real"
# book name -> (strategy instance, live timeframe)
BOOKS = {
    "donchian": (DonchianBreakout(), "5m"),
    "london_orb": (LondonBreakout(), "1m"),
}


def build_bars(timeframe: str) -> pl.DataFrame:
    gold = time_bars(load_ticks(DATA, "XAUUSD"), timeframe)
    eur = time_bars(load_ticks(DATA, "EURUSD"), timeframe)
    return attach_eur(gold, eur).drop_nulls("eur_close")


def _metrics(bars: pl.DataFrame, signal: np.ndarray, slip_bp: float) -> dict:
    m = run(bars, signal, BTConfig(slippage_bps=slip_bp)).metrics
    return {"ret": m["total_return"], "sharpe": m["sharpe"],
            "mdd": m["max_drawdown"], "turn": m.get("total_turnover", float("nan"))}


def evaluate(book_name: str, trend_span: int, slope_lag: int, band: float,
             slip_bp: float = 0.5) -> dict:
    strat, tf = BOOKS[book_name]
    bars = build_bars(tf)
    base = strat.signal(bars)
    mdir = macro_signal(bars, trend_span, slope_lag, band)
    gated = macro_gate(base, mdir)
    return {"ungated": _metrics(bars, base, slip_bp),
            "gated": _metrics(bars, gated, slip_bp),
            "n_bars": len(bars), "vetoed": int(((base != 0) & (gated == 0)).sum())}
