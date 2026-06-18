#!/usr/bin/env python
"""Does the regime gate help donchian ON TOP OF the 8x disaster stop it ships with?

eval_regime_gate showed gating un-stopped donchian halves its maxDD (c=3,
-7.09%->-3.77%, Sharpe -0.63->+0.04). But donchian deploys WITH the 8x
chandelier disaster stop, which already cuts the chop-bleed. Adding the gate is
only worth it if gated+stop beats plain+stop (the deployed baseline). Tests both
at 1x and 4x cost, same tick-bars harness as eval_regime_gate.

    uv run scripts/eval_donchian_regime_stop.py
"""
import numpy as np
import polars as pl

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.data.bars import time_bars
from mt5_trader.data.ingest import load_ticks
from mt5_trader.features.microstructure import with_micro_features
from mt5_trader.features.regime import RANGE, regime_series
from mt5_trader.pipeline.data import DEFAULT_DATA
from mt5_trader.strategies import REGISTRY


def bpy(every: str) -> float:
    return 365 * 24 * 60 / ({"m": 1, "h": 60}[every[-1]] * float(every[:-1]))


def main():
    every = "5m"
    bars = time_bars(with_micro_features(load_ticks(DEFAULT_DATA, "XAUUSD")),
                     every, extra={"ofi": pl.col("ofi").sum()})
    sig = REGISTRY["donchian"]().signal(bars)
    gated = np.where(regime_series(bars, coarsen=3) == RANGE, 0.0, sig)

    def show(tag, sg, slip):
        m = run(bars, sg, BTConfig(slippage_bps=slip, bars_per_year=bpy(every),
                                   sl_atr_mult=8.0, atr_window=60)).metrics
        print(f"  {tag:24}: ret {m['total_return']:+.4f}  sharpe {m['sharpe']:6.2f}  "
              f"maxDD {m['max_drawdown']:.4f}  turn {m['total_turnover']:.0f}")

    print("=== donchian @5m WITH the 8x disaster stop ===")
    print("-- 1x cost (0.5bp) --")
    show("plain+stop (deployed)", sig, 0.5)
    show("gated c=3 + stop", gated, 0.5)
    print("-- 4x cost (2bp) --")
    show("plain+stop", sig, 2.0)
    show("gated c=3 + stop", gated, 2.0)
    print("\nDeploy the gate only if gated+stop has smaller maxDD AND >= ret & Sharpe.")


if __name__ == "__main__":
    main()
