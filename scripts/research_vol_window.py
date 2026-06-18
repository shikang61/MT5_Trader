#!/usr/bin/env python
"""RESEARCH: does a shorter realized-vol sizing window de-risk faster after a
sharp move without hurting the book? vol-targeting uses realized_vol(vol_window)
to set leverage (lev = min(target_vol/realized_vol, max_lev)); a shorter window
reacts to a vol spike in ~1h instead of ~4h, but makes normal sizing noisier.

Nets the 6 books' vol-targeted fracs on a 1m grid at several windows; reports
portfolio ret/Sharpe/maxDD/turnover (lev=1, relative read).

    uv run scripts/research_vol_window.py
"""
from __future__ import annotations

import numpy as np
import polars as pl
import yaml

from mt5_trader.features.technicals import realized_vol
from mt5_trader.pipeline.data import bars_per_year, build_bars
from mt5_trader.strategies import REGISTRY
from mt5_trader.strategies.meanrev_pairs import spread_bars

TF_EVERY = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}
TARGET_VOL, MAX_LEV = 0.30, 2.0
WINDOWS = [60, 120, 180, 240]   # realized-vol bar window (240 = current live)
SLIP = 0.5e-4
_cache: dict = {}


def gb(every: str):
    if every not in _cache:
        _cache[every] = build_bars("XAUUSD", every).sort("ts")
    return _cache[every]


def book_frac_1m(bk: dict, master: pl.DataFrame, vol_win: int) -> np.ndarray:
    name, tf = bk["strategy"], bk.get("timeframe", "M1")
    every, w = TF_EVERY[tf], float(bk.get("weight", 1.0))
    bars = (spread_bars(gb(every), build_bars("XAGUSD", every).sort("ts"), 1.0)
            if name == "ratio_mr" else gb(every))
    sig = np.asarray(REGISTRY[name](**(bk.get("params", {}) or {})).signal(bars), dtype=float)
    vol = bars.select(realized_vol(vol_win, bars_per_year(every))).to_series().to_numpy()
    lev = np.minimum(TARGET_VOL / np.maximum(vol, 1e-4), MAX_LEV)
    frac = np.nan_to_num(sig * lev) * w
    sdf = pl.DataFrame({"ts": bars["ts"], "s": frac}).sort("ts")
    return np.nan_to_num(master.select("ts").join_asof(sdf, on="ts", strategy="backward")["s"].to_numpy())


def main():
    books = yaml.safe_load(open("config/portfolio.yaml"))["books"]
    master = gb("1m")
    close = master["close"].to_numpy()
    ret = np.concatenate([[0.0], close[1:] / close[:-1] - 1.0])
    sp = master["spread_mean"].fill_null(0).to_numpy() / np.where(close == 0, 1, close)
    byr = bars_per_year("1m")

    print(f"{'vol_window':<12}{'~hrs(M5)':>9}{'ret':>9}{'sharpe':>8}{'maxDD':>9}{'turn':>8}{'|net|max':>9}")
    for vw in WINDOWS:
        net = sum(book_frac_1m(b, master, vw) for b in books)
        pos = np.concatenate([[0.0], net[:-1]])
        turn = np.abs(np.diff(pos, prepend=0.0))
        pnl = pos * ret - turn * (sp / 2 + SLIP)
        eq = np.cumprod(1.0 + pnl)
        dd = (eq / np.maximum.accumulate(eq) - 1.0).min()
        sh = pnl.mean() / pnl.std() * np.sqrt(byr) if pnl.std() > 0 else 0.0
        tag = "  <- live" if vw == 240 else ""
        print(f"{vw:<12}{vw*5/60:>9.1f}{eq[-1]-1:>+9.4f}{sh:>8.2f}{dd:>9.4f}"
              f"{turn.sum():>8.0f}{np.abs(net).max():>9.2f}{tag}")
    print("\nShorter window = faster de-size after a spike, noisier normal sizing.")
    print("Want: lower maxDD without losing ret. ~1 month, lev=1 relative read.")


if __name__ == "__main__":
    main()
