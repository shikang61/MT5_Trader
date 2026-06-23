#!/usr/bin/env python
"""EURUSD->gold lead-lag study (M1, same-source dump).

    uv run scripts/research_leadlag.py sweep        # W x H x theta grid (after cost)
    uv run scripts/research_leadlag.py null         # sign-permutation null test
"""
from __future__ import annotations

import argparse

import numpy as np
import polars as pl

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.data.bars import time_bars
from mt5_trader.data.ingest import load_ticks
from mt5_trader.research import cost_bp
from mt5_trader.strategies.usd_leadlag import UsdLeadLag, attach_eur

DATA = "data/real"   # XAUUSD + EURUSD both here, same source/clock (verified tick-identical)
KILL = {"London": (7, 10), "NY": (12, 15)}


def build_bars(data: str = DATA) -> pl.DataFrame:
    gold = time_bars(load_ticks(data, "XAUUSD"), "1m")    # load_ticks drops the weekend by default
    eur = time_bars(load_ticks(data, "EURUSD"), "1m")
    return attach_eur(gold, eur).drop_nulls("eur_close")


def backtest(strat, bars: pl.DataFrame, slip_bp: float = 0.5):
    return run(bars, strat.signal(bars), BTConfig(slippage_bps=slip_bp))
