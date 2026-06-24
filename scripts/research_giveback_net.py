#!/usr/bin/env python
"""RESEARCH: does a daily trailing kill (give_back) help the NET book?

Sweeps give_back g: once net equity retraces g from the day's PEAK, flatten for the
rest of the UTC day (mirrors RiskManager.kill/roll_day). loss_limit=0.04 (kill from
day-START) is always active, matching live. Relative comparison, one window.

    uv run scripts/research_giveback_net.py
"""
from __future__ import annotations

import numpy as np
import polars as pl
import yaml

from mt5_trader.backtest.metrics import summary
from mt5_trader.features.technicals import atr, realized_vol
from mt5_trader.pipeline.data import bars_per_year, build_bars
from mt5_trader.strategies import REGISTRY
from mt5_trader.strategies.meanrev_pairs import spread_bars

SYMBOL = "XAUUSD"
TF_EVERY = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}
SLIP = 0.5e-4
TARGET_VOL, MAX_LEV, VOL_WIN = 0.30, 2.0, 240
SL_ATR_MULT, ATR_WIN, LOSS_LIMIT = 8.0, 240, 0.04
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


def sim(net_frac, close, high, low, atr_arr, sp_frac, dates, give_back) -> dict:
    """Net bar-by-bar sim: chandelier 8xATR disaster stop + daily kill overlay
    (loss_limit from day-start, give_back from day-peak). Returns metrics.summary."""
    n = len(close)
    pnl = np.zeros(n)
    turnover = np.zeros(n)
    atr_entry = entry = extreme = side = 0.0
    re_anchor = True
    prev_eff = 0.0
    equity = 1.0
    cur_day = None
    day_start = day_peak = 1.0
    killed = False

    for t in range(1, n):
        if dates[t] != cur_day:                 # new UTC day: reset anchors
            cur_day, day_start, day_peak, killed = dates[t], equity, equity, False

        raw = 0.0 if killed else net_frac[t - 1]   # one-bar delay; flat if killed today
        prev_close = close[t - 1]

        if raw == 0.0:
            side, re_anchor = 0.0, True
            eff = 0.0
            turnover[t] = abs(eff - prev_eff)
            pnl[t] = -turnover[t] * (sp_frac[t] / 2 + SLIP)
            prev_eff = eff
        else:
            long = raw > 0
            if re_anchor or long != (side > 0):
                atr_entry, entry, extreme = atr_arr[t - 1], prev_close, prev_close
                re_anchor = False
            side = raw
            extreme = max(extreme, high[t]) if long else min(extreme, low[t])
            eff = raw
            direction = 1.0 if long else -1.0
            exit_px = None
            if SL_ATR_MULT > 0 and atr_entry > 0:
                sl = extreme - direction * SL_ATR_MULT * atr_entry
                if (long and low[t] <= sl) or (not long and high[t] >= sl):
                    exit_px = sl
            if exit_px is not None:
                bar_ret = exit_px / prev_close - 1.0
                turnover[t] = abs(eff - prev_eff) + 2.0 * abs(eff)
                pnl[t] = eff * bar_ret - turnover[t] * (sp_frac[t] / 2 + SLIP)
                re_anchor, prev_eff = True, 0.0
            else:
                bar_ret = close[t] / prev_close - 1.0
                turnover[t] = abs(eff - prev_eff)
                pnl[t] = eff * bar_ret - turnover[t] * (sp_frac[t] / 2 + SLIP)
                prev_eff = eff

        equity *= 1.0 + pnl[t]
        day_peak = max(day_peak, equity)
        if not killed and (equity / day_start - 1.0 <= -LOSS_LIMIT
                           or (give_back > 0 and equity / day_peak - 1.0 <= -give_back)):
            killed = True

    return summary(pnl, np.cumprod(1.0 + pnl), bars_per_year("1m"), turnover)


def main():
    cfg = yaml.safe_load(open("config/portfolio.yaml"))
    master = gb("1m")
    close = master["close"].to_numpy().astype(float)
    high = master["high"].to_numpy().astype(float)
    low = master["low"].to_numpy().astype(float)
    sp_frac = (master["spread_mean"].fill_null(0).to_numpy().astype(float)
               / np.where(close == 0, 1.0, close))
    atr_arr = master.select(atr(ATR_WIN)).to_series().to_numpy().astype(float)
    dates = [d.date() for d in master["ts"].to_list()]
    net_frac = sum(book_frac_1m(b, master) for b in cfg["books"])

    print(f"1m grid: {master.height} bars  {master['ts'][0]} -> {master['ts'][-1]}")
    print(f"{'give_back':>10}{'return%':>10}{'sharpe':>8}{'maxDD%':>10}{'turnover':>10}")
    print("-" * 48)
    for g in [0.0, 0.01, 0.015, 0.02, 0.03, 0.04]:
        m = sim(net_frac, close, high, low, atr_arr, sp_frac, dates, g)
        tag = "0 (base)" if g == 0 else f"{g:.3f}"
        print(f"{tag:>10}{m['total_return']*100:>+10.3f}{m['sharpe']:>8.2f}"
              f"{m['max_drawdown']*100:>+10.3f}{m['total_turnover']:>10.0f}")
    print("\nRelative read only: ~1 month, one regime. give_back=0 -> loss_limit (4%) only.")


if __name__ == "__main__":
    main()
