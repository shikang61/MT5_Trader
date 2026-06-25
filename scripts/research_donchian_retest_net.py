#!/usr/bin/env python
"""STAGE 2 (research, not a live component): does swapping donchian ->
donchian_retest improve the NET 6-book portfolio? Standalone book gains routinely
wash out in the netted book (the lesson behind validating overlays on the net),
so THIS is the gate that matters. Also prints the standard 6-gate
validate_candidate report for donchian_retest.

Set BEST_MAX_WAIT / BEST_FALLBACK from the Stage-1 winner before running.
CAVEATS: net builder is an APPROXIMATE relative comparison (close-to-close, 1m
grid), and ~5wk sample -> directional, not a deploy gate (DSR expected to fail).

    uv run scripts/research_donchian_retest_net.py
"""
from __future__ import annotations

import numpy as np
import polars as pl
import yaml

from mt5_trader.features.technicals import realized_vol
from mt5_trader.pipeline.data import bars_per_year, build_bars
from mt5_trader.pipeline.validate import validate_candidate
from mt5_trader.strategies import REGISTRY
from mt5_trader.strategies.meanrev_pairs import spread_bars

SYMBOL = "XAUUSD"
TF_EVERY = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}
SLIP = 0.5e-4
TARGET_VOL, MAX_LEV, VOL_WIN = 0.30, 2.0, 240
BEST_MAX_WAIT, BEST_FALLBACK = 12, False   # <-- Stage-1 winner: mw12/skip
_cache: dict[str, pl.DataFrame] = {}


def gb(every: str) -> pl.DataFrame:
    if every not in _cache:
        _cache[every] = build_bars(SYMBOL, every).sort("ts")
    return _cache[every]


def book_frac_1m(book: dict, master: pl.DataFrame) -> np.ndarray:
    name, tf = book["strategy"], book.get("timeframe", "M1")
    every, w = TF_EVERY[tf], float(book.get("weight", 1.0))
    params = book.get("params", {}) or {}
    bars = (spread_bars(gb(every), build_bars("XAGUSD", every).sort("ts"), 1.0)
            if name == "ratio_mr" else gb(every))
    sig = np.asarray(REGISTRY[name](**params).signal(bars), dtype=float)
    vol = bars.select(realized_vol(VOL_WIN, bars_per_year(every))).to_series().to_numpy()
    lev = np.minimum(TARGET_VOL / np.maximum(vol, 1e-4), MAX_LEV)
    frac = np.nan_to_num(sig * lev) * w
    sdf = pl.DataFrame({"ts": bars["ts"], "s": frac}).sort("ts")
    joined = master.select("ts").join_asof(sdf, on="ts", strategy="backward")
    return np.nan_to_num(joined["s"].to_numpy())


def net_metrics(books: list[dict], master: pl.DataFrame, byr: float) -> dict:
    expo = sum(book_frac_1m(b, master) for b in books)
    close = master["close"].to_numpy()
    ret = np.concatenate([[0.0], close[1:] / close[:-1] - 1.0])
    sp = master["spread_mean"].fill_null(0).to_numpy() / np.where(close == 0, 1.0, close)
    pos = np.concatenate([[0.0], expo[:-1]])
    turn = np.abs(np.diff(pos, prepend=0.0))
    pnl = pos * ret - turn * (sp / 2 + SLIP)
    eq = np.cumprod(1.0 + pnl)
    dd = float((eq / np.maximum.accumulate(eq) - 1.0).min())
    sharpe = float(pnl.mean() / pnl.std() * np.sqrt(byr)) if pnl.std() > 0 else 0.0
    return {"ret": float(eq[-1] - 1.0), "sharpe": sharpe, "maxDD": dd,
            "turn": float(turn.sum())}


def main():
    books = yaml.safe_load(open("config/portfolio.yaml"))["books"]
    master = gb("1m")
    byr = bars_per_year("1m")

    base = net_metrics(books, master, byr)
    swapped = []
    for b in books:
        if b["strategy"] == "donchian":
            b = {**b, "strategy": "donchian_retest",
                 "params": {**(b.get("params") or {}),
                            "max_wait": BEST_MAX_WAIT, "fallback": BEST_FALLBACK}}
        swapped.append(b)
    retest = net_metrics(swapped, master, byr)

    print(f"net (donchian baseline): ret {base['ret']:+.4f}  sharpe {base['sharpe']:.2f}  "
          f"maxDD {base['maxDD']:.4f}  turn {base['turn']:.0f}")
    print(f"net (donchian_retest)  : ret {retest['ret']:+.4f}  sharpe {retest['sharpe']:.2f}  "
          f"maxDD {retest['maxDD']:.4f}  turn {retest['turn']:.0f}")

    print("\n6-gate validate_candidate(donchian_retest):")
    v = validate_candidate("donchian_retest", gb("5m"), every="5m",
                           n_trials=6,  # 3 max_wait x 2 policy searched
                           exit_n=110, max_wait=BEST_MAX_WAIT,
                           fallback=BEST_FALLBACK)
    for gate, d in v["gates"].items():
        print(f"  {gate:<16} {'PASS' if d['pass'] else 'FAIL'}  {d}")
    print(f"  OVERALL: {'PASS' if v['overall'] else 'FAIL'}")


if __name__ == "__main__":
    main()
