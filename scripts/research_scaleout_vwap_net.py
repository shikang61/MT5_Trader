#!/usr/bin/env python
"""RESEARCH: does scale-out on the vwap_trend book's slice (only) improve the NET portfolio?

Method:
  - For the vwap_trend book, apply scale-out on its NATIVE M5 bars (per-trade
    entry/extreme tracking), then ×lev×weight and join_asof-ffill to the 1m master.
  - All other books: full book_frac_1m (unscaled).
  - net_frac = scaled_vwap + sum(other books).
  - sim_net(..., trigger_T=0.0, close_frac=0.0) — NO additional net-level scale-out.
  - Baseline (T=0): vwap unscaled, reproduces ~+6.01%/9.00/-0.95%.
  - Sweep vwap T ∈ {0,1,2,3,4,5,6} × f ∈ {0.25,0.5,0.75,0.9}.

    uv run scripts/research_scaleout_vwap_net.py
"""
from __future__ import annotations

import numpy as np
import polars as pl
import yaml

from mt5_trader.backtest.metrics import summary
from mt5_trader.features.technicals import atr as atr_expr, realized_vol
from mt5_trader.pipeline.data import bars_per_year, build_bars
from mt5_trader.stops import scaleout_remaining
from mt5_trader.strategies import REGISTRY
from mt5_trader.strategies.meanrev_pairs import spread_bars

SYMBOL = "XAUUSD"
TF_EVERY = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}
SLIP = 0.5e-4
TARGET_VOL, MAX_LEV, VOL_WIN = 0.30, 2.0, 240
SL_ATR_MULT = 8.0
ATR_WIN = 240        # 1m master ATR window (~4h on 1m)
M5_ATR_WIN = 60      # M5 ATR window for per-trade profit tracking

_cache: dict[str, pl.DataFrame] = {}


def gb(every: str) -> pl.DataFrame:
    if every not in _cache:
        _cache[every] = build_bars(SYMBOL, every).sort("ts")
    return _cache[every]


def book_frac_1m(book: dict, master: pl.DataFrame) -> np.ndarray:
    """Verbatim from research_scaleout_net.py."""
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


def vwap_frac_1m_scaled(book: dict, master: pl.DataFrame,
                         trigger_T: float, close_frac: float) -> np.ndarray:
    """Build vwap_trend fraction with scale-out applied on M5 bars before ffill.

    Per-trade tracking:
      - entry / favorable extreme reset on signal sign change or flat.
      - open_profit_atr = abs(extreme - entry) / atr_entry  (M5 ATR, window M5_ATR_WIN)
      - position ×= scaleout_remaining(open_profit_atr, trigger_T, close_frac)
      - THEN ×lev×weight and join_asof-ffill to 1m master (same as book_frac_1m).
    """
    name, tf = book["strategy"], book.get("timeframe", "M5")
    every, w = TF_EVERY[tf], float(book.get("weight", 1.0))
    params = book.get("params", {}) or {}
    bars = gb(every)

    sig = np.asarray(REGISTRY[name](**params).signal(bars), dtype=float)
    vol = bars.select(realized_vol(VOL_WIN, bars_per_year(every))).to_series().to_numpy()
    lev = np.minimum(TARGET_VOL / np.maximum(vol, 1e-4), MAX_LEV)

    # M5 ATR for profit tracking
    m5_atr = bars.select(atr_expr(M5_ATR_WIN)).to_series().to_numpy().astype(float)
    m5_close = bars["close"].to_numpy().astype(float)
    m5_high  = bars["high"].to_numpy().astype(float)
    m5_low   = bars["low"].to_numpy().astype(float)

    n = len(sig)
    scaled_sig = np.zeros(n)

    entry = extreme = atr_entry = 0.0
    prev_sign = 0.0

    for i in range(n):
        s = sig[i]
        if s == 0.0:
            prev_sign = 0.0
            scaled_sig[i] = 0.0
            continue

        cur_sign = 1.0 if s > 0 else -1.0

        # Re-anchor on fresh trade or direction flip
        if cur_sign != prev_sign:
            entry = m5_close[i - 1] if i > 0 else m5_close[i]
            atr_entry = m5_atr[i - 1] if i > 0 else m5_atr[i]
            extreme = entry

        prev_sign = cur_sign

        # Update favorable extreme (intrabar high/low)
        if cur_sign > 0:
            extreme = max(extreme, m5_high[i])
        else:
            extreme = min(extreme, m5_low[i])

        open_profit_atr = (abs(extreme - entry) / atr_entry
                           if atr_entry > 0 else 0.0)
        size_mult = scaleout_remaining(open_profit_atr, trigger_T, close_frac)
        scaled_sig[i] = s * size_mult

    frac = np.nan_to_num(scaled_sig * lev) * w
    sdf = pl.DataFrame({"ts": bars["ts"], "s": frac}).sort("ts")
    joined = master.select("ts").join_asof(sdf, on="ts", strategy="backward")
    return np.nan_to_num(joined["s"].to_numpy())


def sim_net(net_frac: np.ndarray, close: np.ndarray, high: np.ndarray,
            low: np.ndarray, atr_arr: np.ndarray, sp_frac: np.ndarray,
            trigger_T: float, close_frac: float) -> dict:
    """Verbatim from research_scaleout_net.py (no net-level scale-out when T=0/f=0)."""
    n = len(close)
    pnl = np.zeros(n)
    turnover_arr = np.zeros(n)

    atr_entry = entry = extreme = side = 0.0
    re_anchor = True
    prev_eff_pos = 0.0

    for t in range(1, n):
        raw_pos = net_frac[t - 1]
        prev_close = close[t - 1]

        if raw_pos == 0.0:
            side, re_anchor = 0.0, True
            eff_pos = 0.0
            turnover_arr[t] = abs(eff_pos - prev_eff_pos)
            pnl[t] = -turnover_arr[t] * (sp_frac[t] / 2 + SLIP)
            prev_eff_pos = eff_pos
            continue

        long = raw_pos > 0

        if re_anchor or long != (side > 0):
            atr_entry = atr_arr[t - 1]
            entry = prev_close
            extreme = prev_close
            re_anchor = False

        side = raw_pos

        extreme = max(extreme, high[t]) if long else min(extreme, low[t])

        open_profit_atr = (abs(extreme - entry) / atr_entry
                           if atr_entry > 0 else 0.0)
        size_mult = scaleout_remaining(open_profit_atr, trigger_T, close_frac)
        eff_pos = raw_pos * size_mult

        direction = 1.0 if long else -1.0
        sl = None
        exit_px = None
        if SL_ATR_MULT > 0 and atr_entry > 0:
            sl = extreme - direction * SL_ATR_MULT * atr_entry
            if (long and low[t] <= sl) or (not long and high[t] >= sl):
                exit_px = sl

        if exit_px is not None:
            bar_ret = exit_px / prev_close - 1.0
            turnover_arr[t] = abs(eff_pos - prev_eff_pos) + 2.0 * abs(eff_pos)
            pnl[t] = eff_pos * bar_ret - turnover_arr[t] * (sp_frac[t] / 2 + SLIP)
            re_anchor = True
            prev_eff_pos = 0.0
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

    atr_arr = master.select(atr_expr(ATR_WIN)).to_series().to_numpy().astype(float)

    # Split books: vwap_trend vs others
    vwap_book = None
    other_books = []
    for b in cfg["books"]:
        if b["strategy"] == "vwap_trend":
            vwap_book = b
        else:
            other_books.append(b)

    if vwap_book is None:
        raise RuntimeError("vwap_trend book not found in portfolio.yaml")

    # Pre-compute other books (unscaled — same for every T/f sweep)
    other_frac = sum(book_frac_1m(b, master) for b in other_books)

    print(f"1m grid: {master.height} bars  {master['ts'][0]} -> {master['ts'][-1]}")
    print(f"other_frac: min {other_frac.min():+.3f}  max {other_frac.max():+.3f}")
    print()

    Ts = [0, 1, 2, 3, 4, 5, 6]
    Fs = [0.25, 0.5, 0.75, 0.9]

    hdr = f"{'T':>4}{'f':>6}{'return%':>10}{'sharpe':>8}{'maxDD%':>10}{'turnover':>10}"
    print(hdr)
    print("-" * len(hdr))

    for T in Ts:
        if T == 0:
            # Baseline: vwap unscaled (trigger_T=0 means scaleout_remaining returns 1.0)
            vwap_frac = vwap_frac_1m_scaled(vwap_book, master, 0.0, 0.0)
            net_frac = vwap_frac + other_frac
            m = sim_net(net_frac, close, high, low, atr_arr, sp_frac,
                        trigger_T=0.0, close_frac=0.0)
            print(f"{T:>4}{'—':>6}{m['total_return']*100:>+10.3f}"
                  f"{m['sharpe']:>8.2f}{m['max_drawdown']*100:>+10.3f}"
                  f"{m['total_turnover']:>10.0f}")
        else:
            for f in Fs:
                vwap_frac = vwap_frac_1m_scaled(vwap_book, master, float(T), f)
                net_frac = vwap_frac + other_frac
                m = sim_net(net_frac, close, high, low, atr_arr, sp_frac,
                            trigger_T=0.0, close_frac=0.0)
                print(f"{T:>4}{f:>6.2f}{m['total_return']*100:>+10.3f}"
                      f"{m['sharpe']:>8.2f}{m['max_drawdown']*100:>+10.3f}"
                      f"{m['total_turnover']:>10.0f}")

    print()
    print("Relative read only: ~1 month, XAUUSD, one regime.")
    print("T=0 = baseline (vwap unscaled, all books full, disaster stop only).")
    print("Scale-out applied to vwap_trend M5 position only; no net-level scale-out.")


if __name__ == "__main__":
    main()
