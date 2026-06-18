#!/usr/bin/env python
"""RESEARCH: compare stop-distance modes on donchian (the book the stop affects),
at its portfolio weight so lot sizes match live.

  NONE     : no stop (current pre-8x state)
  FIXED    : stop distance = sl_mult * ATR_entry  (the deployed 8x; constant distance)
  DIVIDED  : stop distance = (sl_mult / num_lots) * ATR_entry  (user's idea: 8xATR
             divided by lot count, 0.01 lot = 1 -> fixed DOLLAR risk per position)

Vol-targeted sizing (lev = min(target_vol/realized_vol, max_lev) * weight) gives a
real per-bar lot count; the chandelier trail mirrors engine._simulate_stops but
takes a per-bar multiplier. PnL is in leverage-fraction units (lev already in pos).

    uv run scripts/research_stop_compare.py
"""
from __future__ import annotations

import numpy as np

from mt5_trader.features.technicals import realized_vol
from mt5_trader.pipeline.data import bars_per_year, build_bars
from mt5_trader.strategies import REGISTRY

EQUITY, CONTRACT, LOT_STEP = 100_000.0, 100.0, 0.01
TARGET_VOL, MAX_LEV, VOL_WIN, WEIGHT = 0.30, 2.0, 240, 0.4
SL_MULT = 8.0
SLIP = 0.5e-4


def chandelier_pnl(pos, close, high, low, atr, spread_frac, sl_mult_arr):
    """Per-bar chandelier stop with a per-bar multiplier array (sl_mult_arr).
    sl_mult_arr <= 0 / inf -> no stop that bar. Returns pnl array (frac units)."""
    n = len(close)
    ret = np.zeros(n)
    extra_turn = np.zeros(n)
    atr_entry = entry = extreme = side = 0.0
    re_anchor = True
    for t in range(1, n):
        s = float(pos[t])
        prev = close[t - 1]
        if s == 0.0:
            side, re_anchor = 0.0, True
            ret[t] = 0.0
            continue
        long = s > 0
        if re_anchor or long != (side > 0):
            atr_entry, entry, extreme = atr[t - 1], prev, prev
            re_anchor = False
        side = s
        extreme = max(extreme, high[t]) if long else min(extreme, low[t])
        m = sl_mult_arr[t]
        valid = atr_entry > 0 and atr_entry == atr_entry and 0 < m < np.inf
        direction = 1.0 if long else -1.0
        sl = extreme - direction * m * atr_entry if valid else None
        if sl is not None and (low[t] <= sl if long else high[t] >= sl):
            ret[t] = sl / prev - 1.0
            extra_turn[t] = 2.0 * abs(s)
            re_anchor = True
        else:
            ret[t] = close[t] / prev - 1.0
    turnover = np.abs(np.diff(pos, prepend=0.0)) + extra_turn
    cost = turnover * (spread_frac / 2 + SLIP)
    return pos * ret - cost, turnover.sum()


def metrics(pnl, turn, byr):
    eq = np.cumprod(1.0 + pnl)
    dd = (eq / np.maximum.accumulate(eq) - 1.0).min()
    sharpe = pnl.mean() / pnl.std() * np.sqrt(byr) if pnl.std() > 0 else 0.0
    return eq[-1] - 1.0, sharpe, dd, turn


def main():
    bars = build_bars("XAUUSD", "5m").sort("ts")
    close = bars["close"].to_numpy()
    high, low = bars["high"].to_numpy(), bars["low"].to_numpy()
    atr_arr = bars.select(
        __import__("mt5_trader.features.technicals", fromlist=["atr"]).atr(60)
    ).to_series().to_numpy()
    spread_frac = bars["spread_mean"].fill_null(0).to_numpy() / np.where(close == 0, 1, close)
    byr = bars_per_year("5m")

    sig = np.asarray(REGISTRY["donchian"]().signal(bars), dtype=float)
    vol = bars.select(realized_vol(VOL_WIN, byr)).to_series().to_numpy()
    lev = np.minimum(TARGET_VOL / np.maximum(vol, 1e-4), MAX_LEV)
    frac = np.nan_to_num(sig * lev) * WEIGHT
    pos = np.concatenate([[0.0], frac[:-1]])             # exec one bar later

    lots = np.abs(pos) * EQUITY / (CONTRACT * np.where(close == 0, 1, close))
    num_lots = lots / LOT_STEP                            # 0.01 lot -> 1 unit
    print(f"donchian@M5 w{WEIGHT}: |pos_frac| median {np.median(np.abs(pos[pos!=0])):.3f}  "
          f"num_lots median {np.median(num_lots[pos!=0]):.1f}  "
          f"(divided sl_mult median {SL_MULT/np.median(num_lots[pos!=0]):.2f}xATR)")

    modes = {
        "NONE": np.zeros(len(close)),
        f"FIXED {SL_MULT:.0f}x": np.full(len(close), SL_MULT),
        f"DIVIDED {SL_MULT:.0f}/n": np.divide(SL_MULT, num_lots,
            out=np.full_like(num_lots, np.inf), where=num_lots > 0),
    }
    print(f"\n{'mode':<14}{'ret':>9}{'sharpe':>8}{'maxDD':>9}{'turn':>8}")
    for name, mult_arr in modes.items():
        pnl, turn = chandelier_pnl(pos, close, high, low, atr_arr, spread_frac, mult_arr)
        r, sh, dd, tn = metrics(pnl, turn, byr)
        print(f"{name:<14}{r:>+9.4f}{sh:>8.2f}{dd:>9.4f}{tn:>8.1f}")
    print("\n~1 month, donchian only, vol-targeted lots. DIVIDED = user's fixed-$ stop.")


if __name__ == "__main__":
    main()
