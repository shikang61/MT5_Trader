#!/usr/bin/env python
"""Measure the fast vol circuit breaker: every (non-pair) book backtested with
vs without the spike mute, on the same bars. The gate for turning
`vol_guard.enabled: true` on in config/risk.yaml.

Unlike the calendar blackout, this needs no external data — the mask is pure
price — so it runs on the existing tick dump directly.

    uv run scripts/eval_vol_guard.py
    uv run scripts/eval_vol_guard.py --atr-mult 4 --ret-limit-bps 80
"""
import argparse
from pathlib import Path

import numpy as np
import polars as pl
import yaml

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.data.bars import time_bars
from mt5_trader.data.ingest import load_ticks
from mt5_trader.pipeline.data import DEFAULT_DATA
from mt5_trader.features.microstructure import with_micro_features
from mt5_trader.features.vol_guard import VolGuardCfg, vol_spike_mask
from mt5_trader.strategies import REGISTRY

DATA = DEFAULT_DATA
TF = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}


def bars_per_year(every: str) -> float:
    return 365 * 24 * 60 / ({"m": 1, "h": 60}[every[-1]] * float(every[:-1]))


def main():
    p = argparse.ArgumentParser()
    v = yaml.safe_load(open("config/risk.yaml")).get("vol_guard") or {}
    p.add_argument("--atr-n", type=int, default=v.get("atr_n", VolGuardCfg.atr_n))
    p.add_argument("--atr-base-n", type=int, default=v.get("atr_base_n", VolGuardCfg.atr_base_n))
    p.add_argument("--atr-mult", type=float, default=v.get("atr_mult", VolGuardCfg.atr_mult))
    p.add_argument("--ret-limit-bps", type=float,
                   default=v.get("ret_limit_bps", VolGuardCfg.ret_limit_bps))
    args = p.parse_args()

    cfg = VolGuardCfg(enabled=True, atr_n=args.atr_n, atr_base_n=args.atr_base_n,
                      atr_mult=args.atr_mult, ret_limit_bps=args.ret_limit_bps)
    print(f"vol_guard: atr {cfg.atr_n}/{cfg.atr_base_n} > {cfg.atr_mult}x median, "
          f"|1-bar ret| > {cfg.ret_limit_bps}bp")

    for bk in yaml.safe_load(open("config/portfolio.yaml"))["books"]:
        if bk.get("symbol2"):
            print(f"\n{bk['strategy']}: pair book — skipped (close-only spread)")
            continue
        every = TF[bk.get("timeframe", "M1")]
        bars = time_bars(with_micro_features(load_ticks(DATA, bk["symbol"])),
                         every, extra={"ofi": pl.col("ofi").sum()})
        sig = REGISTRY[bk["strategy"]](**bk.get("params", {})).signal(bars)
        mask = vol_spike_mask(bars, cfg)
        btc = BTConfig(slippage_bps=0.5, bars_per_year=bars_per_year(every))
        off = run(bars, sig, btc).metrics
        guard = run(bars, np.where(mask, 0.0, sig), btc).metrics
        print(f"\n{bk['strategy']}@{bk.get('timeframe')}  "
              f"muted {int(mask.sum())}/{len(mask)} bars ({mask.mean() * 100:.2f}%)")
        for tag, m in (("off", off), ("vol_guard", guard)):
            print(f"  {tag:9}: ret {m['total_return']:+.4f}  "
                  f"sharpe {m['sharpe']:6.2f}  maxDD {m['max_drawdown']:.4f}")


if __name__ == "__main__":
    main()
