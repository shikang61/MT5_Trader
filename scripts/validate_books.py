#!/usr/bin/env python
"""Out-of-sample validation for the live books before sizing up (WORKFLOW gate 2).

For each book in config/portfolio.yaml, on XAUUSD at its own timeframe:
  - week-by-week return/Sharpe  (consistent edge, or one lucky week?)
  - cost stress                 (total return at 0.5/1/2/4x slippage)
  - param wiggle +/-25%         (smooth = robust, cliff = overfit)

Metrics come from one full-sample run (no slice-warmup gaps); weekly numbers
aggregate that run's per-bar pnl. Costs/wiggle re-run the full sample.

    uv run scripts/validate_books.py
"""
from __future__ import annotations

import numpy as np
import polars as pl
import yaml

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.backtest.metrics import summary
from mt5_trader.pipeline.data import bars_per_year, build_bars
from mt5_trader.strategies import REGISTRY

SYMBOL = "XAUUSD"
TF_EVERY = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}
# primary param to wiggle (lookbacks +/-25%; session books shift the time knob)
WIGGLE = {
    "donchian": ("entry_n", [180, 240, 300]),
    "vwap_trend": ("z_n", [180, 240, 300]),
    "london_orb": ("flat_hour", [14, 16, 18]),
    "sweep_fade": ("quiet_bars", [45, 60, 75]),
}

_cache: dict[str, pl.DataFrame] = {}


def get_bars(every: str) -> pl.DataFrame:
    if every not in _cache:
        _cache[every] = build_bars(SYMBOL, every)
    return _cache[every]


def bt(name: str, every: str, slippage: float = 0.5, **params):
    bars = get_bars(every)
    sig = REGISTRY[name](**params).signal(bars)
    return run(bars, sig, BTConfig(slippage_bps=slippage,
                                   bars_per_year=bars_per_year(every)))


def main():
    cfg = yaml.safe_load(open("config/portfolio.yaml"))
    for b in cfg["books"]:
        name, tf = b["strategy"], b.get("timeframe", "M1")
        every, byr = TF_EVERY[tf], bars_per_year(TF_EVERY[tf])
        res = bt(name, every)
        m = res.metrics
        print(f"\n{'='*60}\n{name}@{tf}   weight={b.get('weight', 1)}\n{'='*60}")
        print(f"full : ret {m['total_return']:+.4f}  sharpe {m['sharpe']:6.2f}  "
              f"maxDD {m['max_drawdown']:.4f}  turn {m['total_turnover']:.0f}  hit {m['hit_rate']:.2f}")

        wk = res.bars.with_columns(w=pl.col("ts").dt.week())
        print(" week-by-week:")
        for w in sorted(wk["w"].unique().to_list()):
            pnl = wk.filter(pl.col("w") == w)["pnl"].to_numpy()
            sm = summary(pnl, np.cumprod(1.0 + pnl), byr)
            print(f"   wk{w:>2}: ret {sm['total_return']:+.4f}  sharpe {sm['sharpe']:6.2f}")

        print(" cost stress (slippage_bps -> ret):")
        for slip in (0.5, 1.0, 2.0, 4.0):
            r = bt(name, every, slippage=slip).metrics
            print(f"   {slip:>4}: ret {r['total_return']:+.4f}  turn {r['total_turnover']:.0f}")

        pkey, vals = WIGGLE[name]
        print(f" param wiggle ({pkey}):")
        for v in vals:
            r = bt(name, every, **{pkey: v}).metrics
            tag = "  <- deployed" if v == vals[1] else ""
            print(f"   {pkey}={v:>4}: ret {r['total_return']:+.4f}  sharpe {r['sharpe']:6.2f}{tag}")


if __name__ == "__main__":
    main()
