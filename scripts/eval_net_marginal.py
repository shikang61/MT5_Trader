#!/usr/bin/env python
"""Net-marginal weight test: does setting a book's weight to 0 improve the NET
portfolio's return/Sharpe?  Standing version of the ad-hoc test that killed
london_orb (2026-07-06: "w=0 lifts OOS Sh 2.05->3.35").

Method — reuses research_portfolio_overlay's live-netting model (same reuse as
ablation_lbma_fix): each book's vol-targeted weighted frac on a 1m grid, summed
to net exposure, spread+slip charged on turnover.  Zeroing a book does NOT
renormalize the other weights (precedent: ablation_lbma_fix "B NO_LBMA — its
0.05 goes unused", and the london_orb test).  Books at w=0 are skipped;
--include-zeroed instead probes them the other way (baseline + book at w=0.05).

Marginal verdict per book: POSITIVE = removing it hurts, NEGATIVE = removing it
helps, FLAT = |delta Sharpe| < 0.20.

Default data = the REAL broker feeds (data/real + data/ticks). That stitch has
a multi-day hole (Jun 11-15): signals run per contiguous segment and are forced
flat across it (backtest.segment, same as run_backtest.py) — a no-op on a
continuous feed like data/processed.

    uv run scripts/eval_net_marginal.py
    uv run scripts/eval_net_marginal.py --cost-mult 4 --include-zeroed
    uv run scripts/eval_net_marginal.py --insample 2026-05-12:2026-06-11 \
        --oos 2026-06-16:2026-07-06 --data data/processed

RELATIVE read only: normalized-exposure sim (lev=1); compare configs to each
other, not to live $ P&L.
"""
from __future__ import annotations

import argparse
import functools
import pathlib
import sys
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import research_portfolio_overlay as rpo  # noqa: E402

from mt5_trader.backtest.metrics import sharpe  # noqa: E402
from mt5_trader.backtest.segment import segmented_signal  # noqa: E402
from mt5_trader.pipeline.data import DEFAULT_DATA, bars_per_year, build_bars  # noqa: E402
from mt5_trader.strategies import REGISTRY  # noqa: E402

PROBE_W = 0.05    # probe weight for re-adding a zeroed book (--include-zeroed)
FLAT_SH = 0.20    # |delta Sharpe| below this = FLAT


class _Segmented:
    """Wraps a strategy so .signal() runs per contiguous segment (no rolling
    window spans a stitched-feed hole; flat across it). No-op on one segment."""

    def __init__(self, strat):
        self._strat = strat

    def signal(self, bars):
        return segmented_signal(bars, self._strat)


def net_pnl(expo: np.ndarray, ret: np.ndarray, sp_frac: np.ndarray,
            cost_mult: float) -> np.ndarray:
    """Per-bar net P&L (mirrors research_portfolio_overlay.metrics)."""
    pos = np.concatenate([[0.0], expo[:-1]])           # exec one bar later
    turn = np.abs(np.diff(pos, prepend=0.0))
    return pos * ret - turn * (sp_frac / 2 + rpo.SLIP) * cost_mult


def window_stats(pnl: np.ndarray, mask: np.ndarray, byr: float) -> tuple[float, float]:
    p = pnl[mask]
    return float(np.cumprod(1.0 + p)[-1] - 1.0), sharpe(p, byr)


def verdict(d_sh: float) -> str:
    if d_sh > FLAT_SH:
        return "NEGATIVE (removing helps)"
    if d_sh < -FLAT_SH:
        return "POSITIVE (removing hurts)"
    return "FLAT"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--insample", default="2026-05-12:2026-06-10", help="START:END, inclusive")
    p.add_argument("--oos", default="2026-06-16:2026-07-06", help="START:END inclusive; '' skips")
    p.add_argument("--cost-mult", type=float, default=1.0)
    p.add_argument("--include-zeroed", action="store_true",
                   help=f"also probe w=0 books at +{PROBE_W} instead of skipping them")
    p.add_argument("--data", type=Path, nargs="+", default=list(DEFAULT_DATA),
                   help="tick source dirs (default: real broker feeds data/real+data/ticks; "
                        "pass data/processed for Dukascopy)")
    args = p.parse_args()

    # Route ALL bar builds (incl. ratio_mr's XAG leg) through the chosen dirs,
    # and segment every book's signal at multi-day feed holes.
    rpo.build_bars = functools.partial(build_bars, data_dir=args.data)
    rpo.REGISTRY = {k: (lambda cls=v, **kw: _Segmented(cls(**kw))) for k, v in REGISTRY.items()}

    cfg = yaml.safe_load(open("config/portfolio.yaml", encoding="utf-8"))
    books = cfg["books"]
    master = rpo.gb("1m")
    close = master["close"].to_numpy()
    ret = np.concatenate([[0.0], close[1:] / close[:-1] - 1.0])
    sp_frac = master["spread_mean"].fill_null(0).to_numpy() / np.where(close == 0, 1, close)
    byr = bars_per_year("1m")
    days = master["ts"].dt.date().to_numpy()

    fracs = {id(b): rpo.book_frac_1m(b, master) for b in books}
    net_base = sum(fracs.values())
    pnl_base = net_pnl(net_base, ret, sp_frac, args.cost_mult)

    rows = []  # (book, weight label, pnl WITH book, pnl WITHOUT book)
    for b in books:
        w = float(b.get("weight", 1.0))
        if w > 0:
            pnl_w0 = net_pnl(net_base - fracs[id(b)], ret, sp_frac, args.cost_mult)
            rows.append((b["strategy"], f"{w:.2f}", pnl_base, pnl_w0))
        elif args.include_zeroed:
            probe = rpo.book_frac_1m({**b, "weight": PROBE_W}, master)
            pnl_probe = net_pnl(net_base + probe, ret, sp_frac, args.cost_mult)
            rows.append((b["strategy"], f"0+{PROBE_W}p", pnl_probe, pnl_base))

    print(f"1m grid: {master.height} bars  {master['ts'][0]} -> {master['ts'][-1]}")
    print(f"data: {', '.join(map(str, args.data))}   cost x{args.cost_mult:g}   "
          f"books tested: {len(rows)}")

    final: dict[str, tuple] = {}
    for tag, win in [("IN-SAMPLE", args.insample)] + ([("OOS", args.oos)] if args.oos else []):
        a, z = (np.datetime64(x) for x in win.split(":"))
        mask = (days >= a) & (days <= z)
        if not mask.any():
            print(f"\n== {tag} {win}: NO BARS in window — skipped ==")
            continue
        note = "" if days[mask].max() >= z else f"  (PARTIAL: data ends {days[mask].max()})"
        print(f"\n== {tag} {win}{note} ==")
        print(f"{'book':<12}{'w':>8}{'base_ret':>10}{'base_sh':>9}{'w0_ret':>10}{'w0_sh':>9}"
              f"{'d_ret':>9}{'d_sh':>7}  marginal")
        for name, wstr, pnl_with, pnl_wo in rows:
            r1, s1 = window_stats(pnl_with, mask, byr)
            r0, s0 = window_stats(pnl_wo, mask, byr)
            v = verdict(s0 - s1)
            print(f"{name:<12}{wstr:>8}{r1:>+10.4f}{s1:>9.2f}{r0:>+10.4f}{s0:>9.2f}"
                  f"{r0 - r1:>+9.4f}{s0 - s1:>+7.2f}  {v}")
            final[name] = (tag, v, s0 - s1, r0 - r1)  # last window wins = OOS when run

    print("\n== VERDICTS (decision window = last one run, OOS when available) ==")
    for name, (tag, v, d_sh, d_ret) in final.items():
        print(f"  {name:<12} {v:<26} [{tag}: w=0 d_sh {d_sh:+.2f}, d_ret {d_ret:+.4f}]")
    print("\nNo renormalization when a book is zeroed (its weight goes unused) — "
          "london_orb/lbma_fix precedent. Relative read only.")


if __name__ == "__main__":
    main()
