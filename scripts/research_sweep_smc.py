#!/usr/bin/env python
"""RESEARCH: SMC-flavoured upgrades to sweep_fade, BAR-ONLY (no L2 — live MT5
has no depth feed, so every level here is derived from OHLC).

Two questions the user raised:
  (a) Is the liquidity map richer if we add EQUAL-HIGHS / EQUAL-LOWS pools
      (clustered stops above a double-top / below a double-bottom) on top of
      the single prior-day high/low (PDH/PDL) sweep_fade trades today?
  (b) Does a LEVEL-BASED exit (target = opposing liquidity, stop = the sweep
      extreme) beat the fixed time exit (hold_bars)?

Method — faithful to sweep_fade's state machine (first cross of a level after
`quiet_bars`, classify `confirm_bars` later: close back inside => fade), just
generalised over the level source. Causality: swing pivots are only known
`piv` bars later, so EQ levels go active at their confirmation bar. Entries
decided on close[t]; engine lags execution one bar (see backtest/engine.py).

    PYTHONPATH=src .venv/Scripts/python.exe scripts/research_sweep_smc.py

RELATIVE read only: ~40 days, one regime, small event counts. Compare configs
to each other, confirm OOS before touching the live book.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.pipeline.data import DEFAULT_DATA, bars_per_year, cached_bars

SYMBOL, EVERY = "XAUUSD", "1m"


# ── level sources ──────────────────────────────────────────────────────────

def prior_day_levels(bars: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """PDH/PDL aligned to each bar (yesterday's extreme), like sweep_fade."""
    daily = (bars.with_columns(date=pl.col("ts").dt.date())
             .group_by("date").agg(pdh=pl.col("high").max(), pdl=pl.col("low").min())
             .sort("date").with_columns(pl.col("pdh", "pdl").shift(1)))
    df = bars.with_columns(date=pl.col("ts").dt.date()).join(daily, on="date", how="left")
    return df["pdh"].to_numpy(), df["pdl"].to_numpy()


def equal_level_arrays(high: np.ndarray, low: np.ndarray, piv: int,
                       tol_bp: float, lookback: int) -> tuple[np.ndarray, np.ndarray]:
    """Active equal-high / equal-low liquidity level at each bar (causal).

    A swing high at i (strict local max over +/-piv) is CONFIRMED at i+piv. If a
    prior confirmed swing high within `lookback` sits within `tol_bp` of it, the
    pair is an 'equal highs' pool; the level (stops rest just above) = the higher
    of the cluster, active from the confirmation bar onward. Symmetric for lows.
    """
    n = len(high)
    eqh = np.full(n, np.nan)
    eql = np.full(n, np.nan)
    sh_bar: list[int] = []
    sh_px: list[float] = []
    sl_bar: list[int] = []
    sl_px: list[float] = []
    for i in range(piv, n - piv):
        # swing high: strict local max both sides
        if high[i] > high[i - piv:i].max() and high[i] > high[i + 1:i + piv + 1].max():
            c = i + piv  # confirmation bar
            for b, p in zip(reversed(sh_bar), reversed(sh_px)):
                if c - b > lookback:
                    break
                if abs(high[i] - p) / high[i] * 1e4 <= tol_bp:
                    eqh[c:] = max(high[i], p)  # active until a newer EQH overwrites
                    break
            sh_bar.append(c)
            sh_px.append(high[i])
        if low[i] < low[i - piv:i].min() and low[i] < low[i + 1:i + piv + 1].min():
            c = i + piv
            for b, p in zip(reversed(sl_bar), reversed(sl_px)):
                if c - b > lookback:
                    break
                if abs(low[i] - p) / low[i] * 1e4 <= tol_bp:
                    eql[c:] = min(low[i], p)
                    break
            sl_bar.append(c)
            sl_px.append(low[i])
    return eqh, eql


# ── generalised sweep-fade scanner ─────────────────────────────────────────

@dataclass
class Event:
    entry: int          # classification bar (decision on close[entry])
    direction: float    # -1 short (high sweep), +1 long (low sweep)
    entry_px: float
    stop_px: float      # sweep extreme (worst price reached during the grab)
    target_px: float    # opposing prior-day level (opposing liquidity)


def scan(high, low, close, dates, hl, ll, pdh, pdl,
         quiet_bars: int, confirm_bars: int) -> list[Event]:
    """sweep_fade state machine generalised over a high-side level `hl` and a
    low-side level `ll` (price per bar; nan = no active level). Resets the
    per-side 'pending' on a new day, exactly like the production strategy."""
    n = len(close)
    events: list[Event] = []
    day = None
    BIG = 10**9
    last_hi = last_lo = -BIG
    pend_hi = pend_lo = -1
    cross_hi_extreme = cross_lo_extreme = np.nan
    for t in range(n):
        if dates[t] != day:
            day = dates[t]
            last_hi = last_lo = -BIG
            pend_hi = pend_lo = -1
        if not np.isnan(hl[t]):
            if high[t] > hl[t]:
                if t - last_hi > quiet_bars and pend_hi < 0:
                    pend_hi = t + confirm_bars
                    cross_hi_extreme = high[t]
                last_hi = t
                cross_hi_extreme = np.nanmax([cross_hi_extreme, high[t]])
            if t == pend_hi:
                if close[t] < hl[t]:
                    events.append(Event(t, -1.0, close[t],
                                        max(cross_hi_extreme, high[t]), pdl[t]))
                pend_hi = -1
        if not np.isnan(ll[t]):
            if low[t] < ll[t]:
                if t - last_lo > quiet_bars and pend_lo < 0:
                    pend_lo = t + confirm_bars
                    cross_lo_extreme = low[t]
                last_lo = t
                cross_lo_extreme = np.nanmin([cross_lo_extreme, low[t]])
            if t == pend_lo:
                if close[t] > ll[t]:
                    events.append(Event(t, 1.0, close[t],
                                        min(cross_lo_extreme, low[t]), pdh[t]))
                pend_lo = -1
    return events


# ── exit policies -> position array (for engine metrics) ───────────────────

def pos_time(events: list[Event], n: int, hold: int) -> np.ndarray:
    pos = np.zeros(n)
    for e in events:
        pos[e.entry:min(e.entry + hold, n)] += e.direction
    return pos


def resolve_level_exit(e: Event, high, low, close, cap: int,
                       target_px: float) -> tuple[int, float]:
    """Walk forward from entry; exit at stop (sweep extreme) or target, fill at
    that price; else close out at the cap bar. Stop assumed first if both touch
    the same bar (conservative). Returns (exit_bar, fill_px)."""
    n = len(close)
    end = min(e.entry + cap, n - 1)
    for t in range(e.entry + 1, end + 1):
        if e.direction < 0:  # short: stop above, target below
            if high[t] >= e.stop_px:
                return t, e.stop_px
            if low[t] <= target_px:
                return t, target_px
        else:                # long: stop below, target above
            if low[t] <= e.stop_px:
                return t, e.stop_px
            if high[t] >= target_px:
                return t, target_px
    return end, close[end]


def event_returns(events, high, low, close, exit_mode: str, hold: int, cap: int,
                  r_mult: float, cost_bp: float) -> np.ndarray:
    """Per-event net return (bp) under an exit policy, fills at the real price.
    exit_mode: 'time' | 'opposing' (opposing PD level) | 'rmult' (R multiple)."""
    out = []
    for e in events:
        if exit_mode == "time":
            xb = min(e.entry + hold, len(close) - 1)
            fill = close[xb]
        else:
            if exit_mode == "rmult":
                risk = abs(e.entry_px - e.stop_px)
                tgt = e.entry_px - e.direction * (-1) * 0  # placeholder
                tgt = e.entry_px + e.direction * r_mult * risk
            else:  # opposing liquidity
                tgt = e.target_px
                if np.isnan(tgt):
                    xb = min(e.entry + hold, len(close) - 1)
                    out.append(e.direction * (close[xb] / e.entry_px - 1) * 1e4 - 2 * cost_bp)
                    continue
            _, fill = resolve_level_exit(e, high, low, close, cap, tgt)
        out.append(e.direction * (fill / e.entry_px - 1) * 1e4 - 2 * cost_bp)
    return np.array(out) if out else np.array([0.0])


# ── reporting ──────────────────────────────────────────────────────────────

def bt(bars, pos, byr, slip=0.5):
    return run(bars, pos, BTConfig(slippage_bps=slip, bars_per_year=byr,
                                   sl_atr_mult=0.0)).metrics


def estat(r: np.ndarray) -> str:
    n = len(r)
    mean = r.mean()
    t = mean / (r.std() / np.sqrt(n)) if n > 1 and r.std() > 0 else 0.0
    hit = (r > 0).mean() * 100
    return f"n={n:>3}  mean {mean:>+6.1f}bp  hit {hit:>4.1f}%  t {t:>+5.2f}  sum {r.sum()/1e4:>+7.4f}"


def main():
    bars = cached_bars(SYMBOL, EVERY, DEFAULT_DATA)
    byr = bars_per_year(EVERY)
    high = bars["high"].to_numpy(); low = bars["low"].to_numpy()
    close = bars["close"].to_numpy()
    dates = bars["ts"].dt.date().to_numpy()
    pdh, pdl = prior_day_levels(bars)
    n = len(close)
    nan = np.full(n, np.nan)
    print(f"{SYMBOL} {EVERY}: {n:,} bars  [{bars['ts'][0].date()} -> {bars['ts'][-1].date()}]\n")

    QUIET, CONFIRM, HOLD = 60, 3, 10
    PIV, TOL, LOOKBACK, CAP = 5, 5.0, 240, 60

    eqh, eql = equal_level_arrays(high, low, PIV, TOL, LOOKBACK)
    ev_pd = scan(high, low, close, dates, pdh, pdl, pdh, pdl, QUIET, CONFIRM)
    ev_eq = scan(high, low, close, dates, eqh, eql, pdh, pdl, QUIET, CONFIRM)
    ev_both = ev_pd + ev_eq

    # ── (a) level-source comparison, time exit ────────────────────────────
    print("=" * 78)
    print("(a) LEVEL SOURCE  (fade + fixed time exit, hold=10) — does EQH/EQL add?")
    print("=" * 78)
    print(f"  EQ params: piv={PIV} tol={TOL}bp lookback={LOOKBACK}\n")
    print(f"  {'config':<16}{'events':>7}{'ret1x':>9}{'Sh1x':>7}{'DD1x':>9}"
          f"{'ret4x':>9}{'Sh4x':>7}")
    for label, ev in [("PDH/PDL (base)", ev_pd), ("EQH/EQL only", ev_eq),
                      ("PD + EQ", ev_both)]:
        pos = pos_time(ev, n, HOLD)
        m1, m4 = bt(bars, pos, byr, 0.5), bt(bars, pos, byr, 2.0)
        print(f"  {label:<16}{len(ev):>7}{m1['total_return']:>+9.4f}{m1['sharpe']:>7.2f}"
              f"{m1['max_drawdown']:>9.4f}{m4['total_return']:>+9.4f}{m4['sharpe']:>7.2f}")

    print("\n  EQ sensitivity (PD+EQ, time exit, 1x cost) — plateau check:")
    print(f"  {'piv':>4}{'tol':>6}{'lookbk':>8}{'events':>7}{'ret':>9}{'Sh':>7}{'DD':>9}")
    for piv, tol, lb in [(3, 5, 240), (5, 3, 240), (5, 5, 240), (5, 8, 240),
                         (5, 5, 120), (5, 5, 480), (8, 5, 240)]:
        eh, el = equal_level_arrays(high, low, piv, tol, lb)
        ev = ev_pd + scan(high, low, close, dates, eh, el, pdh, pdl, QUIET, CONFIRM)
        m = bt(bars, pos_time(ev, n, HOLD), byr, 0.5)
        print(f"  {piv:>4}{tol:>6.0f}{lb:>8}{len(ev):>7}{m['total_return']:>+9.4f}"
              f"{m['sharpe']:>7.2f}{m['max_drawdown']:>9.4f}")

    # ── (b) exit-policy comparison on the PDH/PDL event set ───────────────
    print("\n" + "=" * 78)
    print("(b) EXIT POLICY  (same PDH/PDL entries) — level target vs time exit")
    print("=" * 78)
    print(f"  events={len(ev_pd)}  cost=0.5bp/side, event-level fills at the actual exit price\n")
    for label, mode, rm in [("time hold=10", "time", 0),
                            ("time hold=20", "time", 0),
                            ("opposing PD level", "opposing", 0),
                            ("2R target", "rmult", 2.0),
                            ("3R target", "rmult", 3.0)]:
        hold = 20 if "20" in label else HOLD
        r = event_returns(ev_pd, high, low, close, mode, hold, CAP, rm, 0.5)
        print(f"  {label:<20}{estat(r)}")

    # engine cross-check (close-marked) for the two headline exits
    print("\n  engine cross-check (close-to-close, 1x cost):")
    pos_t = pos_time(ev_pd, n, HOLD)
    pos_l = np.zeros(n)
    for e in ev_pd:
        xb, _ = resolve_level_exit(e, high, low, close, CAP,
                                   e.target_px if not np.isnan(e.target_px)
                                   else e.entry_px)
        pos_l[e.entry:max(xb, e.entry + 1)] += e.direction
    for label, pos in [("time hold=10", pos_t), ("opposing-level exit", pos_l)]:
        m = bt(bars, pos, byr, 0.5)
        print(f"    {label:<22}ret {m['total_return']:>+8.4f}  Sh {m['sharpe']:>6.2f}  "
              f"DD {m['max_drawdown']:>8.4f}  turn {m['total_turnover']:>6.2f}")

    print("\nRELATIVE read only: ~40 days, n small. Confirm OOS before touching live.")


if __name__ == "__main__":
    main()
