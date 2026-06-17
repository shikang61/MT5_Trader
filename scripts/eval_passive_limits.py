#!/usr/bin/env python
"""Screen: rest passive limit orders at key levels and measure whether they earn
or get adversely selected. No L2 needed. Compares two level sources:

  pdhl          — prior-day high/low (constant per day; zero params).
  consolidation — rolling ceiling/floor of the range, gated to RANGE regime
                  (the "ceiling and floor of a consolidating regime").

Same fill model for both (a resting buy fills the first time ask <= level that
day; sell when bid >= level; fill px = the level) so the comparison is
apples-to-apples — only how the level is computed differs. Then measure forward
MID move at several horizons: positive = level held (you earned the spread+);
negative = it broke (adverse selection).

Optimism caveats (this is a screen, not validation): assumes front-of-queue
fill on touch, no partials, ignores non-fills. The forward-drift SIGN is honest
though — negative drift kills the idea regardless of fill optimism. Real fill
rate needs L2 or the paper run (scripts/run_passive_paper.py).

    uv run scripts/eval_passive_limits.py
    uv run scripts/eval_passive_limits.py --range-n 24 48
"""
import argparse

import polars as pl

from mt5_trader.data.bars import time_bars
from mt5_trader.data.ingest import load_ticks
from mt5_trader.features.regime import RANGE, TREND_DOWN, TREND_UP, regime_series
from mt5_trader.pipeline.data import DEFAULT_DATA

HORIZONS = (5, 15, 30, 60)  # minutes
_REGIME_NAME = {TREND_UP: "trend_up", RANGE: "range", TREND_DOWN: "trend_down"}


def load_tick_frame() -> pl.DataFrame:
    return (load_ticks(DEFAULT_DATA, "XAUUSD").sort("ts")
            .with_columns(mid=(pl.col("bid") + pl.col("ask")) / 2,
                          date=pl.col("ts").dt.date(),
                          spread_bp=(pl.col("ask") - pl.col("bid"))
                          / ((pl.col("bid") + pl.col("ask")) / 2) * 1e4))


def m5_levels(range_n: int, coarsen: int = 15) -> pl.DataFrame:
    """Per-bar consolidation floor/ceiling (prior rolling min/max of close) and
    regime, on M5 bars. shift(1) keeps levels causal; regime_series is causal.
    coarsen sets the regime gate window: 16*coarsen*5min (15 = ~20h, 3 = ~4h)."""
    bars = time_bars(load_ticks(DEFAULT_DATA, "XAUUSD"), "5m")
    reg = regime_series(bars, coarsen=coarsen)
    return bars.with_columns(
        floor=pl.col("close").rolling_min(range_n).shift(1),
        ceiling=pl.col("close").rolling_max(range_n).shift(1),
        regime=pl.Series(reg),
    ).select("ts", "floor", "ceiling", "regime", d=pl.col("ts").dt.date())


def first_per_day(cand: pl.DataFrame, px_col: str) -> pl.DataFrame:
    return (cand.sort("ts").group_by("date")
            .agg(fill_ts=pl.col("ts").first(), fill_px=pl.col(px_col).first()))


def pdhl_fills(ticks: pl.DataFrame):
    daily = (ticks.group_by("date").agg(hi=pl.col("mid").max(), lo=pl.col("mid").min())
             .sort("date")
             .with_columns(pdh=pl.col("hi").shift(1), pdl=pl.col("lo").shift(1)))
    t = ticks.join(daily.select("date", "pdh", "pdl"), on="date").drop_nulls(["pdh", "pdl"])
    buys = first_per_day(t.filter(pl.col("ask") <= pl.col("pdl")).rename({"pdl": "lvl"}), "lvl")
    sells = first_per_day(t.filter(pl.col("bid") >= pl.col("pdh")).rename({"pdh": "lvl"}), "lvl")
    return buys, sells


def consolidation_fills(ticks: pl.DataFrame, range_n: int, coarsen: int = 15):
    lv = m5_levels(range_n, coarsen)
    t = (ticks.sort("ts").join_asof(lv.sort("ts"), on="ts", strategy="backward")
         .filter(pl.col("regime") == RANGE).drop_nulls(["floor", "ceiling"]))
    buys = first_per_day(t.filter(pl.col("ask") <= pl.col("floor")).rename({"floor": "lvl"}), "lvl")
    sells = first_per_day(t.filter(pl.col("bid") >= pl.col("ceiling")).rename({"ceiling": "lvl"}), "lvl")
    return buys, sells


def forward(fills: pl.DataFrame, side: str, midser: pl.DataFrame, regimes=None):
    if fills.height == 0:
        print(f"  {side}: no fills")
        return
    label = "support (buy low)" if side == "buy" else "resistance (sell high)"
    print(f"  {side} @ {label}  n={fills.height}:")
    sign = 1 if side == "buy" else -1
    for hz in HORIZONS:
        f = (fills.with_columns(tgt=pl.col("fill_ts") + pl.duration(minutes=hz)).sort("tgt")
             .join_asof(midser, left_on="tgt", right_on="ts", strategy="forward")
             .with_columns(pnl=sign * (pl.col("mid") / pl.col("fill_px") - 1) * 1e4))
        p = f["pnl"].drop_nulls()
        print(f"    +{hz:>3}m: mean {p.mean():+6.2f} bp   median {p.median():+6.2f}   "
              f"win {100*(p > 0).mean():4.1f}%   n={len(p)}")
    if regimes is not None:  # regime split at +15m (the screen's peak horizon)
        f = (fills.with_columns(tgt=pl.col("fill_ts") + pl.duration(minutes=15)).sort("tgt")
             .join_asof(midser, left_on="tgt", right_on="ts", strategy="forward")
             .with_columns(pnl=sign * (pl.col("mid") / pl.col("fill_px") - 1) * 1e4,
                           reg=pl.col("date").replace_strict(regimes, default="?")))
        for r in ("trend_up", "range", "trend_down"):
            g = f.filter(pl.col("reg") == r)["pnl"].drop_nulls()
            if len(g):
                print(f"      {r:11}: mean {g.mean():+6.2f} bp   n={len(g)}")


def daily_regime(range_n: int) -> dict:
    lv = m5_levels(range_n)
    return {d: _REGIME_NAME.get(r, "?")
            for d, r in lv.group_by("d").agg(r=pl.col("regime").last()).iter_rows()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--range-n", type=int, nargs="+", default=[48],
                    help="consolidation window(s) in M5 bars (48 = 4h)")
    ap.add_argument("--regime-coarsen", type=int, nargs="+", default=[15],
                    help="regime gate ER stride(s) on M5 bars; window = 16*stride*5min "
                         "(15 = ~20h, 3 = ~4h). Pass several to compare gate fill counts.")
    args = ap.parse_args()

    ticks = load_tick_frame()
    midser = ticks.select("ts", "mid").sort("ts")
    print(f"{len(ticks):,} ticks  {ticks['date'].min()} .. {ticks['date'].max()}  "
          f"avg spread {ticks['spread_bp'].mean():.2f} bp")

    print("\n========== MODE: pdhl (prior-day high/low) ==========")
    buys, sells = pdhl_fills(ticks)
    regimes = daily_regime(args.range_n[0])
    forward(buys, "buy", midser, regimes)
    forward(sells, "sell", midser, regimes)

    for rn in args.range_n:
        for cz in args.regime_coarsen:
            gate_h = 16 * cz * 5 / 60
            print(f"\n========== MODE: consolidation (band range_n={rn} M5 = {rn*5/60:.1f}h, "
                  f"gate ~{gate_h:.1f}h [coarsen={cz}], RANGE only) ==========")
            buys, sells = consolidation_fills(ticks, rn, cz)
            forward(buys, "buy", midser)
            forward(sells, "sell", midser)


if __name__ == "__main__":
    main()
