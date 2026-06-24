#!/usr/bin/env python
"""RESEARCH: sweep the profit-ratchet (trigger T, ratchet R) per trend book on the
real feed. Deploy a book's ratchet only if it keeps/raises Sharpe on a plateau
vs the no-ratchet baseline.

    uv run scripts/research_ratchet.py
"""
from __future__ import annotations

import yaml

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.pipeline.data import bars_per_year, build_bars
from mt5_trader.strategies import REGISTRY
from mt5_trader.strategies.meanrev_pairs import spread_bars

TF_EVERY = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}
SL_ATR_MULT, ATR_WINDOW, SLIPPAGE_BPS = 8.0, 60, 0.5
TRIGGERS = [0.0, 1.0, 2.0, 3.0, 4.0]   # 0 = baseline (no ratchet)
RATCHETS = [2.0, 3.0, 4.0]
TREND_BOOKS = {"donchian", "vwap_trend", "london_orb"}


def book_bars(book: dict):
    every = TF_EVERY[book.get("timeframe", "M1")]
    bars = build_bars(book["symbol"], every).sort("ts")
    if book.get("symbol2"):
        bars = spread_bars(bars, build_bars(book["symbol2"], every).sort("ts"),
                           float(book.get("beta", 1.0)))
    return bars, bars_per_year(every)


def main():
    with open("config/portfolio.yaml") as f:
        books = [b for b in yaml.safe_load(f)["books"] if b["strategy"] in TREND_BOOKS]
    for book in books:
        name = book["strategy"]
        bars, bpy = book_bars(book)
        sig = REGISTRY[name](**(book.get("params", {}) or {})).signal(bars)
        print(f"\n=== {name}@{book['symbol']} {book.get('timeframe','M1')} ===")
        print(f"{'T':>4}{'R':>5}{'return%':>10}{'sharpe':>9}{'maxDD%':>9}{'turnover':>10}")
        for T in TRIGGERS:
            for R in ([0.0] if T == 0 else RATCHETS):
                cfg = BTConfig(sl_atr_mult=SL_ATR_MULT, atr_window=ATR_WINDOW,
                               slippage_bps=SLIPPAGE_BPS, bars_per_year=bpy,
                               ratchet_trigger=T, ratchet_r=R)
                m = run(bars, sig, cfg).metrics
                print(f"{T:>4.0f}{R:>5.1f}{m['total_return']*100:>10.2f}"
                      f"{m['sharpe']:>9.2f}{m['max_drawdown']*100:>9.2f}"
                      f"{m['total_turnover']:>10.2f}")


if __name__ == "__main__":
    main()
