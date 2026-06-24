#!/usr/bin/env python
"""RESEARCH: does the CUSUM regime gate beat the efficiency-ratio (ER) gate on the
trend books? For each book, take the RAW (ungated) signal and compare three gates
- none / ER / CUSUM (swept) - on the real feed. Keep CUSUM only if it beats ER on
Sharpe AND maxDD on a plateau.

    uv run scripts/research_cusum_regime.py
"""
from __future__ import annotations

import numpy as np
import yaml

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.features.regime import RANGE, cusum_regime, regime_series
from mt5_trader.pipeline.data import bars_per_year, build_bars
from mt5_trader.strategies import REGISTRY

TF_EVERY = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}
SL_ATR_MULT, ATR_WINDOW, SLIPPAGE_BPS = 8.0, 60, 0.5
DRIFT_K = [0.25, 0.5, 1.0]
THRESH_H = [3.0, 5.0, 8.0]
TREND_BOOKS = {"donchian", "vwap_trend", "london_orb"}


def metrics(bars, sig, bpy):
    cfg = BTConfig(sl_atr_mult=SL_ATR_MULT, atr_window=ATR_WINDOW,
                   slippage_bps=SLIPPAGE_BPS, bars_per_year=bpy)
    m = run(bars, np.asarray(sig, dtype=float), cfg).metrics
    return m["total_return"] * 100, m["sharpe"], m["max_drawdown"] * 100


def main():
    with open("config/portfolio.yaml") as f:
        books = [b for b in yaml.safe_load(f)["books"] if b["strategy"] in TREND_BOOKS]
    for book in books:
        name = book["strategy"]
        every = TF_EVERY[book.get("timeframe", "M1")]
        bars = build_bars(book["symbol"], every).sort("ts")
        bpy = bars_per_year(every)
        raw = np.asarray(REGISTRY[name]().signal(bars), dtype=float)  # ungated
        coarsen = int((book.get("params", {}) or {}).get("regime_coarsen", 15))
        print(f"\n=== {name}@{book['symbol']} {book.get('timeframe','M1')} ===")
        r, s, dd = metrics(bars, raw, bpy)
        print(f"{'none':>20}: ret {r:>7.2f}  Sh {s:>6.2f}  maxDD {dd:>7.2f}")
        er = regime_series(bars, coarsen=coarsen)
        r, s, dd = metrics(bars, np.where(er != RANGE, raw, 0.0), bpy)
        print(f"{'ER':>20}: ret {r:>7.2f}  Sh {s:>6.2f}  maxDD {dd:>7.2f}")
        for k in DRIFT_K:
            for h in THRESH_H:
                cu = cusum_regime(bars, drift_k=k, threshold_h=h, coarsen=coarsen)
                r, s, dd = metrics(bars, np.where(cu != RANGE, raw, 0.0), bpy)
                print(f"{'CUSUM k%.2f h%.1f'%(k,h):>20}: ret {r:>7.2f}  Sh {s:>6.2f}  maxDD {dd:>7.2f}")


if __name__ == "__main__":
    main()
