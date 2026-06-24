#!/usr/bin/env python
"""RESEARCH: does a scale-out overlay on the NET XAUUSD position help the blended book?

Per-book scale-out research showed it helps vwap_trend standalone, but the live books
NET into one XAUUSD position — so the question is whether scale-out on the NET position
(after summing all book fractions) improves the blended book's metrics.

Method (relative comparison only, same sim for all configs):
  - Build net_frac = sum of all portfolio.yaml book fractions on the 1m master grid
    (identical machinery to research_portfolio_overlay.py).
  - Bar-by-bar sim: disaster stop (chandelier 8×ATR) + scale-out overlay on net position.
  - Sweep T ∈ {0,1,2,3,4,5,6} × f ∈ {0.25,0.5,0.75,0.9}; T=0 = baseline (no scale-out).
  - Report: T, f, return%, sharpe, maxDD%, turnover.

    uv run scripts/research_scaleout_net.py
"""
from __future__ import annotations

import numpy as np
import polars as pl
import yaml

from mt5_trader.backtest.metrics import summary
from mt5_trader.features.technicals import realized_vol
from mt5_trader.pipeline.data import bars_per_year, build_bars
from mt5_trader.stops import scaleout_remaining
from mt5_trader.strategies import REGISTRY
from mt5_trader.strategies.meanrev_pairs import spread_bars

SYMBOL = "XAUUSD"
TF_EVERY = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}
SLIP = 0.5e-4
TARGET_VOL, MAX_LEV, VOL_WIN = 0.30, 2.0, 240
SL_ATR_MULT = 8.0      # chandelier disaster stop (mirrors portfolio.yaml)
ATR_WIN = 240          # ~4h on 1m bars

_cache: dict[str, pl.DataFrame] = {}


def gb(every: str) -> pl.DataFrame:
    if every not in _cache:
        _cache[every] = build_bars(SYMBOL, every).sort("ts")
    return _cache[every]


def book_frac_1m(book: dict, master: pl.DataFrame) -> np.ndarray:
    """Verbatim from research_portfolio_overlay.py."""
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


def sim_net(net_frac: np.ndarray, close: np.ndarray, high: np.ndarray,
            low: np.ndarray, atr_arr: np.ndarray, sp_frac: np.ndarray,
            trigger_T: float, close_frac: float) -> dict:
    """Bar-by-bar sim: disaster stop (chandelier SL_ATR_MULT × ATR) + scale-out.

    Position each bar = net_frac[t-1] (one-bar delay).
    Net "trade" = run of constant sign; entry/extreme reset on sign change or flat.
    Scale-out: once abs(extreme-entry)/atr_entry >= trigger_T, multiply remaining
    size by (1-close_frac) for the rest of that net-trade.
    Returns dict compatible with metrics.summary output.
    """
    n = len(close)
    pnl = np.zeros(n)
    turnover_arr = np.zeros(n)

    atr_entry = entry = extreme = side = 0.0
    re_anchor = True
    prev_eff_pos = 0.0

    for t in range(1, n):
        raw_pos = net_frac[t - 1]   # one-bar-delayed signal
        prev_close = close[t - 1]

        if raw_pos == 0.0:
            side, re_anchor = 0.0, True
            eff_pos = 0.0
            turnover_arr[t] = abs(eff_pos - prev_eff_pos)
            pnl[t] = -turnover_arr[t] * (sp_frac[t] / 2 + SLIP)
            prev_eff_pos = eff_pos
            continue

        long = raw_pos > 0

        # Re-anchor on fresh trade or direction flip
        if re_anchor or long != (side > 0):
            atr_entry = atr_arr[t - 1]
            entry = prev_close
            extreme = prev_close
            re_anchor = False

        side = raw_pos

        # Update favorable extreme intrabar
        extreme = max(extreme, high[t]) if long else min(extreme, low[t])

        # Scale-out size multiplier
        open_profit_atr = (abs(extreme - entry) / atr_entry
                           if atr_entry > 0 else 0.0)
        size_mult = scaleout_remaining(open_profit_atr, trigger_T, close_frac)
        eff_pos = raw_pos * size_mult

        # Disaster stop: chandelier at extreme ∓ SL_ATR_MULT * atr_entry
        direction = 1.0 if long else -1.0
        sl = None
        exit_px = None
        if SL_ATR_MULT > 0 and atr_entry > 0:
            sl = extreme - direction * SL_ATR_MULT * atr_entry
            if (long and low[t] <= sl) or (not long and high[t] >= sl):
                exit_px = sl

        if exit_px is not None:
            bar_ret = exit_px / prev_close - 1.0
            # Exit + re-entry round trip in turnover
            turnover_arr[t] = abs(eff_pos - prev_eff_pos) + 2.0 * abs(eff_pos)
            pnl[t] = eff_pos * bar_ret - turnover_arr[t] * (sp_frac[t] / 2 + SLIP)
            re_anchor = True
            prev_eff_pos = 0.0   # re-enter next bar as fresh
        else:
            bar_ret = close[t] / prev_close - 1.0
            turnover_arr[t] = abs(eff_pos - prev_eff_pos)
            pnl[t] = eff_pos * bar_ret - turnover_arr[t] * (sp_frac[t] / 2 + SLIP)
            prev_eff_pos = eff_pos

    equity = np.cumprod(1.0 + pnl)
    return summary(pnl, equity, bars_per_year("1m"), turnover_arr)


def main():
    cfg = yaml.safe_load(open("config/portfolio.yaml"))
    master = gb("1m")
    close = master["close"].to_numpy().astype(float)
    high = master["high"].to_numpy().astype(float)
    low = master["low"].to_numpy().astype(float)
    sp_frac = (master["spread_mean"].fill_null(0).to_numpy().astype(float)
               / np.where(close == 0, 1.0, close))

    # ATR on 1m master close (uses high/low/close, window=ATR_WIN)
    atr_arr = master.select(
        __import__("mt5_trader.features.technicals", fromlist=["atr"]).atr(ATR_WIN)
    ).to_series().to_numpy().astype(float)

    # Build net fraction (all books summed)
    net_frac = sum(book_frac_1m(b, master) for b in cfg["books"])

    print(f"1m grid: {master.height} bars  {master['ts'][0]} -> {master['ts'][-1]}")
    print(f"net_frac: min {net_frac.min():+.3f}  max {net_frac.max():+.3f}")
    print()

    Ts = [0, 1, 2, 3, 4, 5, 6]
    Fs = [0.25, 0.5, 0.75, 0.9]

    hdr = f"{'T':>4}{'f':>6}{'return%':>10}{'sharpe':>8}{'maxDD%':>10}{'turnover':>10}"
    print(hdr)
    print("-" * len(hdr))

    for T in Ts:
        if T == 0:
            # Baseline: no scale-out (f irrelevant — print once)
            m = sim_net(net_frac, close, high, low, atr_arr, sp_frac,
                        trigger_T=0.0, close_frac=0.0)
            print(f"{T:>4}{'—':>6}{m['total_return']*100:>+10.3f}"
                  f"{m['sharpe']:>8.2f}{m['max_drawdown']*100:>+10.3f}"
                  f"{m['total_turnover']:>10.0f}")
        else:
            for f in Fs:
                m = sim_net(net_frac, close, high, low, atr_arr, sp_frac,
                            trigger_T=float(T), close_frac=f)
                print(f"{T:>4}{f:>6.2f}{m['total_return']*100:>+10.3f}"
                      f"{m['sharpe']:>8.2f}{m['max_drawdown']*100:>+10.3f}"
                      f"{m['total_turnover']:>10.0f}")

    print()
    print("Relative read only: ~1 month, XAUUSD, one regime.")
    print("T=0 = baseline net (disaster stop only, no scale-out).")


if __name__ == "__main__":
    main()
