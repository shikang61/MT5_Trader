#!/usr/bin/env python
"""EURUSD->gold lead-lag study (M1, same-source dump).

    uv run scripts/research_leadlag.py sweep        # W x H x theta grid (after cost)
    uv run scripts/research_leadlag.py null         # sign-permutation null test
"""
from __future__ import annotations

import argparse

import numpy as np
import polars as pl

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.data.bars import time_bars
from mt5_trader.data.ingest import load_ticks
from mt5_trader.research import cost_bp
from mt5_trader.strategies.usd_leadlag import UsdLeadLag, attach_eur

DATA = "data/real"   # XAUUSD + EURUSD both here, same source/clock (verified tick-identical)
KILL = {"London": (7, 10), "NY": (12, 15)}


def build_bars(data: str = DATA) -> pl.DataFrame:
    gold = time_bars(load_ticks(data, "XAUUSD"), "1m")    # load_ticks drops the weekend by default
    eur = time_bars(load_ticks(data, "EURUSD"), "1m")
    return attach_eur(gold, eur).drop_nulls("eur_close")


def backtest(strat, bars: pl.DataFrame, slip_bp: float = 0.5):
    return run(bars, strat.signal(bars), BTConfig(slippage_bps=slip_bp))


W_GRID = [1, 2, 3, 5, 8, 13]
H_GRID = [1, 3, 5, 10, 20, 30]
THETA_GRID = [0.0, 0.5, 1.0]      # bp gate

BEST = dict(lead_w=13, hold_h=20, theta_bp=1.0)


def _net_bp(bars: pl.DataFrame, strat, slip_bp: float) -> tuple[float, float, float]:
    res = backtest(strat, bars, slip_bp)
    m = res.metrics
    return (m["total_return"], m["sharpe"], m.get("total_turnover", float("nan")))


def sweep(bars: pl.DataFrame) -> None:
    hurdle = cost_bp(bars, slip_bp=0.5)
    print(f"per-trade cost hurdle ~{hurdle:.3f} bp; grid = after 1x cost (slip 0.5bp)\n")
    print(f"{'W':>3}{'H':>4}{'theta':>7}{'totRet':>9}{'Sharpe':>8}{'turn':>8}")
    best = None
    for w in W_GRID:
        for h in H_GRID:
            for th in THETA_GRID:
                ret, sh, tn = _net_bp(bars, UsdLeadLag(lead_w=w, hold_h=h, theta_bp=th), 0.5)
                print(f"{w:>3}{h:>4}{th:>7.1f}{ret:>9.4f}{sh:>8.2f}{tn:>8.1f}")
                if best is None or (sh == sh and sh > best[0]):
                    best = (sh, w, h, th)
    print(f"\nbest Sharpe: {best}")
    print("\n--- look for a CONTIGUOUS positive-Sharpe region (plateau), not a lone spike ---")
    _session_splits(bars, UsdLeadLag(lead_w=best[1], hold_h=best[2], theta_bp=best[3]))


def _session_splits(bars: pl.DataFrame, strat) -> None:
    sig = strat.signal(bars)
    close = bars["close"].to_numpy()
    ret = np.zeros_like(close); ret[:-1] = np.diff(close) / close[:-1]
    pnl = sig * np.concatenate([ret[1:], [0.0]])     # position t earns t->t+1 return
    hour = bars["ts"].dt.hour().to_numpy()
    week = bars["ts"].dt.week().to_numpy()
    print("\n-- kill-zone net (best params, gross) --")
    for name, (lo, hi) in KILL.items():
        m = (hour >= lo) & (hour < hi)
        print(f"  {name:7} sum={pnl[m].sum()*1e4:>8.1f}bp n={int(m.sum())}")
    print("-- weekly net (gross) --")
    for wk in sorted(set(week.tolist())):
        m = week == wk
        print(f"  week {wk}: {pnl[m].sum()*1e4:>8.1f}bp")


def _tot_return(bars, strat, slip_bp):
    return backtest(strat, bars, slip_bp).metrics["total_return"]


def null_test(bars: pl.DataFrame, n_perm: int = 200, seed: int = 0) -> None:
    strat = UsdLeadLag(**BEST)
    actual = _tot_return(bars, strat, 0.5)
    print(f"actual totRet (1x cost) = {actual:.4f}  params={BEST}")

    rng = np.random.default_rng(seed)
    eur = bars["eur_close"].to_numpy()
    n = len(eur)
    beat = 0
    null = np.empty(n_perm)
    for i in range(n_perm):
        shift = int(rng.integers(n // 10, n - n // 10))    # circular shift breaks the lead
        shuffled = bars.with_columns(eur_close=pl.Series(np.roll(eur, shift)))
        null[i] = _tot_return(shuffled, strat, 0.5)
        beat += null[i] >= actual
    p = (beat + 1) / (n_perm + 1)
    print(f"null mean={null.mean():.4f} std={null.std():.4f}  p(null>=actual)={p:.4f}  "
          f"-> {'PASS' if p < 0.01 else 'FAIL'} (<0.01)")

    print("\n-- cost stress --")
    for k, slip in ((1, 0.5), (4, 2.0)):
        print(f"  {k}x cost (slip {slip}bp): totRet={_tot_return(bars, strat, slip):.4f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sweep", "null"])
    args = ap.parse_args()
    bars = build_bars()
    print(f"bars={bars.height}  span={bars['ts'].min()} -> {bars['ts'].max()}\n")
    if args.cmd == "sweep":
        sweep(bars)
    elif args.cmd == "null":
        null_test(bars)        # Task 5


if __name__ == "__main__":
    main()
