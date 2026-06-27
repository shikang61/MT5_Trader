#!/usr/bin/env python
"""Research: LBMA Gold Fix momentum / drift on XAUUSD M5.

The LBMA Gold Price benchmark is set twice daily in London:
  AM Fix: 10:30 BST  →  09:30 UTC (summer/BST) / 10:30 UTC (winter/GMT)
  PM Fix: 15:00 BST  →  14:00 UTC (summer/BST) / 15:00 UTC (winter/GMT)

Three hypotheses tested:
  1. Pre-fix drift: large institutional orders create directional pressure
     in the 15-30 min before the fix — tradeable in that window.
  2. Post-fix continuation: the fix direction continues after the benchmark
     is set (momentum).
  3. Post-fix reversal: the fix exhausts the pre-fix move and price reverts.

Sections:
  A. Event study  — unconditional mean returns at various horizons
  B. Conditional  — forward returns given the sign of the pre-fix drift
  C. Post-fix drift backtest  — enter at fix, direction = prior N bars
  D. Pre-fix momentum backtest — enter pre_n bars early, exit at fix
  E. Overlap with existing portfolio (Donchian + SweepFade)

Run:
    python scripts/research_lbma_fix.py
    python scripts/research_lbma_fix.py --am-hour 10 --am-min 30  # winter GMT
    python scripts/research_lbma_fix.py --lookback 3 --hold 24
"""
from __future__ import annotations

import argparse

import numpy as np
import polars as pl

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.backtest.segment import segmented_signal
from mt5_trader.pipeline.data import DEFAULT_DATA, bars_per_year, cached_bars
from mt5_trader.strategies.momentum_breakout import DonchianBreakout
from mt5_trader.strategies.sweep_fade import SweepFade

SYMBOL = "XAUUSD"
EVERY  = "5m"


def print_section(title: str) -> None:
    print(f"\n{'='*70}\n{title}\n{'='*70}")


def fix_indices(bars: pl.DataFrame, hour: int, minute: int) -> np.ndarray:
    """Integer indices of bars whose timestamp matches the fix time (UTC)."""
    h = bars["ts"].dt.hour().to_numpy()
    m = bars["ts"].dt.minute().to_numpy()
    return np.where((h == hour) & (m == minute))[0]


# ── A. Unconditional event study ──────────────────────────────────────────────

def event_study(close: np.ndarray, fix_idx: np.ndarray,
                horizons: list[int], label: str) -> None:
    n = len(close)
    print(f"\n  {label}  ({len(fix_idx)} events)")
    print(f"  {'horizon':>10}  {'mean_bp':>8}  {'hit%':>6}  {'t':>5}")
    print(f"  {'-'*36}")
    for h in horizons:
        if h == 0:
            continue
        if h > 0:
            # forward: return from fix close to fix+h close
            fi = fix_idx[fix_idx + h < n]
            rets = np.log(close[fi + h] / close[fi])
        else:
            # backward: return in |h| bars leading up to fix
            fi = fix_idx[fix_idx + h >= 0]
            rets = np.log(close[fi] / close[fi + h])
        mean_bp = rets.mean() * 1e4
        hit = (rets > 0).mean() * 100
        t = rets.mean() / (rets.std() / np.sqrt(len(rets))) if len(rets) > 1 else 0.0
        mins = h * 5
        print(f"  {h:>+5}bar ({mins:>+5}min)  {mean_bp:>+8.2f}  {hit:>5.1f}%  {t:>5.2f}")


# ── B. Conditional forward returns given pre-fix direction ────────────────────

def conditional_study(close: np.ndarray, fix_idx: np.ndarray,
                      lookback: int, label: str) -> None:
    """For each fix event, split by sign of pre-fix return, show fwd return."""
    n = len(close)
    valid = fix_idx[(fix_idx >= lookback) & (fix_idx + 48 < n)]
    pre_ret = np.log(close[valid] / close[valid - lookback])
    up   = valid[pre_ret > 0]
    down = valid[pre_ret < 0]
    print(f"\n  {label}  (lookback={lookback} bars = {lookback*5} min)")
    print(f"  Pre-fix UP ({len(up)}) / DOWN ({len(down)}) / FLAT ({(pre_ret == 0).sum()})")
    print(f"  {'horizon':>10}  {'UP mean_bp':>10}  {'DOWN mean_bp':>11}  {'diff':>6}")
    print(f"  {'-'*44}")
    for h in [3, 6, 12, 24, 48]:
        up_r   = np.log(close[up   + h] / close[up])   if len(up)   else np.array([0.0])
        down_r = np.log(close[down + h] / close[down]) if len(down) else np.array([0.0])
        diff = (up_r.mean() - down_r.mean()) * 1e4
        print(f"  {h:>+5}bar ({h*5:>+4}min)  {up_r.mean()*1e4:>+10.2f}  "
              f"{down_r.mean()*1e4:>+11.2f}  {diff:>+6.2f}")


# ── signal builders ───────────────────────────────────────────────────────────

def post_fix_signal(close: np.ndarray, fix_idx: np.ndarray,
                    lookback: int, hold: int,
                    reversal: bool = False) -> np.ndarray:
    """Enter at each fix bar in direction of prior `lookback` bars, hold `hold` bars.

    reversal=True flips the direction (enter AGAINST the pre-fix move).
    No lookahead: direction computed from data up to and including the fix bar.
    """
    n = len(close)
    pos = np.zeros(n)
    valid = fix_idx[fix_idx >= lookback]
    flip = -1.0 if reversal else 1.0
    for fi in valid:
        ret = np.log(close[fi] / close[fi - lookback])
        direction = flip * float(np.sign(ret))
        if direction == 0.0:
            continue
        end = min(fi + 1 + hold, n)  # enter next bar (fi+1), hold `hold` bars
        pos[fi + 1 : end] = direction
    return pos


def pre_fix_signal(close: np.ndarray, fix_idx: np.ndarray, pre_n: int) -> np.ndarray:
    """Enter pre_n bars before each fix, exit at the fix bar.

    Entry direction = sign of momentum in the pre_n bars leading up to entry.
    (The fix schedule is known in advance, so entering pre_n bars early is
    feasible — it's a calendar-based trade, not a lookahead violation.)
    """
    n = len(close)
    pos = np.zeros(n)
    # entry bar = fix - pre_n; need pre_n earlier bars for momentum
    entry_idx = fix_idx - pre_n
    valid = entry_idx[entry_idx >= pre_n]
    for ei in valid:
        ret = np.log(close[ei] / close[ei - pre_n])
        direction = float(np.sign(ret))
        if direction == 0.0:
            continue
        fi = ei + pre_n  # fix bar
        pos[ei:fi] = direction  # hold from entry to fix (exclusive)
    return pos


# ── engine wrapper ────────────────────────────────────────────────────────────

def bt(bars: pl.DataFrame, sig: np.ndarray, byr: float, sl: float = 0.5) -> dict:
    return run(bars, sig, BTConfig(
        slippage_bps=sl, bars_per_year=byr, sl_atr_mult=0.0
    )).metrics


def n_trades(sig: np.ndarray) -> int:
    return int((np.diff(sig.astype(float)) != 0).sum())


# ── overlap helpers ───────────────────────────────────────────────────────────

def overlap_summary(sig_a: np.ndarray, sig_b: np.ndarray,
                    label_a: str, label_b: str) -> None:
    aa, ab = sig_a != 0, sig_b != 0
    both = aa & ab
    if both.sum() == 0:
        print(f"  {label_a} ∩ {label_b}: 0 bars overlap")
        return
    agree = (np.sign(sig_a[both]) == np.sign(sig_b[both])).mean() * 100
    print(f"  {label_a} ∩ {label_b}: "
          f"{both.sum()} bars  "
          f"({both.sum()/aa.sum()*100:.1f}% of {label_a} bars)  "
          f"same-dir {agree:.1f}%")


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--am-hour",  type=int,   default=9,
                    help="AM fix UTC hour   (default 9  = 10:30 BST)")
    ap.add_argument("--am-min",   type=int,   default=30,
                    help="AM fix UTC minute (default 30)")
    ap.add_argument("--pm-hour",  type=int,   default=14,
                    help="PM fix UTC hour   (default 14 = 15:00 BST)")
    ap.add_argument("--pm-min",   type=int,   default=0,
                    help="PM fix UTC minute (default 0)")
    ap.add_argument("--lookback", type=int,   default=6,
                    help="pre-fix lookback bars for direction (default 6 = 30 min on M5)")
    ap.add_argument("--hold",     type=int,   default=12,
                    help="post-fix hold bars (default 12 = 60 min on M5)")
    ap.add_argument("--pre-n",    type=int,   default=6,
                    help="bars before fix to enter pre-fix trade (default 6 = 30 min)")
    args = ap.parse_args()

    print(f"Loading {SYMBOL} {EVERY} bars …")
    bars = cached_bars(SYMBOL, EVERY, DEFAULT_DATA)
    byr  = bars_per_year(EVERY)
    close = bars["close"].to_numpy()
    n = len(close)
    print(f"  {n:,} bars  [{bars['ts'][0].date()}  →  {bars['ts'][-1].date()}]")
    print(f"  AM Fix: {args.am_hour:02d}:{args.am_min:02d} UTC  |  "
          f"PM Fix: {args.pm_hour:02d}:{args.pm_min:02d} UTC\n")

    am_idx   = fix_indices(bars, args.am_hour, args.am_min)
    pm_idx   = fix_indices(bars, args.pm_hour, args.pm_min)
    both_idx = np.sort(np.concatenate([am_idx, pm_idx]))

    horizons = [-12, -6, -3, 3, 6, 12, 24, 48]

    # ── A. Unconditional event study ──────────────────────────────────────────
    print_section("A. Unconditional event study (mean return at each horizon)")
    event_study(close, am_idx,   horizons, f"AM Fix ({args.am_hour:02d}:{args.am_min:02d} UTC)")
    event_study(close, pm_idx,   horizons, f"PM Fix ({args.pm_hour:02d}:{args.pm_min:02d} UTC)")
    event_study(close, both_idx, horizons, "Both Fixes combined")

    # ── B. Conditional forward returns ────────────────────────────────────────
    print_section("B. Forward returns conditioned on pre-fix direction")
    for lb in [3, 6, 12]:
        conditional_study(close, am_idx,   lb, f"AM Fix")
        conditional_study(close, pm_idx,   lb, f"PM Fix")

    # ── C. Post-fix drift backtests ───────────────────────────────────────────
    print_section("C. Post-fix drift  (enter at fix close, direction = prior lookback bars)")
    fmt = "  {:<16}  lb={:>2}  hold={:>2}  ret {:>+7.4f}  sh {:>6.2f}  DD {:>7.4f}  trades {:>3}"
    print(f"  {'strategy':<16}  {'lb':>4}  {'hold':>6}  {'ret':>10}  {'sharpe':>8}  {'maxDD':>9}  {'#':>4}")
    print(f"  {'-'*72}")
    for label, idx in [("AM Fix", am_idx), ("PM Fix", pm_idx), ("Both", both_idx)]:
        for lb in [3, 6, 12]:
            for hold in [6, 12, 24, 48]:
                sig = post_fix_signal(close, idx, lb, hold)
                m   = bt(bars, sig, byr)
                nt  = n_trades(sig)
                print(fmt.format(label, lb, hold,
                                 m["total_return"], m["sharpe"],
                                 m["max_drawdown"], nt))
        print()

    # ── C2. Post-fix REVERSAL backtests ───────────────────────────────────────
    print_section("C2. Post-fix REVERSAL  (enter at fix OPPOSITE to pre-fix direction)")
    print("  [AM Fix shows strong reversal in Section B; testing here for confirmation]")
    print(f"  {'strategy':<16}  {'lb':>4}  {'hold':>6}  {'ret':>10}  {'sharpe':>8}  {'maxDD':>9}  {'#':>4}")
    print(f"  {'-'*72}")
    for label, idx in [("AM Fix", am_idx), ("PM Fix", pm_idx), ("Both", both_idx)]:
        for lb in [3, 6, 12]:
            for hold in [6, 12, 24, 48]:
                sig = post_fix_signal(close, idx, lb, hold, reversal=True)
                m   = bt(bars, sig, byr)
                nt  = n_trades(sig)
                print(fmt.format(label, lb, hold,
                                 m["total_return"], m["sharpe"],
                                 m["max_drawdown"], nt))
        print()

    # ── D. Pre-fix momentum backtests ─────────────────────────────────────────
    print_section("D. Pre-fix momentum  (enter pre_n bars before fix, exit at fix)")
    print(f"  {'strategy':<16}  {'pre_n':>5}  {'ret':>10}  {'sharpe':>8}  {'maxDD':>9}  {'#':>4}")
    print(f"  {'-'*56}")
    for label, idx in [("AM Fix", am_idx), ("PM Fix", pm_idx), ("Both", both_idx)]:
        for pre_n in [3, 6, 12]:
            sig = pre_fix_signal(close, idx, pre_n)
            m   = bt(bars, sig, byr)
            nt  = n_trades(sig)
            print(f"  {label:<16}  {pre_n:>5}  "
                  f"{m['total_return']:>+10.4f}  {m['sharpe']:>8.2f}  "
                  f"{m['max_drawdown']:>9.4f}  {nt:>4}")
        print()

    # ── E. Cost stress for best signal ────────────────────────────────────────
    print_section("E. Cost stress  (best post-fix configuration on both fixes)")
    best_sig = post_fix_signal(close, both_idx, args.lookback, args.hold)
    print(f"  Signal: lookback={args.lookback} bars, hold={args.hold} bars  "
          f"({n_trades(best_sig)} trades)")
    print(f"  {'cost':>20}  {'ret':>10}  {'sharpe':>8}  {'maxDD':>9}")
    for slip, label in [(0.5, "1x (0.5bp)"), (1.0, "2x (1.0bp)"), (2.0, "4x (2.0bp)")]:
        m = bt(bars, best_sig, byr, sl=slip)
        print(f"  {label:>20}  {m['total_return']:>+10.4f}  {m['sharpe']:>8.2f}  "
              f"{m['max_drawdown']:>9.4f}")

    # ── F. Overlap with existing strategies ───────────────────────────────────
    print_section("F. Overlap with existing portfolio strategies")
    sig_don   = segmented_signal(bars, DonchianBreakout())
    sig_sweep = segmented_signal(bars, SweepFade())

    print(f"  LBMA Fix (post-fix, both):  {(best_sig != 0).sum():>5} bars  "
          f"({100*(best_sig != 0).mean():.1f}%)")
    print(f"  Donchian:                   {(sig_don != 0).sum():>5} bars  "
          f"({100*(sig_don != 0).mean():.1f}%)")
    print(f"  SweepFade:                  {(sig_sweep != 0).sum():>5} bars  "
          f"({100*(sig_sweep != 0).mean():.1f}%)\n")

    overlap_summary(best_sig, sig_don,   "LBMA-Fix", "Donchian")
    overlap_summary(best_sig, sig_sweep, "LBMA-Fix", "SweepFade")


if __name__ == "__main__":
    main()
