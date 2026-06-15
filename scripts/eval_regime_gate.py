#!/usr/bin/env python
"""Test A: does regime-gating the trend books stop the chop-bleed?

The deployed donchian/vwap_trend hold positions regardless of regime, so they
bleed in range (chop) markets. This backtests each one plain vs gated — signal
forced flat whenever the Kaufman efficiency-ratio regime is RANGE — on the same
XAUUSD bars, at a couple of regime-window (coarsen) settings.

    uv run scripts/eval_regime_gate.py
"""
from pathlib import Path

import numpy as np
import polars as pl

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.data.bars import time_bars
from mt5_trader.data.ingest import load_ticks
from mt5_trader.features.microstructure import with_micro_features
from mt5_trader.features.regime import RANGE, regime_series
from mt5_trader.strategies import REGISTRY

DATA = Path("data/processed")


def bars_per_year(every: str) -> float:
    return 365 * 24 * 60 / ({"m": 1, "h": 60}[every[-1]] * float(every[:-1]))


def main():
    every = "5m"
    bars = time_bars(with_micro_features(load_ticks(DATA, "XAUUSD")),
                     every, extra={"ofi": pl.col("ofi").sum()})
    btc = BTConfig(slippage_bps=0.5, bars_per_year=bars_per_year(every))

    def show(tag, m):
        print(f"  {tag:18}: ret {m['total_return']:+.4f}  "
              f"sharpe {m['sharpe']:6.2f}  maxDD {m['max_drawdown']:.4f}")

    for strat in ("donchian", "vwap_trend"):
        sig = REGISTRY[strat]().signal(bars)
        active = sig != 0
        print(f"\n=== {strat}@{every} (active {active.mean()*100:.1f}% of bars) ===")
        show("plain", run(bars, sig, btc).metrics)
        for coarsen in (3, 15):   # 3 = ~4h regime window on M5, 15 = ~20h
            regime = regime_series(bars, coarsen=coarsen)
            gated = np.where(regime == RANGE, 0.0, sig)
            suppressed = (active & (gated == 0)).sum()
            show(f"gated c={coarsen} (-{suppressed} sig-bars)", run(bars, gated, btc).metrics)


if __name__ == "__main__":
    main()
