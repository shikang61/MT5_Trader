#!/usr/bin/env python
"""Backtest a registered strategy on ingested tick data.

    uv run scripts/run_backtest.py --strategy donchian --symbol XAUUSD
    uv run scripts/run_backtest.py --strategy ratio_mr --symbol XAUUSD --symbol2 XAGUSD
    uv run scripts/run_backtest.py --strategy ofi --symbol EURUSD --leverage 2
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import polars as pl

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.backtest.segment import segmented_signal
from mt5_trader.pipeline.data import DEFAULT_DATA, bars_per_year, build_bars
from mt5_trader.strategies import REGISTRY
from mt5_trader.strategies.meanrev_pairs import spread_bars


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", type=Path, nargs="+", default=list(DEFAULT_DATA),
                   help="one or more tick source dirs, merged (default: real broker "
                        "feeds data/real+data/ticks; pass data/processed for Dukascopy)")
    p.add_argument("--bars-file", type=Path, help="pre-built bars parquet (skips tick ingestion)")
    p.add_argument("--strategy", required=True, choices=sorted(REGISTRY))
    p.add_argument("--symbol", required=True)
    p.add_argument("--symbol2", help="second leg for pair strategies")
    p.add_argument("--every", default="1m")
    p.add_argument("--beta", type=float, default=1.0)
    p.add_argument("--leverage", type=float, default=1.0)
    p.add_argument("--slippage-bps", type=float, default=0.5)
    p.add_argument("--sl-atr-mult", type=float, default=0.0, help="intrabar trailing stop, x ATR (0=off)")
    p.add_argument("--tp-atr-mult", type=float, default=0.0, help="intrabar take profit, x ATR (0=off)")
    p.add_argument("--atr-window", type=int, default=60)
    p.add_argument("--out", type=Path, default=Path("reports"))
    args = p.parse_args()

    bars = (pl.read_parquet(args.bars_file) if args.bars_file
            else build_bars(args.symbol, args.every, args.data))
    if args.symbol2:
        bars = spread_bars(bars, build_bars(args.symbol2, args.every, args.data), args.beta)

    strategy = REGISTRY[args.strategy]()
    # signal per contiguous segment so a stitched-feed gap (real + live) can't
    # whipsaw a rolling indicator; a no-op on a continuous single feed.
    result = run(bars, segmented_signal(bars, strategy),
                 BTConfig(leverage=args.leverage, slippage_bps=args.slippage_bps,
                          bars_per_year=bars_per_year(args.every),
                          sl_atr_mult=args.sl_atr_mult, tp_atr_mult=args.tp_atr_mult,
                          atr_window=args.atr_window))

    name = f"{args.strategy}_{args.symbol}" + (f"_{args.symbol2}" if args.symbol2 else "")
    print(f"\n=== {name} ===")
    for k, v in result.metrics.items():
        print(f"{k:>16}: {v:,.4f}" if isinstance(v, float) else f"{k:>16}: {v}")

    args.out.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(result.bars["ts"], result.bars["equity"])
    ax.set_title(name)
    fig.savefig(args.out / f"{name}.png", dpi=120, bbox_inches="tight")
    print(f"equity curve -> {args.out / f'{name}.png'}")


if __name__ == "__main__":
    main()
