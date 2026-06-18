#!/usr/bin/env python
"""RESEARCH: sweep vol_guard configs to find (if any) a NON-churning fast spike
mute that helps the books on sharp moves. Default (3x ATR / 50bp) churned the
breakout books -> off. Looser configs fire only on genuinely violent bars; does
muting those help (dodge the bad fill/whipsaw) or hurt (miss the breakout)?

Per book at its timeframe: off vs guard, for several (atr_mult, ret_bps) configs.

    uv run scripts/research_vol_guard_sweep.py
"""
from __future__ import annotations

import numpy as np
import yaml

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.features.vol_guard import VolGuardCfg, vol_spike_mask
from mt5_trader.pipeline.data import bars_per_year, build_bars
from mt5_trader.strategies import REGISTRY

TF_EVERY = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}
# (atr_mult, ret_limit_bps): default, then progressively looser (fire less often)
CONFIGS = [(3.0, 50.0), (5.0, 100.0), (8.0, 150.0), (10.0, 250.0)]
_cache: dict = {}


def gb(every: str):
    if every not in _cache:
        _cache[every] = build_bars("XAUUSD", every).sort("ts")
    return _cache[every]


def main():
    for bk in yaml.safe_load(open("config/portfolio.yaml"))["books"]:
        if bk.get("symbol2"):
            continue  # pair book: close-only spread, ATR gate N/A
        name, tf = bk["strategy"], bk.get("timeframe", "M1")
        every = TF_EVERY[tf]
        bars = gb(every)
        sig = REGISTRY[name](**(bk.get("params", {}) or {})).signal(bars)
        btc = BTConfig(slippage_bps=0.5, bars_per_year=bars_per_year(every))
        off = run(bars, sig, btc).metrics
        print(f"\n{name}@{tf}   off: ret {off['total_return']:+.4f} "
              f"sharpe {off['sharpe']:5.2f} maxDD {off['max_drawdown']:.4f}")
        print(f"  {'atr_mult/ret_bp':<16}{'muted%':>8}{'ret':>9}{'sharpe':>8}{'maxDD':>9}")
        for am, rb in CONFIGS:
            cfg = VolGuardCfg(enabled=True, atr_mult=am, ret_limit_bps=rb)
            mask = vol_spike_mask(bars, cfg)
            g = run(bars, np.where(mask, 0.0, sig), btc).metrics
            print(f"  {f'{am:g}x / {rb:g}bp':<16}{mask.mean()*100:>7.2f}%"
                  f"{g['total_return']:>+9.4f}{g['sharpe']:>8.2f}{g['max_drawdown']:>9.4f}")
    print("\nWant: a config that HELPS (or is neutral) ret/DD while muting few bars.")
    print("If every config hurts return, the spike mute churns -> keep it off.")
    print("~1 month, real feed. ratio_mr (pair) skipped.")


if __name__ == "__main__":
    main()
