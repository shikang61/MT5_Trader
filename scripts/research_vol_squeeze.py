#!/usr/bin/env python
"""Research: Volatility Compression Breakout on XAUUSD M5.

Two conditions must both be true to declare a "squeeze":
  1. BB width percentile < pct_thresh  (price range compressed vs recent history)
  2. ATR_short / ATR_long  < atr_ratio (short-term vol below long-run baseline)

Then we test:
  A. Directional breakout: enter on the first bar that CLOSES outside the BB
     after a squeeze, hold hold_bars, exit flat.  Signal = +1 (long) / -1 (short).
  B. Overlap: what fraction of squeeze-entry bars coincide with Donchian /
     sweep_fade being in a position?

Run:
    python scripts/research_vol_squeeze.py
    python scripts/research_vol_squeeze.py --bb-n 20 --pct-thresh 15 --atr-ratio 0.75
"""
from __future__ import annotations

import argparse

import numpy as np
import polars as pl

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.backtest.metrics import summary
from mt5_trader.backtest.segment import segmented_signal
from mt5_trader.features.technicals import atr
from mt5_trader.pipeline.data import DEFAULT_DATA, bars_per_year, cached_bars
from mt5_trader.strategies.momentum_breakout import DonchianBreakout
from mt5_trader.strategies.sweep_fade import SweepFade

SYMBOL = "XAUUSD"
EVERY  = "5m"


# ── feature computation ───────────────────────────────────────────────────────

def bb_width(bars: pl.DataFrame, n: int = 20, k: float = 2.0) -> pl.Expr:
    """Bollinger Band width = (upper - lower) / mid, as a fraction."""
    c = pl.col("close")
    mid = c.rolling_mean(n)
    std = c.rolling_std(n)
    return (2 * k * std) / mid


def squeeze_mask(bars: pl.DataFrame, bb_n: int, pct_n: int, pct_thresh: float,
                 atr_short: int, atr_long: int, atr_ratio: float) -> np.ndarray:
    """Boolean array: True when BOTH conditions hold (the squeeze is active)."""
    df = bars.with_columns(
        bbw=bb_width(bars, bb_n),
        atr_s=atr(atr_short),
        atr_l=atr(atr_long),
    ).with_columns(
        bbw_pct=pl.col("bbw").rolling_quantile(pct_thresh / 100.0, window_size=pct_n),
        atr_ratio_val=pl.col("atr_s") / pl.col("atr_l"),
    )
    bbw   = df["bbw"].to_numpy()
    bbw_p = df["bbw_pct"].to_numpy()
    ratio = df["atr_ratio_val"].to_numpy()

    # condition 1: current BB width <= its own pct_thresh-th percentile
    cond1 = bbw <= bbw_p
    # condition 2: ATR ratio below threshold
    cond2 = ratio < atr_ratio
    return cond1 & cond2


def breakout_signal(bars: pl.DataFrame, bb_n: int, squeeze: np.ndarray,
                    hold_bars: int) -> np.ndarray:
    """Enter on first bar that closes OUTSIDE the BB after a squeeze.
    Direction = +1 if close > upper band, -1 if close < lower band.
    Hold hold_bars then exit flat. No re-entry while already holding."""
    df = bars.with_columns(
        mid=pl.col("close").rolling_mean(bb_n),
        std=pl.col("close").rolling_std(bb_n),
    ).with_columns(
        upper=pl.col("mid") + 2 * pl.col("std"),
        lower=pl.col("mid") - 2 * pl.col("std"),
    )
    close = bars["close"].to_numpy()
    upper = df["upper"].to_numpy()
    lower = df["lower"].to_numpy()

    pos   = np.zeros(len(close))
    state = 0.0
    hold  = 0
    armed = False   # True during a squeeze (waiting for the breakout bar)

    for t in range(len(close)):
        if hold > 0:
            pos[t] = state
            hold -= 1
            if hold == 0:
                state = 0.0
            continue

        if squeeze[t]:
            armed = True

        if armed and state == 0.0 and not np.isnan(upper[t]):
            if close[t] > upper[t]:
                state, hold, armed = 1.0, hold_bars, False
            elif close[t] < lower[t]:
                state, hold, armed = -1.0, hold_bars, False

        pos[t] = state

    return pos


# ── overlap analysis ──────────────────────────────────────────────────────────

def overlap_pct(sig_a: np.ndarray, sig_b: np.ndarray) -> float:
    """Fraction of bars where both signals are non-zero and same direction."""
    active_a = sig_a != 0
    active_b = sig_b != 0
    both = active_a & active_b
    if both.sum() == 0:
        return 0.0
    same_dir = np.sign(sig_a[both]) == np.sign(sig_b[both])
    return float(same_dir.mean())


def print_section(title: str) -> None:
    print(f"\n{'='*70}\n{title}\n{'='*70}")


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bb-n",       type=int,   default=20,
                    help="BB rolling window (bars, default 20 = 100min on M5)")
    ap.add_argument("--pct-n",      type=int,   default=288,
                    help="percentile lookback window (default 288 = ~1 trading day on M5)")
    ap.add_argument("--pct-thresh", type=float, default=10.0,
                    help="BB width below this percentile = compressed (default 10)")
    ap.add_argument("--atr-short",  type=int,   default=5,
                    help="short ATR window (default 5 bars = 25 min on M5)")
    ap.add_argument("--atr-long",   type=int,   default=60,
                    help="long ATR window (default 60 bars = 5h on M5)")
    ap.add_argument("--atr-ratio",  type=float, default=0.80,
                    help="ATR_short/ATR_long threshold (default 0.80)")
    ap.add_argument("--hold-bars",  type=int,   default=24,
                    help="bars to hold after breakout entry (default 24 = 2h on M5)")
    args = ap.parse_args()

    print(f"Loading {SYMBOL} {EVERY} bars …")
    bars = cached_bars(SYMBOL, EVERY, DEFAULT_DATA)
    byr  = bars_per_year(EVERY)
    n    = len(bars)
    print(f"  {n:,} bars  [{bars['ts'][0].date()}  →  {bars['ts'][-1].date()}]\n")

    # ── squeeze detection ─────────────────────────────────────────────────────
    squeeze = squeeze_mask(bars,
                           bb_n=args.bb_n, pct_n=args.pct_n,
                           pct_thresh=args.pct_thresh,
                           atr_short=args.atr_short, atr_long=args.atr_long,
                           atr_ratio=args.atr_ratio)
    n_squeeze = int(squeeze.sum())
    print_section("A. Squeeze statistics")
    print(f"  Parameters : BB({args.bb_n}) width < p{args.pct_thresh:.0f} over {args.pct_n} bars  "
          f"AND  ATR({args.atr_short})/ATR({args.atr_long}) < {args.atr_ratio}")
    print(f"  Squeeze bars: {n_squeeze:,} / {n:,}  ({100*n_squeeze/n:.1f}% of time)")

    # ── forward return magnitude after squeeze ────────────────────────────────
    print_section("B. Volatility expansion after squeeze (raw, no direction)")
    close = bars["close"].to_numpy()
    for fwd in (6, 12, 24, 48):
        r_all = np.abs(np.log(close[fwd:] / close[:-fwd]))
        r_sq  = r_all[squeeze[:len(r_all)]]
        r_no  = r_all[~squeeze[:len(r_all)]]
        print(f"  {fwd:>2}bar fwd |ret|  squeeze={r_sq.mean()*1e4:.1f}bp  "
              f"non-squeeze={r_no.mean()*1e4:.1f}bp  "
              f"ratio={r_sq.mean()/r_no.mean():.2f}x")

    # ── directional breakout backtest ─────────────────────────────────────────
    print_section(f"C. Directional breakout (enter on close outside BB, hold {args.hold_bars} bars)")
    sig_squeeze = breakout_signal(bars, args.bb_n, squeeze, args.hold_bars)
    result = run(bars, sig_squeeze,
                 BTConfig(slippage_bps=0.5, bars_per_year=byr,
                          sl_atr_mult=0.0))
    m = result.metrics
    byr_c = byr  # bars_per_year already correct
    n_trades = int((np.diff(sig_squeeze) != 0).sum() // 2) if sig_squeeze.any() else 0
    print(f"  Total return : {m['total_return']:+.4f}  ({m['total_return']*100:.2f}%)")
    print(f"  Sharpe       : {m['sharpe']:.2f}")
    print(f"  Max DD       : {m['max_drawdown']:.4f}  ({m['max_drawdown']*100:.2f}%)")
    print(f"  Hit rate     : {m['hit_rate']:.2f}")
    print(f"  Active bars  : {m['active_bars']:,} / {n:,}  ({100*m['active_bars']/n:.1f}%)")

    # ── cost stress ───────────────────────────────────────────────────────────
    print(f"\n  Cost stress (return at different slippage):")
    for slip, label in [(0.5, "1x (0.5bp)"), (1.0, "2x (1.0bp)"), (2.0, "4x (2.0bp)")]:
        r = run(bars, sig_squeeze,
                BTConfig(slippage_bps=slip, bars_per_year=byr)).metrics
        print(f"    {label:14}: ret {r['total_return']:+.4f}  sharpe {r['sharpe']:.2f}")

    # ── overlap with existing strategies ─────────────────────────────────────
    print_section("D. Overlap with existing portfolio strategies")
    sig_don   = segmented_signal(bars, DonchianBreakout())
    sig_sweep = segmented_signal(bars, SweepFade())

    sq_active = sig_squeeze != 0
    don_active = sig_don != 0
    sw_active  = sig_sweep != 0

    def pct(a, b):
        both = a & b
        if both.sum() == 0:
            return 0.0, 0.0
        frac_of_a = both.sum() / a.sum() * 100 if a.sum() else 0
        frac_of_b = both.sum() / b.sum() * 100 if b.sum() else 0
        return frac_of_a, frac_of_b

    sq_don_a, sq_don_b = pct(sq_active, don_active)
    sq_sw_a,  sq_sw_b  = pct(sq_active, sw_active)

    print(f"  Squeeze active          : {sq_active.sum():>5} bars  ({100*sq_active.mean():.1f}%)")
    print(f"  Donchian active         : {don_active.sum():>5} bars  ({100*don_active.mean():.1f}%)")
    print(f"  SweepFade active        : {sw_active.sum():>5} bars  ({100*sw_active.mean():.1f}%)\n")
    print(f"  Squeeze ∩ Donchian      : {sq_don_a:.1f}% of squeeze bars are also in Donchian")
    print(f"                          : {sq_don_b:.1f}% of Donchian bars are also in squeeze")
    print(f"  Squeeze ∩ SweepFade     : {sq_sw_a:.1f}% of squeeze bars are also in SweepFade")
    print(f"                          : {sq_sw_b:.1f}% of SweepFade bars are also in squeeze")

    # ── same-direction agreement ──────────────────────────────────────────────
    print(f"\n  Direction agreement (when both active, same direction?):")
    print(f"    Squeeze vs Donchian : {overlap_pct(sig_squeeze, sig_don)*100:.1f}%")
    print(f"    Squeeze vs SweepFade: {overlap_pct(sig_squeeze, sig_sweep)*100:.1f}%")

    # ── does squeeze improve donchian entry timing? ───────────────────────────
    print_section("E. Does squeeze filter improve Donchian? (only trade when squeeze=True at entry)")
    don_entries = np.zeros(n)
    in_pos = False
    for t in range(1, n):
        if sig_don[t] != 0 and sig_don[t-1] == 0:
            in_pos = True
        if sig_don[t] == 0:
            in_pos = False
        if in_pos and squeeze[t]:
            don_entries[t] = sig_don[t]
    result_filtered = run(bars, don_entries,
                          BTConfig(slippage_bps=0.5, bars_per_year=byr,
                                   sl_atr_mult=8.0, atr_window=60))
    mf = result_filtered.metrics
    result_plain = run(bars, sig_don,
                       BTConfig(slippage_bps=0.5, bars_per_year=byr,
                                sl_atr_mult=8.0, atr_window=60))
    mp = result_plain.metrics
    print(f"  {'':20}  {'ret':>7}  {'sharpe':>6}  {'maxDD':>7}  {'active':>6}")
    print(f"  {'Donchian plain':20}  {mp['total_return']:>+7.4f}  {mp['sharpe']:>6.2f}  "
          f"{mp['max_drawdown']:>7.4f}  {mp['active_bars']:>6}")
    print(f"  {'Donchian+squeeze':20}  {mf['total_return']:>+7.4f}  {mf['sharpe']:>6.2f}  "
          f"{mf['max_drawdown']:>7.4f}  {mf['active_bars']:>6}")


if __name__ == "__main__":
    main()
