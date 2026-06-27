#!/usr/bin/env python
"""Grid search on DonchianBreakout exit_n, entry_n and ema_n fixed.

Fixed:  timeframe=5m, entry_n=240, ema_n=1440
Grid:   exit_n in [60 (baseline), 80, 90, 100, 110, 120, 130, 140, 150]
Split:  last 20% of bars = OOS (same wall-clock cut as tune_donchian.py)

    python scripts/grid_donchian_exit.py
"""
from __future__ import annotations

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.backtest.segment import segmented_signal
from mt5_trader.pipeline.data import DEFAULT_DATA, bars_per_year, cached_bars
from mt5_trader.strategies.momentum_breakout import DonchianBreakout

SYMBOL    = "XAUUSD"
EVERY     = "5m"
ENTRY_N   = 240
EMA_N     = 1440
SLIP      = 0.5      # 1x cost (deployed slippage)
GRID      = [60, 80, 90, 100, 110, 120, 130, 140, 150]


def bt(bars, exit_n: int) -> dict:
    strategy = DonchianBreakout(entry_n=ENTRY_N, exit_n=exit_n, ema_n=EMA_N)
    sig = segmented_signal(bars, strategy)
    return run(bars, sig, BTConfig(
        slippage_bps=SLIP,
        bars_per_year=bars_per_year(EVERY),
        sl_atr_mult=8.0,
        atr_window=60,
    )).metrics


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--oos-frac", type=float, default=0.20,
                    help="tail fraction held out as OOS (default 0.20)")
    args = ap.parse_args()

    all_bars = cached_bars(SYMBOL, EVERY, DEFAULT_DATA)
    cut = round(len(all_bars) * (1 - args.oos_frac))
    is_bars  = all_bars[:cut]
    oos_bars = all_bars[cut:]
    is_end   = all_bars["ts"][cut - 1]
    oos_end  = all_bars["ts"][-1]

    print(f"Data  : {all_bars['ts'][0].date()} → {oos_end.date()}  ({len(all_bars):,} bars)")
    print(f"IS    : → {is_end.date()}  ({len(is_bars):,} bars)")
    print(f"OOS   : → {oos_end.date()}  ({len(oos_bars):,} bars)")
    print(f"Fixed : entry_n={ENTRY_N}  ema_n={EMA_N}  tf={EVERY}\n")

    W = 80
    print("=" * W)
    print(f"{'exit_n':>6}  {'IS ret':>7}  {'IS Sh':>6}  {'IS DD':>7}  "
          f"{'OOS ret':>7}  {'OOS Sh':>6}  {'OOS DD':>7}  {'note':>10}")
    print("=" * W)

    results = []
    for exit_n in GRID:
        is_m  = bt(is_bars,  exit_n)
        oos_m = bt(oos_bars, exit_n)
        tag = "<-- baseline" if exit_n == 60 else ""
        results.append((exit_n, is_m, oos_m))
        print(f"{exit_n:>6}  "
              f"{is_m['total_return']:>+7.4f}  {is_m['sharpe']:>6.2f}  "
              f"{is_m['max_drawdown']:>7.4f}  "
              f"{oos_m['total_return']:>+7.4f}  {oos_m['sharpe']:>6.2f}  "
              f"{oos_m['max_drawdown']:>7.4f}  {tag}")

    # Summary: best IS and best OOS
    best_is  = max(results, key=lambda r: r[1]["sharpe"])
    best_oos = max(results, key=lambda r: r[2]["sharpe"])
    print("=" * W)
    print(f"Best IS  Sharpe: exit_n={best_is[0]}   "
          f"IS Sh={best_is[1]['sharpe']:.2f}  OOS Sh={best_is[2]['sharpe']:.2f}")
    print(f"Best OOS Sharpe: exit_n={best_oos[0]}  "
          f"IS Sh={best_oos[1]['sharpe']:.2f}  OOS Sh={best_oos[2]['sharpe']:.2f}")


if __name__ == "__main__":
    main()
