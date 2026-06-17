#!/usr/bin/env python
"""RESEARCH (not a live component): does flattening into the daily/weekly close
help the existing books? Gold breaks 21:00->22:00 UTC daily; the week ends Fri
21:00 UTC (reopen Sun 22:00).

For each live book we re-run three policies and compare:
  HOLD   : raw signal (carry through the break/weekend) — today's behaviour
  DAILY  : flat into every 21:00 break (signal=0 in hour 21), re-enter at reopen
  WEEKLY : flat only Friday 21:00 into the weekend (signal=0 Fri hour 21)
The engine charges the exit + re-entry turnover, so the cost of flatting is paid.

    uv run scripts/research_flat_close.py
"""
from __future__ import annotations

import numpy as np
import polars as pl
import yaml

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.pipeline.data import bars_per_year, build_bars
from mt5_trader.strategies import REGISTRY
from mt5_trader.strategies.meanrev_pairs import spread_bars

SYMBOL = "XAUUSD"
TF_EVERY = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}
_cache: dict[str, pl.DataFrame] = {}


def get_bars(every: str) -> pl.DataFrame:
    if every not in _cache:
        _cache[every] = build_bars(SYMBOL, every)
    return _cache[every]


def masks(bars: pl.DataFrame) -> dict[str, np.ndarray]:
    """Per-policy keep-mask (1 = strategy signal kept, 0 = forced flat)."""
    h = bars["ts"].dt.hour().to_numpy()
    wd = bars["ts"].dt.weekday().to_numpy()  # Mon=1..Sun=7 (polars)
    return {
        "HOLD": np.ones(len(bars)),
        "DAILY": (h != 21).astype(float),               # flat into every break
        "WEEKLY": ~((wd == 5) & (h == 21)) + 0.0,        # flat only Fri 21:00
    }


def book_bars(name: str, every: str) -> pl.DataFrame:
    if name == "ratio_mr":  # signal off the XAU/XAG spread; only XAU is traded
        return spread_bars(get_bars(every), build_bars("XAGUSD", every), 1.0)
    return get_bars(every)


def run_policies(name: str, every: str, params: dict) -> dict[str, dict]:
    bars = book_bars(name, every)
    sig = np.asarray(REGISTRY[name](**params).signal(bars), dtype=float)
    byr = bars_per_year(every)
    out = {}
    for pol, keep in masks(bars).items():
        res = run(bars, sig * keep, BTConfig(bars_per_year=byr))
        out[pol] = res.metrics
    return out


def main():
    cfg = yaml.safe_load(open("config/portfolio.yaml"))
    print(f"{'book':<26}{'policy':<8}{'ret':>9}{'sharpe':>8}{'maxDD':>9}{'turn':>8}")
    agg = {p: 0.0 for p in ("HOLD", "DAILY", "WEEKLY")}
    for b in cfg["books"]:
        name, tf = b["strategy"], b.get("timeframe", "M1")
        every, w = TF_EVERY[tf], float(b.get("weight", 1.0))
        params = b.get("params", {}) or {}
        pol = run_policies(name, every, params)
        print("-" * 68)
        for p in ("HOLD", "DAILY", "WEEKLY"):
            m = pol[p]
            tag = f"  w={w}" if p == "HOLD" else ""
            print(f"{name+'@'+tf:<26}{p:<8}{m['total_return']:>+9.4f}{m['sharpe']:>8.2f}"
                  f"{m['max_drawdown']:>9.4f}{m['total_turnover']:>8.0f}{tag}")
            agg[p] += w * m["total_return"]
    print("=" * 68)
    print("weighted-sum book return (portfolio proxy; live vol-targets the net):")
    for p in ("HOLD", "DAILY", "WEEKLY"):
        print(f"   {p:<8}{agg[p]:>+9.4f}")
    print("\nReminder: ~1 month, one regime (gold trended up). Directional read only.")


if __name__ == "__main__":
    main()
