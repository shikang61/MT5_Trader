#!/usr/bin/env python
"""ABLATION: does lbma_fix earn its own book, or is it redundant with donchian?

Question (2026-06-25): in the AM-fix window lbma_fix and donchian are same-
direction ~68% of their overlap. Netting just adds them. So does the separate
book add anything that scaling donchian by the same risk would not?

Three portfolios on the SAME 1m grid / same risk budget (live netting model,
reused from research_portfolio_overlay.py — vol-targeted weighted fracs, summed,
spread-cost charged on turnover):

  A  BASELINE     all books as in portfolio.yaml (incl lbma_fix 0.05)
  B  NO_LBMA      drop lbma_fix entirely (its 0.05 goes unused)
  C  FOLD_INTO    drop lbma_fix, add its weight to donchian (0.30 -> 0.35)

Decision rule — lbma_fix only earns a separate book if A beats BOTH:
  A > B : it has a positive marginal contribution at all.
  A > C : that contribution is NOT just "more donchian" — if C ~= A, the fix
          book is redundant and the weight is better folded into donchian.

Also reports P&L booked inside the AM-fix window (09:30-11:30 UTC) where the
two books interact, and a 4x-cost stress (turnover churn is the real risk if
lbma_fix is noise).

    PYTHONPATH=src .venv/Scripts/python.exe scripts/ablation_lbma_fix.py

RELATIVE read only: normalized-exposure sim (lev=1), ~1 month, one regime.
Compare configs to each other, not to live $ P&L.
"""
from __future__ import annotations

import copy
import pathlib
import sys

import numpy as np
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from research_portfolio_overlay import SLIP, book_frac_1m, gb  # noqa: E402

from mt5_trader.pipeline.data import bars_per_year  # noqa: E402

LBMA = "lbma_fix"
DONCHIAN = "donchian"


def pnl_metrics(expo: np.ndarray, ret: np.ndarray, sp_frac: np.ndarray,
                byr: float, win_mask: np.ndarray, cost_mult: float = 1.0) -> dict:
    """Live-netting metrics (mirrors research_portfolio_overlay.metrics) with a
    cost multiplier and a windowed-P&L readout instead of the reopen window."""
    pos = np.concatenate([[0.0], expo[:-1]])              # exec one bar later
    turn = np.abs(np.diff(pos, prepend=0.0))
    cost = turn * (sp_frac / 2 + SLIP) * cost_mult
    pnl = pos * ret - cost
    eq = np.cumprod(1.0 + pnl)
    dd = (eq / np.maximum.accumulate(eq) - 1.0).min()
    sharpe = pnl.mean() / pnl.std() * np.sqrt(byr) if pnl.std() > 0 else 0.0
    return {"ret": eq[-1] - 1.0, "sharpe": sharpe, "maxDD": dd,
            "turn": turn.sum(), "win_pnl": pnl[win_mask].sum()}


def main() -> None:
    cfg = yaml.safe_load(open("config/portfolio.yaml", encoding="utf-8"))
    books = cfg["books"]

    master = gb("1m")
    close = master["close"].to_numpy()
    ret = np.concatenate([[0.0], close[1:] / close[:-1] - 1.0])
    sp_frac = master["spread_mean"].fill_null(0).to_numpy() / np.where(close == 0, 1, close)
    byr = bars_per_year("1m")

    hour = master["ts"].dt.hour().to_numpy()
    minute = master["ts"].dt.minute().to_numpy()
    # AM-fix hold window: signal fires 09:30 UTC, holds 120 min -> 09:30..11:30
    after_open = (hour > 9) | ((hour == 9) & (minute >= 30))
    before_end = (hour < 11) | ((hour == 11) & (minute <= 30))
    fix_mask = after_open & before_end

    # Per-book weighted fracs on the common grid (faithful to live netting).
    fracs = {id(b): book_frac_1m(b, master) for b in books}
    net_all = sum(fracs.values())

    lbma_books = [b for b in books if b["strategy"] == LBMA]
    don_books = [b for b in books if b["strategy"] == DONCHIAN]
    if not lbma_books:
        raise SystemExit("no lbma_fix book in portfolio.yaml")
    if not don_books:
        raise SystemExit("no donchian book in portfolio.yaml")
    lbma_frac = sum(fracs[id(b)] for b in lbma_books)
    lbma_w = sum(float(b.get("weight", 1.0)) for b in lbma_books)
    don_w = sum(float(b.get("weight", 1.0)) for b in don_books)

    # A: baseline.  B: drop lbma.  C: drop lbma, fold its weight into donchian.
    net_A = net_all
    net_B = net_all - lbma_frac
    # donchian frac scales linearly in weight -> bump by (don_w+lbma_w)/don_w
    don_boost = sum(fracs[id(b)] * (lbma_w / don_w) for b in don_books)
    net_C = net_B + don_boost

    # Sanity: rebuild C from a modified config and confirm it matches the scaling.
    cfg_C = copy.deepcopy(cfg)
    for b in cfg_C["books"]:
        if b["strategy"] == DONCHIAN:
            b["weight"] = float(b.get("weight", 1.0)) * (don_w + lbma_w) / don_w
    net_C_rebuilt = sum(
        book_frac_1m(b, master) for b in cfg_C["books"] if b["strategy"] != LBMA
    )
    assert np.allclose(net_C, net_C_rebuilt, atol=1e-9), "fold-in scaling mismatch"

    print(f"1m grid: {master.height} bars  {master['ts'][0]} -> {master['ts'][-1]}")
    print(f"books: {len(books)}  | lbma_w={lbma_w:.2f}  donchian_w={don_w:.2f} "
          f"-> fold target {don_w + lbma_w:.2f}")
    print(f"lbma_fix active on {(lbma_frac != 0).sum()} of {master.height} bars "
          f"({100 * (lbma_frac != 0).mean():.1f}%); "
          f"AM-fix window = {fix_mask.sum()} bars\n")

    configs = [("A BASELINE", net_A), ("B NO_LBMA", net_B), ("C FOLD->donch", net_C)]

    for cost_mult, tag in [(1.0, "1x cost (0.5bp)"), (4.0, "4x cost (2.0bp)")]:
        print(f"== {tag} ==")
        print(f"{'config':<16}{'ret':>9}{'sharpe':>8}{'maxDD':>9}"
              f"{'turn':>8}{'fixwin_pnl':>12}")
        m = {}
        for label, expo in configs:
            r = pnl_metrics(expo, ret, sp_frac, byr, fix_mask, cost_mult)
            m[label[0]] = r
            print(f"{label:<16}{r['ret']:>+9.4f}{r['sharpe']:>8.2f}{r['maxDD']:>9.4f}"
                  f"{r['turn']:>8.0f}{r['win_pnl']:>+12.4f}")
        # Verdict.  A>B settles "adds at all".  A vs C is a return/risk tradeoff:
        # folding into donchian just leverages it (more ret, worse Sharpe/DD), so
        # judge "not just donchian" on risk-adjusted terms (Sharpe AND maxDD), and
        # confirm the edge lands where it should via the fix-window P&L.
        a, b, c = m["A"], m["B"], m["C"]
        adds = a["ret"] > b["ret"]
        risk_better = a["sharpe"] > c["sharpe"] and a["maxDD"] > c["maxDD"]  # DD<0
        in_window = a["win_pnl"] > c["win_pnl"]
        print(f"  A vs B (adds at all?)       ret {a['ret'] - b['ret']:+.4f}  "
              f"sharpe {a['sharpe'] - b['sharpe']:+.2f}  -> {'YES' if adds else 'no'}")
        print(f"  A vs C (not just donchian?) ret {a['ret'] - c['ret']:+.4f}  "
              f"sharpe {a['sharpe'] - c['sharpe']:+.2f}  maxDD {a['maxDD'] - c['maxDD']:+.4f}  "
              f"fixwin {a['win_pnl'] - c['win_pnl']:+.4f}")
        if adds and risk_better and in_window:
            verdict = ("KEEP separate — adds risk-adjusted value beyond scaling "
                       "donchian (better Sharpe+DD, more fix-window P&L)")
        elif adds and (risk_better or in_window):
            verdict = ("MARGINAL — return favours folding into donchian, "
                       "risk/window favours keeping; decide on your objective")
        elif adds:
            verdict = "FOLD into donchian — positive but no edge over scaling it"
        else:
            verdict = "DROP — no marginal value"
        print(f"  VERDICT: {verdict}\n")

    print("Reminder: relative read only (lev=1 sim, ~1 month, one regime, n~27 "
          "fix events). Re-run on out-of-sample data before acting.")


if __name__ == "__main__":
    main()
