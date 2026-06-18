#!/usr/bin/env python
"""RESEARCH (not a live component): does a WIDE disaster stop cap the tail of a
sustained adverse move without bleeding the books?

Motivation: 6/18 the trend books sat short into a +1.8% gold rally (open -$131).
Tight stops are known to bleed these books (whipsaw); the open question is
whether a WIDE chandelier stop (10-20x ATR) caps a runaway while firing rarely
enough not to churn. The engine's stop model is the live runner's chandelier
trail (engine._simulate_stops mirrors runner.chandelier_sl), so this is faithful.

Per book (config/portfolio.yaml), at its timeframe, sweep sl_atr_mult and report
return / Sharpe / maxDD / turnover. Read: does maxDD shrink materially while
return holds? If return craters, a stop bleeds even when wide.

    uv run scripts/research_wide_stop.py
"""
from __future__ import annotations

import yaml

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.pipeline.data import bars_per_year, build_bars
from mt5_trader.strategies import REGISTRY
from mt5_trader.strategies.meanrev_pairs import spread_bars

TF_EVERY = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}
STOPS = [0.0, 10.0, 15.0, 20.0]   # sl_atr_mult; 0 = off (current live)
_cache: dict[str, object] = {}


def gb(every: str):
    if every not in _cache:
        _cache[every] = build_bars("XAUUSD", every).sort("ts")
    return _cache[every]


def book_bars(name: str, every: str):
    if name == "ratio_mr":
        return spread_bars(gb(every), build_bars("XAGUSD", every).sort("ts"), 1.0)
    return gb(every)


def main():
    cfg = yaml.safe_load(open("config/portfolio.yaml"))
    print(f"{'book':<26}{'sl_atr':>7}{'ret':>9}{'sharpe':>8}{'maxDD':>9}{'turn':>8}")
    for b in cfg["books"]:
        name, tf = b["strategy"], b.get("timeframe", "M1")
        if name == "ratio_mr":   # synthetic spread bars carry no high/low for an
            continue             # intrabar stop; ratio_mr already has its own z_stop
        every, w = TF_EVERY[tf], float(b.get("weight", 1.0))
        params = b.get("params", {}) or {}
        bars = book_bars(name, every)
        sig = REGISTRY[name](**params).signal(bars)
        byr = bars_per_year(every)
        print("-" * 67)
        for sl in STOPS:
            m = run(bars, sig, BTConfig(sl_atr_mult=sl, atr_window=60,
                                        bars_per_year=byr)).metrics
            tag = "  <- live (off)" if sl == 0.0 else ""
            print(f"{name+'@'+tf:<26}{sl:>7.0f}{m['total_return']:>+9.4f}"
                  f"{m['sharpe']:>8.2f}{m['max_drawdown']:>9.4f}{m['total_turnover']:>8.0f}{tag}")
    print("\nRead: a wide stop is worth it only if maxDD shrinks materially while")
    print("return ~holds. If return drops with the stop, it bleeds (whipsaw) even wide.")
    print("~1 month, one regime (gold up) — directional read.")


if __name__ == "__main__":
    main()
