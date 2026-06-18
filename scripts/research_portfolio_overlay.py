#!/usr/bin/env python
"""RESEARCH (not a live component): do a net-exposure cap and a reopen-fade book
COMPLEMENT each other on the live portfolio?

Motivation: 2026-06-17 the trend books stacked short and gave back ~$420 when
gold V-reversed at the 22:00 UTC reopen. Two candidate fixes:
  - CAP  : bound |net directional exposure| so aligned books can't stack
  - FADE : add a reopen-gap-fade book (long the bounce while trends are short)
Both should shrink the reopen-window drawdown; this measures each and together.

Method (approximate, RELATIVE comparison only — not live-accurate):
  - Build each portfolio.yaml book's signal at its timeframe, forward-fill onto a
    common 1m grid, scale by weight. Net exposure = sum of weighted signals.
  - reopen_fade prototype: at each 22:00 reopen, fade the gap (-sign), hold N min.
  - Portfolio return at lev=1 on the net exposure; turnover charged at real spread.
  - Configs: BASELINE / CAP(c) / FADE / BOTH(c). Report ret, Sharpe, maxDD,
    turnover, and P&L booked in the 22:00-23:00 UTC reopen window.

    uv run scripts/research_portfolio_overlay.py
"""
from __future__ import annotations

import numpy as np
import polars as pl
import yaml

from mt5_trader.features.technicals import realized_vol
from mt5_trader.pipeline.data import bars_per_year, build_bars
from mt5_trader.strategies import REGISTRY
from mt5_trader.strategies.meanrev_pairs import spread_bars

SYMBOL = "XAUUSD"
TF_EVERY = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}
SLIP = 0.5e-4            # per-unit-turnover slippage on top of half-spread
REOPEN_HOUR = 22        # this broker's daily break reopen, UTC
TARGET_VOL, MAX_LEV, VOL_WIN = 0.30, 2.0, 240   # risk.yaml posture (live vol-targeting)
_cache: dict[str, pl.DataFrame] = {}


def gb(every: str) -> pl.DataFrame:
    if every not in _cache:
        _cache[every] = build_bars(SYMBOL, every).sort("ts")
    return _cache[every]


def book_frac_1m(book: dict, master: pl.DataFrame) -> np.ndarray:
    """A book's vol-targeted target fraction (leverage multiple, like the live
    runner: frac = signal * min(target_vol/realized_vol, max_lev) * weight),
    forward-filled onto the 1m master grid. Net of these = net_frac = the live
    net target lots * contract * price / equity (the unit the live cap acts in)."""
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


def reopen_fade_1m(master: pl.DataFrame, min_gap_bp: float, hold: int) -> np.ndarray:
    """Prototype reopen-gap-fade on the 1m grid: at each REOPEN_HOUR reopen, if
    |gap| >= min_gap_bp, hold -sign(gap) for `hold` bars (exec lag applied later)."""
    ts = master["ts"]
    o, c = master["open"].to_numpy(), master["close"].to_numpy()
    gap_min = master.select((pl.col("ts").diff().dt.total_seconds() / 60)).to_series().to_numpy()
    hour = ts.dt.hour().to_numpy()
    prev_close = np.concatenate([[np.nan], c[:-1]])
    pos = np.zeros(len(c))
    for i in range(len(c)):
        if gap_min[i] is not None and gap_min[i] > 20 and hour[i] == REOPEN_HOUR and prev_close[i] > 0:
            gap = o[i] / prev_close[i] - 1.0
            if abs(gap) * 1e4 >= min_gap_bp:
                pos[i:i + hold] = -np.sign(gap)
    return pos


def metrics(expo: np.ndarray, ret: np.ndarray, sp_frac: np.ndarray,
            byr: float, reopen_mask: np.ndarray) -> dict:
    pos = np.concatenate([[0.0], expo[:-1]])           # exec one bar later
    turn = np.abs(np.diff(pos, prepend=0.0))
    cost = turn * (sp_frac / 2 + SLIP)
    pnl = pos * ret - cost
    eq = np.cumprod(1.0 + pnl)
    dd = (eq / np.maximum.accumulate(eq) - 1.0).min()
    sharpe = pnl.mean() / pnl.std() * np.sqrt(byr) if pnl.std() > 0 else 0.0
    return {"ret": eq[-1] - 1.0, "sharpe": sharpe, "maxDD": dd,
            "turn": turn.sum(), "reopen_pnl": pnl[reopen_mask].sum()}


def main():
    cfg = yaml.safe_load(open("config/portfolio.yaml"))
    master = gb("1m")
    close = master["close"].to_numpy()
    ret = np.concatenate([[0.0], close[1:] / close[:-1] - 1.0])
    sp_frac = (master["spread_mean"].fill_null(0).to_numpy() / np.where(close == 0, 1, close))
    byr = bars_per_year("1m")
    hour = master["ts"].dt.hour().to_numpy()
    reopen_mask = (hour == REOPEN_HOUR) | (hour == REOPEN_HOUR + 1)  # 22:00-23:59 UTC

    net6 = sum(book_frac_1m(b, master) for b in cfg["books"])  # net target FRACTION (live units)
    fade = reopen_fade_1m(master, min_gap_bp=10.0, hold=20)
    W_FADE = 0.15
    CAPS = (0.5, 0.4, 0.3)  # net-frac (leverage-multiple) units

    print(f"1m grid: {master.height} bars  {master['ts'][0]} -> {master['ts'][-1]}")
    print(f"net_frac exposure: min {net6.min():+.2f} max {net6.max():+.2f}  "
          f"(|net_frac|>{CAPS[0]} on {(np.abs(net6)>CAPS[0]).mean()*100:.0f}% of bars)")
    print(f"reopen_fade active on {(fade!=0).sum()} bars, weight {W_FADE}\n")

    print(f"{'config':<16}{'ret':>9}{'sharpe':>8}{'maxDD':>9}{'turn':>8}{'reopen_pnl':>12}")
    configs = [("BASELINE", net6)]
    for c in CAPS:
        configs.append((f"CAP {c}", np.clip(net6, -c, c)))
    configs.append(("FADE", net6 + W_FADE * fade))
    for c in CAPS:
        configs.append((f"BOTH cap{c}", np.clip(net6 + W_FADE * fade, -c, c)))
    for label, expo in configs:
        m = metrics(expo, ret, sp_frac, byr, reopen_mask)
        print(f"{label:<16}{m['ret']:>+9.4f}{m['sharpe']:>8.2f}{m['maxDD']:>9.4f}"
              f"{m['turn']:>8.0f}{m['reopen_pnl']:>+12.4f}")

    print("\nReminder: normalized-exposure sim (lev=1), RELATIVE read only; ~1 month,")
    print("one regime. Compare configs to each other, not to live $ P&L.")


if __name__ == "__main__":
    main()
