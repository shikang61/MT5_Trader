#!/usr/bin/env python
"""Liquidity-sweep event studies (M1 bars, tick archive).

    uv run scripts/research_sweep.py all --symbol EURUSD   # or: pdhl | round

A: prior-day high/low sweeps — price takes out yesterday's extreme, then
   either reclaims the level (stop-run reversal, fade) or holds beyond it
   (genuine breakout, follow). Which side has the edge, after costs?
B: round-number sweeps — same event at psychological levels (00/50 grids),
   where FX stop clustering is documented (Osler 2003).

Events are classified `confirm` bars after the sweep: close back inside the
level = "reclaim" (fade entry), close still beyond = "hold" (follow entry).
Entry at the close after the confirm bar (no lookahead); forward returns at
15/30/60 min. Each study prints a kill-zone split (London 7-10 / NY 12-15 UTC)
and a week-by-week split per the research gate in docs/WORKFLOW.md.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import polars as pl

from mt5_trader.data.bars import time_bars
from mt5_trader.data.ingest import load_ticks
from mt5_trader.pipeline.data import DEFAULT_DATA
from mt5_trader.research import cost_bp, fwd_returns, report, sweep_events

DATA = DEFAULT_DATA
ROUND_GRID = {"EURUSD": 0.0050, "GBPUSD": 0.0050, "EURGBP": 0.0050,
              "XAUUSD": 10.0, "XAGUSD": 0.50, "BTCUSD": 500.0}
QUIET_BARS = 60          # bars a level must sit untouched before a cross counts
CONFIRM = 5              # bars after the sweep to classify reclaim vs hold
HORIZONS = (15, 30, 60)


def build(symbol: str) -> pl.DataFrame:
    bars = time_bars(load_ticks(DATA, symbol), "1m")
    return bars.with_columns(
        hour=pl.col("ts").dt.hour(),
        date=pl.col("ts").dt.date(),
        week=pl.col("ts").dt.week(),
    )


def _study(bars: pl.DataFrame, level_sets: list[tuple[str, np.ndarray, int]],
           hurdle: float) -> None:
    """Shared evaluation: classify events, report fade/follow at each horizon,
    then kill-zone and weekly splits on the pooled events."""
    close = bars["close"].to_numpy()
    hours = bars["hour"].to_numpy()
    weeks = bars["week"].to_numpy()

    ev_t, ev_side, ev_reclaim = [], [], []
    for label, levels, side in level_sets:
        idx, reclaim = sweep_events(bars, levels, side)
        ev_t.append(idx)
        ev_side.append(np.full(len(idx), side))
        ev_reclaim.append(reclaim)
        print(f"  [{label}] events={len(idx)} "
              f"reclaimed={reclaim.mean():.0%}" if len(idx) else
              f"  [{label}] events=0")
    t = np.concatenate(ev_t)
    side = np.concatenate(ev_side)
    reclaim = np.concatenate(ev_reclaim)
    if len(t) == 0:
        print("  no events at all")
        return
    entry = t + CONFIRM
    # fade a reclaimed sweep (position against the sweep direction),
    # follow a held sweep (position with it)
    pos = np.where(reclaim, -side, side)

    for horizon in HORIZONS:
        fwd = fwd_returns(close, horizon)
        report(f"reclaim-fade  fwd {horizon:>2}m",
                pos[reclaim] * fwd[entry[reclaim]] * 1e4, hurdle)
        report(f"hold-follow   fwd {horizon:>2}m",
                pos[~reclaim] * fwd[entry[~reclaim]] * 1e4, hurdle)

    fwd = fwd_returns(close, 30)
    edge = pos * fwd[entry] * 1e4
    h = hours[t]
    print("  -- kill-zone split (all events, fwd 30m) --")
    report("London 07-10 UTC", edge[(h >= 7) & (h < 10)], hurdle)
    report("NY     12-15 UTC", edge[(h >= 12) & (h < 15)], hurdle)
    report("other  hours    ", edge[(h < 7) | (h >= 15) & (h < 12)], hurdle)
    print("  -- week-by-week (all events, fwd 30m) --")
    for w in np.unique(weeks[t]):
        report(f"week {w}", edge[weeks[t] == w], hurdle)
    print()


def study_pdhl(symbol: str) -> None:
    print(f"=== A: prior-day high/low sweeps ({symbol}) ===")
    bars = build(symbol)
    daily = (bars.group_by("date").agg(pdh=pl.col("high").max(),
                                       pdl=pl.col("low").min())
             .sort("date").with_columns(pl.col("pdh", "pdl").shift(1)))
    bars = bars.join(daily, on="date", how="left")
    _study(bars, [("PDH", bars["pdh"].to_numpy(), +1),
                  ("PDL", bars["pdl"].to_numpy(), -1)], cost_bp(bars))


def study_round(symbol: str) -> None:
    grid = ROUND_GRID[symbol]
    print(f"=== B: round-number sweeps ({symbol}, grid {grid}) ===")
    bars = build(symbol)
    close = bars["close"].to_numpy()
    # nearest gridline above / below the previous close
    up = np.floor(np.concatenate([[np.nan], close[:-1]]) / grid) * grid + grid
    dn = np.ceil(np.concatenate([[np.nan], close[:-1]]) / grid) * grid - grid
    _study(bars, [("round-up", up, +1), ("round-dn", dn, -1)], cost_bp(bars))


def study_wiggle(symbol: str) -> None:
    """Parameter wiggle on the PDH/PDL reclaim-fade cell: vary the quiet
    window, confirm bars, and horizon. Plateau = edge; lone hot cell = noise
    (the BTC taker-flow lesson). Also prints positive-week count per cell."""
    print(f"=== C: PDH/PDL reclaim-fade wiggle ({symbol}) ===")
    bars = build(symbol)
    daily = (bars.group_by("date").agg(pdh=pl.col("high").max(),
                                       pdl=pl.col("low").min())
             .sort("date").with_columns(pl.col("pdh", "pdl").shift(1)))
    bars = bars.join(daily, on="date", how="left")
    close = bars["close"].to_numpy()
    weeks = bars["week"].to_numpy()
    hurdle = cost_bp(bars)
    horizons = (10, 15, 30)   # 60m already dead in study A
    print(f"  cost hurdle {hurdle:.2f} bp; cells: edge bp / n / positive weeks")
    print(f"  {'':14}" + "".join(f"fwd {h:>3}m{'':10}" for h in horizons))
    for quiet in (30, 60, 90):
        for confirm in (3, 5, 10):
            ev_t, ev_pos = [], []
            for levels, side in ((bars["pdh"].to_numpy(), +1),
                                 (bars["pdl"].to_numpy(), -1)):
                idx, reclaim = sweep_events(bars, levels, side, quiet, confirm)
                ev_t.append(idx[reclaim])
                ev_pos.append(np.full(reclaim.sum(), -side))
            t, pos = np.concatenate(ev_t), np.concatenate(ev_pos)
            cells = []
            for horizon in horizons:
                fwd = fwd_returns(close, horizon)
                edge = pos * fwd[t + confirm] * 1e4
                ok = ~np.isnan(edge)
                wk = weeks[t][ok]
                pos_weeks = sum(edge[ok][wk == w].mean() > 0 for w in np.unique(wk))
                mark = "*" if ok.sum() and np.nanmean(edge) > hurdle else " "
                cells.append(f"{np.nanmean(edge):+6.2f}{mark}/{ok.sum():>3}/{pos_weeks}w   ")
            print(f"  q={quiet:>2} c={confirm:>2}  " + "".join(cells))
    print()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("study", choices=["pdhl", "round", "wiggle", "all"])
    p.add_argument("--symbol", default="EURUSD")
    args = p.parse_args()
    studies = {"pdhl": study_pdhl, "round": study_round, "wiggle": study_wiggle}
    for name, fn in studies.items():
        if args.study in (name, "all"):
            fn(args.symbol)


if __name__ == "__main__":
    main()
