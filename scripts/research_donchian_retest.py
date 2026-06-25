#!/usr/bin/env python
"""STAGE 1 (research, not a live component): does waiting for a retest of the
broken Donchian channel edge give a better entry on XAUUSD@M5 — and is the trade
we skip (the breakout that never pulls back) worth more than the better fill?

Baseline = current donchian (exit_n=110, market entry at the breakout).
Retest   = donchian_retest over max_wait x policy.

Reports per config: trade/fill/skip counts, mean entry-improvement at the anchor
L vs the baseline breakout close (bp, +ve = retest entered better), and the
engine PnL. CAVEATS: engine PnL prices the fill at the fill-bar CLOSE (conservative
proxy); the entry-improvement column is the precise L-vs-breakout edge. Optimistic
touch-fill; ~5wk sample -> DIRECTIONAL evidence, not a deploy gate.

    uv run scripts/research_donchian_retest.py
"""
from __future__ import annotations

import numpy as np

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.backtest.retest import simulate_retest_entry
from mt5_trader.pipeline.data import bars_per_year, build_bars
from mt5_trader.strategies.momentum_breakout import DonchianBreakout, DonchianRetest

SYMBOL, EVERY, EXIT_N = "XAUUSD", "5m", 110
MAX_WAITS = (12, 24, 48)          # M5 bars ~ 1h / 2h / 4h
POLICIES = (("skip", False), ("market_fallback", True))


def entry_improvement_bp(raw, entry_hi, entry_lo, entry_px, close) -> list[float]:
    """Per filled episode: side * (baseline breakout close - L) / breakout close,
    in bp. +ve means the retest entered at a better price than the market breakout."""
    out = []
    n = len(raw)
    for t in range(1, n):
        if raw[t] != 0 and raw[t - 1] == 0:           # breakout bar of a new episode
            side, ref = raw[t], close[t]
            for j in range(t, n):                      # find this episode's fill
                if raw[j] == 0:
                    break
                if not np.isnan(entry_px[j]):
                    out.append(side * (ref - entry_px[j]) / ref * 1e4)
                    break
    return out


def main():
    bars = build_bars(SYMBOL, EVERY)
    byr = bars_per_year(EVERY)
    cfg = BTConfig(bars_per_year=byr)
    close = bars["close"].to_numpy()

    base = DonchianBreakout(exit_n=EXIT_N)
    raw = base.signal(bars)
    lv = base.levels(bars)
    bm = run(bars, raw, cfg).metrics
    n_breakouts = int(((raw != 0) & (np.concatenate([[0], raw[:-1]]) == 0)).sum())
    print(f"baseline donchian@M5 exit_n={EXIT_N}: ret {bm['total_return']:+.4f}  "
          f"sharpe {bm['sharpe']:.2f}  maxDD {bm['max_drawdown']:.4f}  "
          f"trades {n_breakouts}")
    print(f"\n{'config':<26}{'fill':>5}{'fb':>4}{'skip':>5}"
          f"{'entry+bp':>10}{'ret':>9}{'sharpe':>8}{'maxDD':>9}")

    for mw in MAX_WAITS:
        for label, fb in POLICIES:
            fill = simulate_retest_entry(
                raw, lv["entry_hi"], lv["entry_lo"],
                bars["high"].to_numpy(), bars["low"].to_numpy(), close, mw, fb)
            imp = entry_improvement_bp(raw, lv["entry_hi"], lv["entry_lo"],
                                       fill.entry_px, close)
            m = run(bars, fill.pos, cfg).metrics
            mean_imp = float(np.mean(imp)) if imp else 0.0
            print(f"{f'mw{mw}/{label}':<26}{fill.n_filled:>5}{fill.n_fallback:>4}"
                  f"{fill.n_skipped:>5}{mean_imp:>10.1f}{m['total_return']:>9.4f}"
                  f"{m['sharpe']:>8.2f}{m['max_drawdown']:>9.4f}")


if __name__ == "__main__":
    main()
