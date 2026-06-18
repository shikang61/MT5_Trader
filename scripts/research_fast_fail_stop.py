#!/usr/bin/env python
"""RESEARCH: asymmetric 'fast-fail' disaster stop — cut the whipsaw, ride the move.

Goal (operator): exit fast when a fresh entry goes against us (a false-breakout /
whipsaw), but stay in once a position has proven itself (a real move). Encodes
"quick to cut when wrong, patient when right" WITHOUT the dead uniform-tighter
stop (3x bled donchian +5.1%->-2.2% because normal pullbacks stopped out winners).

Mechanism, layered on the live 8x chandelier disaster stop:
  - A position is PROVEN once its favorable excursion >= prove_k x entry-ATR.
  - While NOT proven, a tight fail_m x entry-ATR adverse move from entry exits NOW
    (the whipsaw signature: an entry that never worked).
  - Once proven, the wide 8x chandelier trails as today (ride the winner).

Mirrors backtest.engine._simulate_stops (same entry/extreme/ATR/re-anchor model)
so the chandelier-only run reproduces the validated 8x number; adds the fast-fail
leg. Per trend book (donchian, vwap_trend) on the real feed. Stop fires first if
both touched in a bar (conservative). Want: lower maxDD WITHOUT losing return.

    uv run scripts/research_fast_fail_stop.py
"""
from __future__ import annotations

import numpy as np
import polars as pl
import yaml

from mt5_trader.backtest.metrics import summary
from mt5_trader.features.technicals import atr
from mt5_trader.pipeline.data import bars_per_year, build_bars
from mt5_trader.strategies import REGISTRY

TF_EVERY = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}
SL_MULT = 8.0          # live chandelier disaster stop
ATR_WINDOW = 60
SLIP = 0.5e-4
# fast-fail grid: (fail_m adverse-ATR to cut an unproven entry, prove_k fav-ATR to "prove")
FF_GRID = [(2.0, 1.0), (3.0, 1.0), (2.0, 2.0), (3.0, 2.0), (1.5, 1.0)]


def sim(pos, close, high, low, atr_arr, spread_frac, byr,
        sl_mult=SL_MULT, fail_m=0.0, prove_k=1.0):
    """One book through the stop model. fail_m=0 -> chandelier-only (baseline)."""
    n = len(close)
    ret = np.zeros(n)
    extra = np.zeros(n)
    atr_e = entry = extreme = side = 0.0
    re_anchor, proven = True, False
    n_fail = n_chand = 0
    for t in range(1, n):
        s = float(pos[t])
        prev = close[t - 1]
        if s == 0.0:
            side, re_anchor = 0.0, True
            continue
        long = s > 0
        if re_anchor or long != (side > 0):          # fresh entry / flip / post-stop
            atr_e, entry, extreme = atr_arr[t - 1], prev, prev
            re_anchor, proven = False, False
        side = s
        extreme = max(extreme, high[t]) if long else min(extreme, low[t])
        valid = atr_e > 0 and atr_e == atr_e
        d = 1.0 if long else -1.0
        if valid:
            fav = (extreme - entry) if long else (entry - extreme)
            if fav >= prove_k * atr_e:
                proven = True
        exit_px = None
        if fail_m > 0 and valid and not proven:       # fast-fail an unproven entry
            ff = entry - d * fail_m * atr_e
            if (low[t] <= ff) if long else (high[t] >= ff):
                exit_px, n_fail = ff, n_fail + 1
        if exit_px is None and sl_mult > 0 and valid:  # wide chandelier
            sl = extreme - d * sl_mult * atr_e
            if (low[t] <= sl) if long else (high[t] >= sl):
                exit_px, n_chand = sl, n_chand + 1
        if exit_px is not None:
            ret[t] = exit_px / prev - 1.0
            extra[t] = 2.0 * abs(s)
            re_anchor = True
        else:
            ret[t] = close[t] / prev - 1.0
    turnover = np.abs(np.diff(pos, prepend=0.0)) + extra
    cost = turnover * (spread_frac / 2 + SLIP)
    pnl = pos * ret - cost
    eq = np.cumprod(1.0 + pnl)
    m = summary(pnl, eq, byr, turnover)
    return m, n_fail, n_chand


def book_arrays(bk: dict):
    name, tf = bk["strategy"], bk.get("timeframe", "M1")
    every = TF_EVERY[tf]
    bars = build_bars("XAUUSD", every).sort("ts")
    sig = np.asarray(REGISTRY[name](**(bk.get("params", {}) or {})).signal(bars), dtype=float)
    pos = np.concatenate([[0.0], sig[:-1]])            # executed one bar later
    close = bars["close"].to_numpy().astype(float)
    high = bars["high"].to_numpy().astype(float)
    low = bars["low"].to_numpy().astype(float)
    atr_arr = bars.select(atr(ATR_WINDOW)).to_series().to_numpy().astype(float)
    sf = (bars["spread_mean"].fill_null(0).to_numpy() / close).astype(float)
    return name, every, pos, close, high, low, atr_arr, sf


def main() -> None:
    books = yaml.safe_load(open("config/portfolio.yaml"))["books"]
    trend = [b for b in books if b["strategy"] in ("donchian", "vwap_trend")]
    for bk in trend:
        name, every, pos, close, high, low, atr_arr, sf = book_arrays(bk)
        byr = bars_per_year(every)
        print(f"\n===== {name}@{bk.get('timeframe')} =====")
        print(f"{'mode':<22}{'ret':>9}{'sharpe':>8}{'maxDD':>9}{'turn':>8}{'fail':>6}{'chand':>6}")

        def row(tag, fail_m=0.0, prove_k=1.0, sl=SL_MULT):
            m, nf, nc = sim(pos, close, high, low, atr_arr, sf, byr, sl, fail_m, prove_k)
            print(f"{tag:<22}{m['total_return']:>+9.4f}{m['sharpe']:>8.2f}"
                  f"{m['max_drawdown']:>9.4f}{m['total_turnover']:>8.0f}{nf:>6}{nc:>6}")

        row("no stop", sl=0.0)
        row("8x chandelier (base)")
        for fm, pk in FF_GRID:
            row(f"+fastfail m={fm:g} k={pk:g}", fail_m=fm, prove_k=pk)

    print("\nfail/chand = # exits by each leg. Want: maxDD up (less negative) w/o losing ret.")
    print("~1 month real feed, donchian-dominated; fast-fail only cuts UNPROVEN entries.")


if __name__ == "__main__":
    main()
