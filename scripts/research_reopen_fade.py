#!/usr/bin/env python
"""RESEARCH (proxy): is gold's 22:00-UTC reopen gap a tradeable FADE — and is it
REAL (reopen-specific, not luck)?

The daily break is 21:00->22:00 UTC; research_open_jump found the reopen jump
FADES. This sizes the fade as a strategy (short an up-gap / long a down-gap at
the 22:00 reopen, hold H min into Asia, net of the wide reopen spread + slippage
stress) and then validates it:
  1. hold sweep + cost-stress + fade-vs-ride contrast
  2. NULL placebo-hours: the same prior-move fade at non-reopen hours. If 22:00
     dominates, the edge is reopen-specific, not generic intraday mean-reversion.
  3. NULL sign-permutation: p = P(random fade-sign beats the gap-based sign).
  4. WIGGLE: entry delay (can't fill at the open) and a min-gap threshold.

Daily reopens only (gap 20-200min). Thin (~20/month) — directional, gate on more
history later.

    uv run scripts/research_reopen_fade.py
"""
from __future__ import annotations

import numpy as np
import polars as pl

from mt5_trader.pipeline.data import build_bars

HOLDS = [15, 30, 60, 120, 240]
SLIPS = [0.5, 2.0, 4.0]
PLACEBO_HOURS = [2, 6, 10, 14, 18, 22]   # 22 = the real reopen
N_PERM = 20000
RNG = np.random.default_rng(0)


def reopens(b: pl.DataFrame) -> pl.DataFrame:
    b = b.with_columns(
        gap_min=pl.col("ts").diff().dt.total_seconds() / 60,
        prev_close=pl.col("close").shift(1),
    )
    return b.filter((pl.col("gap_min") > 20) & (pl.col("gap_min") < 200)
                    & (pl.col("ts").dt.hour() == 22))


def collect(close, open_, spf, idx, rows, hold, slip, delay=0, gap_min_bp=0.0):
    """Real 22:00 reopen fade. Returns (fade_ret, ride_ret, signed_gap, fwd, weeks)."""
    n = len(close)
    fade, ride, sgap, fwd_, wks = [], [], [], [], []
    for ts, pc, wk in zip(rows["ts"], rows["prev_close"], rows["wk"]):
        i = idx.get(ts)
        if i is None or pc is None:
            continue
        gap = open_[i] / pc - 1.0
        if abs(gap) < gap_min_bp * 1e-4:
            continue
        e = min(i + delay, n - 1)              # entry bar (delay after the reopen)
        ep = open_[i] if delay == 0 else close[e]
        j = min(e + hold, n - 1)
        cost = spf[e] / 2 + spf[j] / 2 + 2 * slip * 1e-4
        fwd = close[j] / ep - 1.0
        fade.append(-np.sign(gap) * fwd - cost)
        ride.append(np.sign(gap) * fwd - cost)
        sgap.append(np.sign(gap)); fwd_.append(fwd); wks.append(int(wk))
    return (np.array(fade), np.array(ride), np.array(sgap), np.array(fwd_), np.array(wks))


def placebo(close, spf, tod, week, hour, hold, slip):
    """Same fade at a non-reopen hour: synthetic 'gap' = prior-60min move."""
    n = len(close)
    pos = np.where(tod == hour * 60)[0]
    rets, wks = [], []
    for i in pos:
        if i < 60:
            continue
        gap = close[i] / close[i - 60] - 1.0
        j = min(i + hold, n - 1)
        cost = spf[i] / 2 + spf[j] / 2 + 2 * slip * 1e-4
        rets.append(-np.sign(gap) * (close[j] / close[i] - 1.0) - cost)
        wks.append(int(week[i]))
    return np.array(rets), np.array(wks)


def line(r, wks):
    if r.size == 0:
        return f"{0:>4}      (no trades)"
    wk = {w: r[wks == w].sum() for w in np.unique(wks)}
    share = np.abs(r).max() / np.abs(r).sum() if r.sum() != 0 else float("nan")
    return (f"{r.size:>4}{r.mean()*1e4:>+9.2f}{(r>0).mean():>6.0%}{r.sum()*1e4:>+9.1f}"
            f"{sum(v>0 for v in wk.values()):>3}/{len(wk)}{share:>8.0%}")


def main() -> None:
    b = build_bars("XAUUSD", "1m").sort("ts").with_columns(wk=pl.col("ts").dt.week().cast(pl.Int32))
    idx = {ts: i for i, ts in enumerate(b["ts"])}
    rows = reopens(b)
    close = b["close"].to_numpy().astype(float)
    open_ = b["open"].to_numpy().astype(float)
    spf = (b["spread_mean"].fill_null(0).to_numpy() / close).astype(float)
    tod = (b["ts"].dt.hour().cast(pl.Int32) * 60 + b["ts"].dt.minute().cast(pl.Int32)).to_numpy()
    week = b["wk"].to_numpy()
    print(f"XAUUSD 1m  {b['ts'][0]} -> {b['ts'][-1]}   daily 22:00 reopens: n={rows.height}")
    hdr = f"{'n':>4}{'avg_bp':>9}{'hit':>6}{'sum_bp':>9}{'wk+':>6}{'top%':>8}"

    print(f"\n-- GAP-FADE hold sweep x cost-stress --\n{'hold':>5}{'slip':>6}" + hdr)
    for hold in HOLDS:
        for slip in SLIPS:
            fade, *_unused, wks = collect(close, open_, spf, idx, rows, hold, slip)
            print(f"{hold:>5}{slip:>6.1f}" + line(fade, wks))

    print(f"\n-- contrast @ slip=2bp: FADE vs RIDE --\n{'hold':>5}  {'var':<6}" + hdr)
    for hold in HOLDS:
        fade, ride, _, _, wks = collect(close, open_, spf, idx, rows, hold, 2.0)
        print(f"{hold:>5}  {'fade':<6}" + line(fade, wks))
        print(f"{hold:>5}  {'ride':<6}" + line(ride, wks))

    print(f"\n-- NULL placebo-hours @ hold=60 slip=2bp (does 22:00 stand out?) --\n{'hourUTC':>8}" + hdr)
    for h in PLACEBO_HOURS:
        if h == 22:
            fade, _, _, _, wks = collect(close, open_, spf, idx, rows, 60, 2.0)
            print(f"{h:>6}:00 (real)" + line(fade, wks))
        else:
            r, wks = placebo(close, spf, tod, week, h, 60, 2.0)
            print(f"{h:>6}:00" + line(r, wks))

    print("\n-- NULL sign-permutation @ hold=60 slip=2bp --")
    fade, ride, sgap, fwd, wks = collect(close, open_, spf, idx, rows, 60, 2.0)
    actual = (-sgap * fwd).sum()
    perm = (RNG.choice([-1.0, 1.0], size=(N_PERM, fwd.size)) * fwd).sum(axis=1)
    p = (perm >= actual).mean()
    print(f"  actual gap-based fade edge = {actual*1e4:+.1f}bp-sum;  "
          f"random-sign >= actual in {p:.4f} of {N_PERM} perms  (p={p:.4f})")

    print(f"\n-- WIGGLE @ hold=60 slip=2bp --\n{'param':<16}" + hdr)
    for d in (0, 1, 2, 5):
        fade, _, _, _, wks = collect(close, open_, spf, idx, rows, 60, 2.0, delay=d)
        print(f"{'entry +'+str(d)+'min':<16}" + line(fade, wks))
    for g in (0.0, 5.0, 10.0):
        fade, _, _, _, wks = collect(close, open_, spf, idx, rows, 60, 2.0, gap_min_bp=g)
        print(f"{'gap>='+str(g)+'bp':<16}" + line(fade, wks))

    print("\nfade>0 & ride<0 => fade pays; 22:00>>placebo => reopen-specific; p<0.05 => not luck.")
    print("Thin (~18 reopens, ~5 weeks). Directional; gate on more history.")


if __name__ == "__main__":
    main()
