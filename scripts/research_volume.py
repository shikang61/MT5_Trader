#!/usr/bin/env python
"""Volume-based strategy feasibility studies (M1 bars, tick archive).

    uv run scripts/research_volume.py all      # or: breakout_vol | imbalance | vwap

A: do high-activity breakouts continue more than quiet ones?
B: does quoted L1 size imbalance predict returns at 5/30 min?
C: does distance from session VWAP revert?

Volume here = tick count + quoted sizes (no true traded volume in FX/CFDs).
Activity is normalized by hour-of-day median (strong intraday seasonality).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import polars as pl

from mt5_trader.data.bars import time_bars
from mt5_trader.data.ingest import load_ticks
from mt5_trader.pipeline.data import DEFAULT_DATA
from mt5_trader.features.microstructure import with_micro_features
from mt5_trader.research import cost_bp, fwd_returns, report

DATA = DEFAULT_DATA


def build(symbol="XAUUSD") -> pl.DataFrame:
    ticks = with_micro_features(load_ticks(DATA, symbol))
    bars = time_bars(ticks, "1m", extra={
        "ofi": pl.col("ofi").sum(),
        "imb": pl.col("l1_imbalance").mean(),
    })
    # activity normalized by hour-of-day median
    bars = bars.with_columns(hour=pl.col("ts").dt.hour(), date=pl.col("ts").dt.date())
    bars = bars.with_columns(
        rel_activity=pl.col("n_ticks") / pl.col("n_ticks").median().over("hour"))
    # session (daily) VWAP weighted by tick count
    bars = bars.with_columns(
        vwap=(pl.col("close") * pl.col("n_ticks")).cum_sum().over("date")
             / pl.col("n_ticks").cum_sum().over("date"))
    return bars


def study_breakout_vol(symbol="XAUUSD") -> None:
    print(f"=== A: volume-confirmed breakouts ({symbol}) ===")
    bars = build(symbol)
    close = bars["close"].to_numpy()
    hi = bars.select(h=pl.col("close").rolling_max(60).shift(1))["h"].to_numpy()
    lo = bars.select(l=pl.col("close").rolling_min(60).shift(1))["l"].to_numpy()
    act = bars.select(a=pl.col("rel_activity").rolling_mean(5))["a"].to_numpy()
    hurdle = cost_bp(bars)

    up = (close > hi) & ~np.isnan(hi)
    dn = (close < lo) & ~np.isnan(lo)
    sig = np.where(up, 1.0, np.where(dn, -1.0, 0.0))
    # only first bar of each breakout episode
    first = sig != np.concatenate([[0.0], sig[:-1]])
    fwd = fwd_returns(close, 30)
    events = (sig != 0) & first & ~np.isnan(act)

    terciles = np.nanpercentile(act[events], [33, 66])
    for label, mask in [
        ("quiet breakouts (low activity) ", events & (act <= terciles[0])),
        ("normal breakouts               ", events & (act > terciles[0]) & (act <= terciles[1])),
        ("high-activity breakouts        ", events & (act > terciles[1])),
    ]:
        report(label, sig[mask] * fwd[mask] * 1e4, hurdle)
    print()


def study_imbalance(symbol="XAUUSD") -> None:
    print(f"=== B: L1 quote-imbalance prediction ({symbol}) ===")
    bars = build(symbol)
    close = bars["close"].to_numpy()
    imb = bars.select(i=pl.col("imb").rolling_mean(5))["i"].to_numpy()
    hurdle = cost_bp(bars)
    sd = np.nanstd(imb)
    events = np.abs(imb) > 2 * sd
    for horizon in (5, 30):
        fwd = fwd_returns(close, horizon)
        report(f"|imb|>2sd, fwd {horizon:>2}m", np.sign(imb[events]) * fwd[events] * 1e4, hurdle)
    print()


def study_vwap(symbol="XAUUSD") -> None:
    print(f"=== C: session-VWAP reversion ({symbol}) ===")
    bars = build(symbol)
    close = bars["close"].to_numpy()
    vwap = bars["vwap"].to_numpy()
    dist = np.log(close) - np.log(vwap)
    sd = bars.select(s=pl.Series(dist).rolling_std(240))["s"].to_numpy()
    z = np.divide(dist, sd, out=np.zeros_like(dist), where=(sd > 0) & ~np.isnan(sd))
    hurdle = cost_bp(bars)
    events = np.abs(z) > 2
    # exclude first 2h of session (VWAP unstable)
    minute = bars.select(m=pl.col("ts").dt.hour() * 60 + pl.col("ts").dt.minute())["m"].to_numpy()
    events &= minute > 120
    for horizon in (30, 60, 120):
        fwd = fwd_returns(close, horizon)
        report(f"|z|>2, fade, fwd {horizon:>3}m",
                -np.sign(z[events]) * fwd[events] * 1e4, hurdle)
    print()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("study", choices=["breakout_vol", "imbalance", "vwap", "all"])
    p.add_argument("--symbol", default="XAUUSD")
    args = p.parse_args()
    studies = {"breakout_vol": study_breakout_vol, "imbalance": study_imbalance,
               "vwap": study_vwap}
    for name, fn in studies.items():
        if args.study in (name, "all"):
            fn(args.symbol)


if __name__ == "__main__":
    main()
