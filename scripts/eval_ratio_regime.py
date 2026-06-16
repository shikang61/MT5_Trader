#!/usr/bin/env python
"""Diagnostic for ratio_mr (XAU/XAG mean-reversion) and its RANGE gate.

Two questions:
  1. Robustness: is the deployed +ve return real, or an artifact of the gate /
     one good week / cost-fragile / param-fragile?
  2. The claim: does the gate (regime_series default coarsen=15 -> ~20h window
     on M5) tag bars RANGE while the spread is in a SHORT-TERM trend, so the
     book fades into trends?

    uv run scripts/eval_ratio_regime.py
"""
from pathlib import Path

import numpy as np
import polars as pl

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.data.bars import time_bars
from mt5_trader.data.ingest import load_ticks
from mt5_trader.features.regime import RANGE, efficiency_ratio, regime_series
from mt5_trader.strategies.meanrev_pairs import RatioMeanRev, spread_bars

DATA = Path("data/processed")
EVERY = "5m"
BPY = 365 * 24 * 60 / 5            # 5-minute bars per year
Z_N = 288


def trades_from_pos(pos: np.ndarray, pnl: np.ndarray) -> list[float]:
    """Per-trade net pnl = sum of bar pnl over each contiguous same-sign run."""
    out, cur, side = [], 0.0, 0
    for t in range(len(pos)):
        s = int(np.sign(pos[t]))
        if s != side:
            if side != 0:
                out.append(cur)
            cur, side = 0.0, s
        cur += pnl[t]
    if side != 0:
        out.append(cur)
    return out


def line(tag, m, trades):
    wins = sum(1 for t in trades if t > 0)
    wr = f"{100*wins/len(trades):.0f}%" if trades else "-"
    print(f"  {tag:16}: ret {m['total_return']:+.4f}  sharpe {m['sharpe']:6.2f}  "
          f"maxDD {m['max_drawdown']:.4f}  trades {len(trades):3d}  win {wr:>4}  "
          f"turn {m.get('total_turnover',0):6.1f}")


def main():
    xau = time_bars(load_ticks(DATA, "XAUUSD"), EVERY)
    xag = time_bars(load_ticks(DATA, "XAGUSD"), EVERY)
    spread = spread_bars(xau, xag, beta=1.0)
    btc = BTConfig(slippage_bps=0.5, bars_per_year=BPY)
    btc4x = BTConfig(slippage_bps=2.0, bars_per_year=BPY)   # cost stress

    raw_pos = RatioMeanRev(z_n=Z_N, regime_filter=False).signal(spread)

    def gated(coarsen):
        reg = regime_series(spread, coarsen=coarsen)
        return np.where(reg == RANGE, raw_pos, 0.0)

    print(f"=== ratio_mr XAU/XAG @ {EVERY}, z_n={Z_N}  (May 12 - Jun 11) ===")
    print(f"bars: {len(spread)}   raw active: {(raw_pos!=0).mean()*100:.1f}% of bars")
    variants = {
        "raw (no gate)": raw_pos,
        "gate c=15 (DEPLOYED ~20h)": gated(15),
        "gate c=3  (~4h)": gated(3),
        "gate c=1  (per-bar ER)": gated(1),
    }
    for tag, pos in variants.items():
        r = run(spread, pos, btc)
        line(tag, r.metrics, trades_from_pos(pos, r.bars["pnl"].to_numpy()))

    print("\n=== cost stress (slippage 0.5 -> 2.0 bps) ===")
    for tag in ("gate c=15 (DEPLOYED ~20h)", "gate c=3  (~4h)"):
        r = run(spread, variants[tag], btc4x)
        line(tag, r.metrics, trades_from_pos(variants[tag], r.bars["pnl"].to_numpy()))

    print("\n=== week-by-week net return (DEPLOYED c=15) ===")
    r = run(spread, variants["gate c=15 (DEPLOYED ~20h)"], btc)
    wk = (r.bars.with_columns(wk=pl.col("ts").dt.week())
          .group_by("wk").agg(ret=pl.col("pnl").sum(), bars=pl.len()).sort("wk"))
    for row in wk.iter_rows(named=True):
        print(f"  week {row['wk']:2d}: {row['ret']:+.4f}  ({row['bars']} bars)")

    # --- THE CLAIM: short-term trend on bars the gate calls RANGE -----------
    print("\n=== claim: is the spread trending short-term when gate says RANGE? ===")
    reg15 = regime_series(spread, coarsen=15)
    # short-horizon efficiency ratio on the RAW M5 spread (12 bars = 1h, 24 = 2h)
    for k in (12, 24):
        er = spread.select(er=efficiency_ratio(k))["er"].to_numpy()
        rng = reg15 == RANGE
        valid = rng & ~np.isnan(er)
        share_trendy = (er[valid] > 0.40).mean()
        print(f"  ER({k*5}min) on RANGE-tagged bars: median {np.nanmedian(er[valid]):.2f}, "
              f"{share_trendy*100:.0f}% have ER>0.40 (short-term trend)")

    # entries (0 -> +/-1) under c=15: does the spread EXTEND (fade loses) over
    # the next hour, or REVERT (fade wins)?  short pos profits if spread falls.
    pos15 = gated(15)
    close = spread["close"].to_numpy()
    K = 12
    extend = revert = 0
    for t in range(1, len(pos15) - K):
        if pos15[t] != 0 and pos15[t-1] == 0:        # fresh entry bar
            fwd = close[t+K] / close[t] - 1.0
            # need spread to move toward mean = opposite of position sign-pain;
            # short (pos<0) wants fwd<0; long wants fwd>0.
            if np.sign(fwd) == np.sign(pos15[t]):
                revert += 1
            else:
                extend += 1
    tot = extend + revert
    if tot:
        print(f"  entries (c=15): {tot}  ->  {extend} EXTEND into trend "
              f"({100*extend/tot:.0f}%), {revert} revert  [next {K*5}min]")


if __name__ == "__main__":
    main()
