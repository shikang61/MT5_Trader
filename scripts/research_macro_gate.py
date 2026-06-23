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
from mt5_trader.pipeline.data import cached_bars
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
    # disk-cached per (symbol, timeframe) so the sweep builds bars once, not per cell
    gold = cached_bars("XAUUSD", timeframe, data_dir=DATA)
    eur = cached_bars("EURUSD", timeframe, data_dir=DATA)
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


SPAN_GRID = [240, 480, 960]      # bars of the book's timeframe
LAG_GRID = [60, 120, 240]
BAND_GRID = [0.0, 0.0005, 0.001, 0.002]   # fractional EURUSD slope dead-zone


def sweep(slip_bp: float = 0.5) -> None:
    for book in BOOKS:
        print(f"\n===== {book} (gated vs ungated, {slip_bp}bp slip) =====")
        base = evaluate(book, SPAN_GRID[0], LAG_GRID[0], BAND_GRID[0], slip_bp)["ungated"]
        print(f"UNGATED baseline: ret={base['ret']:+.4f} sharpe={base['sharpe']:.2f} "
              f"mdd={base['mdd']:.4f} turn={base['turn']:.1f}")
        print(f"{'span':>5}{'lag':>5}{'band':>8}{'g_ret':>9}{'g_sh':>7}{'g_mdd':>9}{'g_turn':>9}{'WIN?':>6}")
        wins = []
        for span in SPAN_GRID:
            for lag in LAG_GRID:
                for band in BAND_GRID:
                    e = evaluate(book, span, lag, band, slip_bp)
                    g, u = e["gated"], e["ungated"]
                    win = (g["ret"] > u["ret"] and g["mdd"] > u["mdd"] and g["turn"] < u["turn"])
                    # note: max_drawdown is negative; "lower DD" == closer to 0 == g['mdd'] > u['mdd']
                    print(f"{span:>5}{lag:>5}{band:>8.4f}{g['ret']:>9.4f}{g['sharpe']:>7.2f}"
                          f"{g['mdd']:>9.4f}{g['turn']:>9.1f}{'  YES' if win else '   no':>6}")
                    if win:
                        wins.append((span, lag, band))
        print(f"WIN cells (ret up, DD smaller, turnover down): {wins}")
        print("--- need a CONTIGUOUS plateau of WIN cells, not isolated hits ---")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sweep", "placebo"])
    args = ap.parse_args()
    if args.cmd == "sweep":
        sweep()
    elif args.cmd == "placebo":
        placebo()      # Task 4


if __name__ == "__main__":
    main()
