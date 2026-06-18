#!/usr/bin/env python
"""RESEARCH: fast-vol defense overlays vs the 240-bar baseline.

D1 (fast-vol size scalar): feed max(slow_240, fast_ewma) into the inverse-vol
sizer instead of slow_240 alone. In calm fast<slow so sizing == the validated
240 (research_vol_window: 240 won); only when fast vol spikes ABOVE slow does
the position shrink faster. Unlike the window sweep (one knob: faster window =
noisier NORMAL sizing), max(slow,fast) de-risks on spikes WITHOUT touching
normal sizing — that's the bet.

D2 (spread-blowout entry mute): freeze the netted target on bars whose spread
blows out past k x its rolling median (hold the existing position, don't
transact into the blown spread; non-churning, unlike a flat-on-spike gate).
The engine already charges spread/2 per turnover, so D2's only edge is the
saved cost of NOT trading on blown bars plus where the deferred fill lands.

Nets the 6 books on a 1m grid (lev=1 relative read), same harness as
research_vol_window.py, so the baseline row reproduces its 240 number.
Want: lower maxDD without losing ret.

    uv run scripts/research_fast_vol.py
"""
from __future__ import annotations

import math

import numpy as np
import polars as pl
import yaml

from mt5_trader.pipeline.data import bars_per_year, build_bars
from mt5_trader.strategies import REGISTRY
from mt5_trader.strategies.meanrev_pairs import spread_bars

TF_EVERY = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}
TARGET_VOL, MAX_LEV = 0.30, 2.0
SLOW_N = 240                      # baseline realized-vol window (= current live)
# (fast EWMA span, spike gate c): use the fast leg only when fast > c x slow.
# c=1 is plain max(slow,fast); c>1 keeps normal sizing on the validated 240 and
# only de-sizes on genuine spikes.
D1_VARIANTS = [(48, 1.0), (48, 1.5), (48, 2.0), (48, 3.0), (24, 2.0), (96, 2.0)]
SPREAD_BASE_N = 1440             # ~1 trading day on the 1m master
SPREAD_MULTS = [3.0, 5.0, 8.0]   # mute a bar when spread > k x its rolling median
SLIP = 0.5e-4
_cache: dict = {}


def gb(every: str) -> pl.DataFrame:
    if every not in _cache:
        _cache[every] = build_bars("XAUUSD", every).sort("ts")
    return _cache[every]


def vol_eff(bars: pl.DataFrame, every: str, fast_span: int | None, c: float = 1.0) -> np.ndarray:
    """Effective sizing vol. fast_span=None -> the slow 240 baseline. Else the
    fast EWMA leg is used only when it spikes past c x slow (c=1 -> plain
    max(slow,fast)); below that, sizing stays on the validated 240. Held NaN
    until slow is defined so the first 240 bars trade nothing like the baseline."""
    bpy = bars_per_year(every)
    r = pl.col("close").log().diff()
    slow = bars.select((r.rolling_std(SLOW_N) * math.sqrt(bpy)).alias("s"))["s"].to_numpy()
    if fast_span is None:
        return slow
    fast = np.nan_to_num(
        bars.select((r.ewm_std(span=fast_span) * math.sqrt(bpy)).alias("f"))["f"].to_numpy())
    v = np.maximum(slow, fast) if c <= 1.0 else np.where(fast > c * slow, fast, slow)
    return np.where(np.isnan(slow), np.nan, v)


def book_frac_1m(bk: dict, master: pl.DataFrame, fast_span: int | None, c: float = 1.0) -> np.ndarray:
    name, tf = bk["strategy"], bk.get("timeframe", "M1")
    every, w = TF_EVERY[tf], float(bk.get("weight", 1.0))
    bars = (spread_bars(gb(every), build_bars("XAGUSD", every).sort("ts"), 1.0)
            if name == "ratio_mr" else gb(every))
    sig = np.asarray(REGISTRY[name](**(bk.get("params", {}) or {})).signal(bars), dtype=float)
    vol = vol_eff(bars, every, fast_span, c)
    lev = np.minimum(TARGET_VOL / np.maximum(vol, 1e-4), MAX_LEV)
    frac = np.nan_to_num(sig * lev) * w
    sdf = pl.DataFrame({"ts": bars["ts"], "s": frac}).sort("ts")
    return np.nan_to_num(master.select("ts").join_asof(sdf, on="ts", strategy="backward")["s"].to_numpy())


def net_books(books: list, master: pl.DataFrame, fast_span: int | None, c: float = 1.0) -> np.ndarray:
    return sum(book_frac_1m(b, master, fast_span, c) for b in books)


def spread_blown(master: pl.DataFrame, k: float) -> np.ndarray:
    """Boolean over master: spread abnormally wide vs its own rolling median."""
    sp = master["spread_mean"].fill_null(0).to_numpy().astype(float)
    med = master.select(pl.col("spread_mean").fill_null(0)
                        .rolling_median(SPREAD_BASE_N).alias("m"))["m"].to_numpy()
    ratio = sp / np.where((med == 0) | np.isnan(med), np.nan, med)
    return np.nan_to_num(ratio) > k


def apply_d2(net: np.ndarray, blown: np.ndarray) -> np.ndarray:
    """Freeze the target on blown bars (hold prior), resume after. fill_nan(None)
    first: polars treats np.nan as NaN, not null, so fill_null alone won't fill."""
    s = pl.Series(np.where(blown, np.nan, net))
    return s.fill_nan(None).fill_null(strategy="forward").fill_null(0).to_numpy()


def score(net: np.ndarray, ret: np.ndarray, sp: np.ndarray, byr: float) -> tuple:
    pos = np.concatenate([[0.0], net[:-1]])
    turn = np.abs(np.diff(pos, prepend=0.0))
    pnl = pos * ret - turn * (sp / 2 + SLIP)
    eq = np.cumprod(1.0 + pnl)
    dd = (eq / np.maximum.accumulate(eq) - 1.0).min()
    sh = pnl.mean() / pnl.std() * np.sqrt(byr) if pnl.std() > 0 else 0.0
    return eq[-1] - 1, sh, dd, turn.sum(), np.abs(net).max()


def row(tag: str, s: tuple) -> None:
    r, sh, dd, turn, nmax = s
    print(f"{tag:<22}{r:>+9.4f}{sh:>8.2f}{dd:>9.4f}{turn:>8.0f}{nmax:>9.2f}")


def main() -> None:
    books = yaml.safe_load(open("config/portfolio.yaml"))["books"]
    master = gb("1m")
    close = master["close"].to_numpy()
    ret = np.concatenate([[0.0], close[1:] / close[:-1] - 1.0])
    sp = master["spread_mean"].fill_null(0).to_numpy() / np.where(close == 0, 1, close)
    byr = bars_per_year("1m")

    base = net_books(books, master, None)

    print(f"{'variant':<22}{'ret':>9}{'sharpe':>8}{'maxDD':>9}{'turn':>8}{'|net|max':>9}")
    row("baseline (240)", score(base, ret, sp, byr))

    print("-- D1 fast-vol scalar: fast=ewm_std(span), used when fast > c x slow --")
    for span, c in D1_VARIANTS:
        net = net_books(books, master, span, c)
        bite = (np.abs(net) < np.abs(base) - 1e-9).mean()
        row(f"D1 s={span} c={c:g} (bite {bite:.1%})", score(net, ret, sp, byr))

    print("-- D2 spread mute: freeze target when spread > k x median --")
    for k in SPREAD_MULTS:
        blown = spread_blown(master, k)
        row(f"D2 k={k:g} (mute {blown.mean():.2%})", score(apply_d2(base, blown), ret, sp, byr))

    print("-- D1+D2 combined (span=48 c=2, k=5) --")
    combo = apply_d2(net_books(books, master, 48, 2.0), spread_blown(master, 5.0))
    row("D1 s=48 c=2 + D2 k=5", score(combo, ret, sp, byr))

    print("\nbite = bars D1 cut |net| vs baseline; mute = bars D2 froze the target.")
    print("Want: lower maxDD without losing ret. ~1 month, lev=1 relative read.")


if __name__ == "__main__":
    main()
