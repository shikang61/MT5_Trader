#!/usr/bin/env python
"""RESEARCH (not a live component): how much open profit do the trend books give
back on pullbacks, and does tick-volume (n_ticks) separate a reversal from a
resumption at favorable extremes? Decides whether a profit-ratchet (and a volume
gate) is worth building.

    uv run scripts/research_giveback.py
"""
from __future__ import annotations

import numpy as np
import polars as pl
import yaml

from mt5_trader.features.technicals import atr
from mt5_trader.pipeline.data import build_bars
from mt5_trader.strategies import REGISTRY
from mt5_trader.strategies.meanrev_pairs import spread_bars

TF_EVERY = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}
ATR_WINDOW = 60
FWD = 20          # bars ahead to classify reversal vs resumption after an extreme
VOL_WIN = 60      # lookback for the n_ticks z-score
TREND_BOOKS = {"donchian", "vwap_trend", "london_orb"}


def book_bars(book: dict):
    every = TF_EVERY[book.get("timeframe", "M1")]
    bars = build_bars(book["symbol"], every).sort("ts")
    if book.get("symbol2"):
        bars = spread_bars(bars, build_bars(book["symbol2"], every).sort("ts"),
                           float(book.get("beta", 1.0)))
    return bars


def main():
    with open("config/portfolio.yaml") as f:
        books = [b for b in yaml.safe_load(f)["books"] if b["strategy"] in TREND_BOOKS]
    for book in books:
        name = book["strategy"]
        bars = book_bars(book)
        sig = np.asarray(REGISTRY[name](**(book.get("params", {}) or {})).signal(bars), dtype=float)
        close = bars["close"].to_numpy().astype(float)
        atr_arr = bars.select(atr(ATR_WINDOW)).to_series().to_numpy().astype(float)
        nt = bars["n_ticks"].to_numpy().astype(float)
        s = pl.Series(nt)
        ntz = ((s - s.rolling_mean(VOL_WIN)) / s.rolling_std(VOL_WIN)).to_numpy()

        givebacks, rev_z, res_z = [], [], []
        side = 0.0; entry = ext = ae = 0.0
        for t in range(1, len(close) - FWD):
            s_t = sig[t - 1]
            if s_t == 0 or (side != 0 and (s_t > 0) != (side > 0)):
                side = 0.0
            if s_t != 0 and side == 0:           # fresh entry
                side, entry, ext, ae = s_t, close[t], close[t], atr_arr[t]
            if side == 0 or not (ae > 0):
                continue
            d = 1.0 if side > 0 else -1.0
            new_ext = max(ext, close[t]) if side > 0 else min(ext, close[t])
            if new_ext != ext:                    # a new favorable extreme printed
                ext = new_ext
                fav_atr = abs(ext - entry) / ae
                fwd = (close[t + FWD] - ext) * d   # >0 resumes, <0 reverses
                z = ntz[t]
                if z == z:
                    (res_z if fwd > 0 else rev_z).append(z)
                if fwd < 0 and fav_atr > 0:
                    givebacks.append(fav_atr)
        gb = np.array(givebacks); rz = np.array(rev_z); sz = np.array(res_z)
        print(f"\n=== {name}@{book['symbol']} {book.get('timeframe','M1')} ===")
        if len(gb):
            print(f"giveback-from-profitable-extreme (ATRs): n={len(gb)} "
                  f"median={np.median(gb):.2f} p75={np.percentile(gb,75):.2f} p90={np.percentile(gb,90):.2f}")
        rzm = rz.mean() if len(rz) else float('nan')
        szm = sz.mean() if len(sz) else float('nan')
        print(f"n_ticks z at REVERSAL extremes:   n={len(rz)} mean={rzm:.2f}")
        print(f"n_ticks z at RESUMPTION extremes: n={len(sz)} mean={szm:.2f}")
        if len(rz) and len(sz):
            print(f"  separation (rev mean - res mean) = {rzm - szm:+.2f}  "
                  f"(>0 supports the volume idea)")


if __name__ == "__main__":
    main()
