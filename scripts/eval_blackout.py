#!/usr/bin/env python
"""Measure the economic-calendar blackout overlay: every book backtested with
vs without the event mute, on the same bars. The gate for turning
`blackout.enabled: true` on in config/risk.yaml.

Needs a populated calendar (config/risk.yaml -> blackout.calendar; cols
ts(UTC), impact, currency, title).

    uv run scripts/eval_blackout.py
"""
from pathlib import Path

import numpy as np
import polars as pl
import yaml

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.data.bars import time_bars
from mt5_trader.data.ingest import load_ticks
from mt5_trader.pipeline.data import DEFAULT_DATA
from mt5_trader.features.calendar import BlackoutCfg, blackout_mask, load_events
from mt5_trader.features.microstructure import with_micro_features
from mt5_trader.strategies import REGISTRY

DATA = DEFAULT_DATA
TF = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}


def bars_per_year(every: str) -> float:
    return 365 * 24 * 60 / ({"m": 1, "h": 60}[every[-1]] * float(every[:-1]))


def main():
    risk = yaml.safe_load(open("config/risk.yaml"))
    b = risk.get("blackout") or {}
    cfg = BlackoutCfg(
        enabled=True,                       # force on for the comparison
        calendar=b.get("calendar", "data/calendar/events.parquet"),
        before_min=int(b.get("before_min", 15)),
        after_min=int(b.get("after_min", 15)),
        min_impact=str(b.get("min_impact", "high")),
        currencies=list(b.get("currencies", ["USD"])),
    )
    events = load_events(cfg.calendar)
    if events.is_empty():
        raise SystemExit(f"no events in {cfg.calendar} — populate it first "
                         "(cols: ts, impact, currency, title)")

    print(f"blackout: +/-{cfg.before_min}/{cfg.after_min}m, >= {cfg.min_impact}, "
          f"{cfg.currencies}, {len(events)} events")
    for bk in yaml.safe_load(open("config/portfolio.yaml"))["books"]:
        if bk.get("symbol2"):
            print(f"\n{bk['strategy']}: pair book — skipped")
            continue
        every = TF[bk.get("timeframe", "M1")]
        bars = time_bars(with_micro_features(load_ticks(DATA, bk["symbol"])),
                         every, extra={"ofi": pl.col("ofi").sum()})
        sig = REGISTRY[bk["strategy"]](**bk.get("params", {})).signal(bars)
        mask = blackout_mask(bars["ts"], events, cfg)
        btc = BTConfig(slippage_bps=0.5, bars_per_year=bars_per_year(every))
        off = run(bars, sig, btc).metrics
        mute = run(bars, np.where(mask, 0.0, sig), btc).metrics
        print(f"\n{bk['strategy']}@{bk.get('timeframe')}  "
              f"muted {int(mask.sum())}/{len(mask)} bars")
        for tag, m in (("off", off), ("blackout", mute)):
            print(f"  {tag:9}: ret {m['total_return']:+.4f}  "
                  f"sharpe {m['sharpe']:6.2f}  maxDD {m['max_drawdown']:.4f}")


if __name__ == "__main__":
    main()
