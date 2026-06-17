#!/usr/bin/env python
"""RESEARCH (not a live component): does gold's daily-reopen jump continue or fade?

Daily break is 21:00->22:00 UTC; reopen bar at 22:00. For each reopen we measure
the jump and then forward returns, testing two directional hypotheses:
  (A) GAP continuation: direction = sign(reopen_open / pre_break_close - 1)
  (B) MOMENTUM:        direction = sign(first 5-min move after reopen)
Signed forward return = direction * fwd_return. >0 mean => continuation pays;
<0 => it fades. Net subtracts a round-trip cost (real gold spread ~0.21bp).

    uv run scripts/research_open_jump.py
"""
import numpy as np
import polars as pl

from mt5_trader.pipeline.data import build_bars

RT_COST = 0.21e-4 * 2  # round-trip: real-feed gold spread ~0.21bp, in+out
HORIZONS = [5, 15, 30, 60]  # minutes after reopen


def reopen_rows(b: pl.DataFrame) -> pl.DataFrame:
    """Rows where a >20min gap precedes the bar = a session reopen. Tags the
    pre-break close and the gap length so daily (60min) and weekly reopens split."""
    b = b.sort("ts").with_columns(
        gap_min=(pl.col("ts").diff().dt.total_seconds() / 60),
        prev_close=pl.col("close").shift(1),
    )
    return b.filter(pl.col("gap_min") > 20)


def study(b: pl.DataFrame, idx: dict, reopens: pl.DataFrame, kind: str) -> None:
    close = b["close"].to_numpy()
    open_ = b["open"].to_numpy()
    rows = reopens.filter(
        (pl.col("gap_min") < 200) if kind == "daily" else (pl.col("gap_min") >= 200)
    )
    print(f"\n{'='*64}\n{kind.upper()} reopens: n={rows.height}\n{'='*64}")
    if rows.height == 0:
        return

    # A gap: direction known at reopen OPEN, entry at open, forward from open.
    # B momentum: direction = first-5min move (known at reopen+5), entry at the
    #   reopen+5 price, forward from there — no look-ahead, non-overlapping.
    gaps, mom_dir, fwd_open, fwd_5 = [], [], {h: [] for h in HORIZONS}, {h: [] for h in HORIZONS}
    for ts, prev_close in zip(rows["ts"], rows["prev_close"]):
        i = idx.get(ts)
        if i is None or prev_close is None:
            continue
        o, j5 = open_[i], i + 5
        gaps.append(o / prev_close - 1.0)
        mom_dir.append(np.sign(close[j5] / o - 1.0) if j5 < len(close) else np.nan)
        for h in HORIZONS:
            j = i + h
            fwd_open[h].append(close[j] / o - 1.0 if j < len(close) else np.nan)       # from open
            jj = j5 + h
            fwd_5[h].append(close[jj] / close[j5] - 1.0 if jj < len(close) else np.nan)  # from +5

    gaps, mom_dir = np.array(gaps), np.array(mom_dir)
    print(f"mean |gap| = {np.nanmean(np.abs(gaps))*1e4:6.1f} bp   "
          f"up-gaps {int((gaps>0).sum())}/{len(gaps)}")

    for name, direction, fwd, entry in [
        ("A gap-continuation (entry=reopen open)", np.sign(gaps), fwd_open, "open"),
        ("B momentum after first-5m (entry=reopen+5m)", mom_dir, fwd_5, "+5m"),
    ]:
        print(f"\n  {name}")
        print(f"    {'horizon':>8}{'mean_bp':>10}{'net_bp':>9}{'hit%':>7}{'t':>7}")
        for h in HORIZONS:
            m = direction * np.array(fwd[h])
            m = m[~np.isnan(m)]
            if len(m) == 0:
                continue
            mean = np.mean(m)
            t = mean / (np.std(m, ddof=1) / np.sqrt(len(m))) if len(m) > 1 and np.std(m) > 0 else 0.0
            print(f"    {h:>6}m {mean*1e4:>+9.1f}{(mean-RT_COST)*1e4:>+9.1f}"
                  f"{(m>0).mean()*100:>6.0f}%{t:>+7.2f}")


def main():
    b = build_bars("XAUUSD", "1m").sort("ts")
    idx = {ts: i for i, ts in enumerate(b["ts"])}
    print(f"XAUUSD 1m: {b.height} bars  {b['ts'][0]} -> {b['ts'][-1]}")
    reopens = reopen_rows(b).filter(pl.col("ts").dt.hour() == 22)  # the 22:00 reopen
    study(b, idx, reopens, "daily")
    study(b, idx, reopens, "weekly")
    print(f"\nRT_COST subtracted in net_bp = {RT_COST*1e4:.2f}bp (real gold spread, in+out)")
    print("Reminder: tiny sample — treat t<2 as noise.")


if __name__ == "__main__":
    main()
