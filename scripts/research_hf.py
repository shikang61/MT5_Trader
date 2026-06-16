#!/usr/bin/env python
"""Seconds-level strategy feasibility studies on the tick archive.

    uv run scripts/research_hf.py leadlag      # gold -> silver information flow
    uv run scripts/research_hf.py momentum     # short-horizon flow persistence
    uv run scripts/research_hf.py spike        # vol-spike direction persistence
    uv run scripts/research_hf.py triangle     # EURUSD x EURGBP vs GBPUSD consistency
    uv run scripts/research_hf.py all

Each study ends with a PASS/FAIL verdict against the realistic cost hurdle
(full spread round trip + 0.5bp slippage). Event horizons in seconds.
"""
from __future__ import annotations

import argparse

import numpy as np
import polars as pl

from mt5_trader.research import aligned_fwd, cost_bp, dedupe, load_1s, verdict


def study_leadlag(leader="XAUUSD", lagger="XAGUSD") -> None:
    print(f"=== Lead-lag: {leader} -> {lagger} (1s) ===")
    a, b = load_1s(leader), load_1s(lagger)
    df = a.join(b, on="ts", suffix="_b").sort("ts")
    la = np.log(df["mid"].to_numpy())
    lb = np.log(df["mid_b"].to_numpy())
    ra, rb = np.diff(la), np.diff(lb)

    print("corr(leader ret 5s lagged by k, lagger ret 5s):")
    w = 5
    ra5 = np.convolve(ra, np.ones(w), "valid")
    rb5 = np.convolve(rb, np.ones(w), "valid")
    for k in (0, 1, 2, 5, 10, 30):
        n = min(len(ra5) - k, len(rb5))
        c = np.corrcoef(ra5[: n], rb5[k: k + n])[0, 1]
        print(f"  k={k:>2}s: {c:+.4f}")

    # tradeable test: leader 5s move z>3 -> trade lagger for 30s
    sig = ra5
    sd = np.std(sig)
    fwd = np.full(len(sig), np.nan)
    horizon = 30
    lb5 = lb[w - 1:]
    valid = len(lb5) - horizon - 1
    fwd[:valid] = lb5[horizon + 1: valid + horizon + 1] - lb5[1: valid + 1]
    events = dedupe(np.nonzero(np.abs(sig) > 3 * sd)[0], horizon)
    edge, hit, n = aligned_fwd(sig, fwd, events)
    verdict("leadlag", edge, cost_bp(b, spread_col="spread", mid_col="mid"), n, hit)


def study_momentum(symbol="XAUUSD") -> None:
    print(f"=== Tick momentum: {symbol} (30s signal, 60s hold) ===")
    df = load_1s(symbol)
    lm = np.log(df["mid"].to_numpy())
    r = np.diff(lm)

    print("autocorr of k-second returns at lag 1:")
    for k in (1, 5, 10, 30, 60):
        rk = lm[k::k]
        rr = np.diff(rk)
        c = np.corrcoef(rr[:-1], rr[1:])[0, 1]
        print(f"  {k:>2}s returns: {c:+.4f}")

    w, horizon = 30, 60
    sig = np.convolve(r, np.ones(w), "valid")
    sd = np.std(sig)
    lmw = lm[w - 1:]
    fwd = np.full(len(sig), np.nan)
    valid = len(lmw) - horizon - 1
    fwd[:valid] = lmw[horizon + 1: valid + horizon + 1] - lmw[1: valid + 1]
    events = dedupe(np.nonzero(np.abs(sig) > 2.5 * sd)[0], horizon)
    edge, hit, n = aligned_fwd(sig, fwd, events)
    verdict("momentum", edge, cost_bp(df, spread_col="spread", mid_col="mid"), n, hit)


def study_spike(symbol="XAUUSD") -> None:
    print(f"=== Vol spike reaction: {symbol} (5s spike, 30/60/180s follow) ===")
    df = load_1s(symbol)
    lm = np.log(df["mid"].to_numpy())
    r5 = lm[5:] - lm[:-5]
    base = pl.Series(r5).rolling_std(3600).to_numpy()
    z = np.divide(r5, base, out=np.zeros_like(r5), where=(base > 0) & ~np.isnan(base))
    hurdle = cost_bp(df, spread_col="spread", mid_col="mid")
    for horizon in (30, 60, 180):
        fwd = np.full(len(r5), np.nan)
        valid = len(lm) - 5 - horizon - 1
        fwd[:valid] = lm[5 + horizon + 1: valid + 5 + horizon + 1] - lm[5 + 1: valid + 5 + 1]
        events = dedupe(np.nonzero(np.abs(z) > 5)[0], 300)
        edge, hit, n = aligned_fwd(z, fwd, events)
        print(f"  horizon {horizon:>3}s:", end="")
        verdict("spike", edge, hurdle, n, hit)


def study_triangle() -> None:
    print("=== FX triangle: EURUSD / EURGBP vs GBPUSD (1s) ===")
    eu, gu, eg = load_1s("EURUSD"), load_1s("GBPUSD"), load_1s("EURGBP")
    df = (eu.join(gu, on="ts", suffix="_gu").join(eg, on="ts", suffix="_eg")
          .sort("ts"))
    synth = df["mid"].to_numpy() / df["mid_eg"].to_numpy()
    quoted = df["mid_gu"].to_numpy()
    dev_bp = (synth / quoted - 1) * 1e4
    legs_cost = (cost_bp(eu, spread_col="spread", mid_col="mid") + cost_bp(gu, spread_col="spread", mid_col="mid") + cost_bp(eg, spread_col="spread", mid_col="mid"))
    p = np.percentile(np.abs(dev_bp), [50, 95, 99, 99.9])
    print(f"  |deviation| bp: p50 {p[0]:.2f}  p95 {p[1]:.2f}  p99 {p[2]:.2f}  p99.9 {p[3]:.2f}")
    print(f"  3-leg cost: {legs_cost:.2f} bp")
    n_opp = int((np.abs(dev_bp) > legs_cost).sum())
    print(f"  -> {'PASS' if n_opp > 100 else 'FAIL'}: {n_opp} seconds beyond cost "
          f"(real markets ~0; sim may differ — rerun on competition feed)\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("study", choices=["leadlag", "momentum", "spike", "triangle", "all"])
    args = p.parse_args()
    studies = {"leadlag": study_leadlag, "momentum": study_momentum,
               "spike": study_spike, "triangle": study_triangle}
    if args.study == "all":
        for fn in studies.values():
            fn()
    else:
        studies[args.study]()


if __name__ == "__main__":
    main()
