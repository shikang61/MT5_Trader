#!/usr/bin/env python
"""Optuna hyperparameter search for DonchianBreakout on XAUUSD.

Searches over timeframe (M1/M5), entry window, exit window, and EMA trend
filter. Parameters are expressed in time units (hours/days) so the search
space is meaningful across different bar sizes. Uses a fixed temporal IS/OOS
split so Optuna only ever sees the first (1 - oos_frac) of the data.

    uv run scripts/tune_donchian.py
    uv run scripts/tune_donchian.py --n-trials 300 --oos-frac 0.25
    uv run scripts/tune_donchian.py --timeframe 5m   # fix TF, tune params only

The final report shows:
  - Top 10 trials ranked by IS Sharpe (in-sample optimisation target)
  - OOS metrics for the best trial at 1x / 2x / 4x cost
  - OOS baseline: the currently-deployed config (5m, 240/60/1440)
"""
from __future__ import annotations

import argparse
import warnings
from typing import Optional

import optuna
import polars as pl

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.backtest.segment import segmented_signal
from mt5_trader.pipeline.data import DEFAULT_DATA, bars_per_year, cached_bars
from mt5_trader.strategies.momentum_breakout import DonchianBreakout

# ── constants ────────────────────────────────────────────────────────────────
TF_MINUTES = {"1m": 1, "5m": 5}
DEPLOYED   = dict(timeframe="5m", entry_n=240, exit_n=60, ema_n=1440)

# ── bar cache: one disk read per timeframe for all N trials ──────────────────
_BAR_CACHE: dict[str, pl.DataFrame] = {}


def get_bars(every: str) -> pl.DataFrame:
    if every not in _BAR_CACHE:
        _BAR_CACHE[every] = cached_bars("XAUUSD", every, DEFAULT_DATA)
    return _BAR_CACHE[every]


def run_bt(every: str, entry_n: int, exit_n: int, ema_n: int,
           bars: pl.DataFrame, slippage: float = 0.5) -> dict:
    strategy = DonchianBreakout(entry_n=entry_n, exit_n=exit_n, ema_n=ema_n)
    sig = segmented_signal(bars, strategy)
    result = run(bars, sig, BTConfig(
        slippage_bps=slippage,
        bars_per_year=bars_per_year(every),
        sl_atr_mult=8.0,   # match deployed disaster stop
        atr_window=60,
    ))
    return result.metrics


# ── Optuna objective ─────────────────────────────────────────────────────────

def make_objective(splits: dict[str, int],
                   fixed_tf: Optional[str] = None) -> callable:
    """Return an objective function closed over the IS/OOS split indices.

    splits  – {timeframe: last IS bar index}
    fixed_tf – if set, only that timeframe is tried (no TF search)
    """
    tf_choices = [fixed_tf] if fixed_tf else list(TF_MINUTES)

    def objective(trial: optuna.Trial) -> float:
        every = trial.suggest_categorical("timeframe", tf_choices)
        bph   = 60.0 / TF_MINUTES[every]   # bars per hour

        # Suggest in HOURS/DAYS so the search is timeframe-invariant.
        # log=True compresses the range so the sampler spends equal effort
        # at 4h and 40h (avoids over-sampling near the upper bound).
        entry_h = trial.suggest_float("entry_hours", 2.0, 48.0, log=True)
        exit_h  = trial.suggest_float("exit_hours",  0.5, 24.0, log=True)
        ema_d   = trial.suggest_float("ema_days",    0.5, 10.0, log=True)

        entry_n = max(2, round(entry_h * bph))
        exit_n  = max(1, round(exit_h  * bph))
        ema_n   = max(2, round(ema_d * 24 * bph))

        # Logical constraint: exit channel must be tighter than entry channel
        if exit_n >= entry_n:
            return -10.0

        is_bars = get_bars(every)[:splits[every]]
        m = run_bt(every, entry_n, exit_n, ema_n, is_bars)

        # Reject if warmup consumed most of the sample (few real trades)
        if m.get("active_bars", 0) < 50:
            return -10.0

        trial.set_user_attr("entry_n",       entry_n)
        trial.set_user_attr("exit_n",        exit_n)
        trial.set_user_attr("ema_n",         ema_n)
        trial.set_user_attr("is_return",     m["total_return"])
        trial.set_user_attr("is_sharpe",     m["sharpe"])
        trial.set_user_attr("is_maxdd",      m["max_drawdown"])
        trial.set_user_attr("is_active_bars", m.get("active_bars", 0))

        return float(m["sharpe"])

    return objective


# ── reporting ────────────────────────────────────────────────────────────────

def print_top(study: optuna.Study, n: int = 10) -> None:
    top = sorted(
        [t for t in study.trials if t.value is not None and t.value > -10],
        key=lambda t: t.value,
        reverse=True,
    )[:n]
    w = 72
    print(f"\n{'='*w}")
    print(f"Top {n} IS trials  (Sharpe on in-sample data)")
    print(f"{'='*w}")
    hdr = (f"{'#':>3}  {'TF':>3}  {'entry_h':>7}  {'exit_h':>6}  {'ema_d':>5}  "
           f"{'entry_n':>7}  {'exit_n':>6}  {'ema_n':>6}  "
           f"{'IS ret':>7}  {'IS Sh':>6}  {'IS DD':>7}  {'active':>6}")
    print(hdr)
    for rank, t in enumerate(top, 1):
        a, u = t.params, t.user_attrs
        print(
            f"{rank:>3}  {a['timeframe']:>3}  "
            f"{a['entry_hours']:>7.1f}  {a['exit_hours']:>6.1f}  "
            f"{a['ema_days']:>5.1f}  "
            f"{u['entry_n']:>7}  {u['exit_n']:>6}  {u['ema_n']:>6}  "
            f"{u['is_return']:>+7.4f}  {u['is_sharpe']:>6.2f}  "
            f"{u['is_maxdd']:>7.4f}  {u['is_active_bars']:>6}"
        )


def print_oos(study: optuna.Study, splits: dict[str, int]) -> None:
    best  = study.best_trial
    every = best.params["timeframe"]
    entry_n, exit_n, ema_n = (best.user_attrs[k]
                               for k in ("entry_n", "exit_n", "ema_n"))
    oos_bars = get_bars(every)[splits[every]:]

    w = 72
    print(f"\n{'='*w}")
    print(f"OOS report — best trial  "
          f"({every}  entry_n={entry_n}  exit_n={exit_n}  ema_n={ema_n})")
    print(f"{'='*w}")
    for slip, label in [(0.5, "1x cost (0.5bp)"),
                        (1.0, "2x cost (1.0bp)"),
                        (2.0, "4x cost (2.0bp)")]:
        m = run_bt(every, entry_n, exit_n, ema_n, oos_bars, slippage=slip)
        print(f"  {label:18}  ret {m['total_return']:+.4f}  "
              f"sharpe {m['sharpe']:6.2f}  maxDD {m['max_drawdown']:.4f}  "
              f"active_bars {m.get('active_bars', 0)}")

    # Compare to deployed baseline on the same OOS window
    dep_tf   = DEPLOYED["timeframe"]
    dep_bars = get_bars(dep_tf)[splits[dep_tf]:]
    bm = run_bt(dep_tf, DEPLOYED["entry_n"], DEPLOYED["exit_n"],
                DEPLOYED["ema_n"], dep_bars)
    print(f"\n  {'deployed baseline':18}  ret {bm['total_return']:+.4f}  "
          f"sharpe {bm['sharpe']:6.2f}  maxDD {bm['max_drawdown']:.4f}  "
          f"active_bars {bm.get('active_bars', 0)}")
    print(f"  ({dep_tf}  entry_n={DEPLOYED['entry_n']}  "
          f"exit_n={DEPLOYED['exit_n']}  ema_n={DEPLOYED['ema_n']})")


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n-trials",  type=int,   default=200,
                    help="number of Optuna trials (default 200)")
    ap.add_argument("--oos-frac",  type=float, default=0.2,
                    help="tail fraction held out as OOS (default 0.20 = last 20%%)")
    ap.add_argument("--timeframe", choices=list(TF_MINUTES),
                    help="fix timeframe; if omitted, both 1m and 5m are searched")
    ap.add_argument("--seed",      type=int,   default=42)
    ap.add_argument("--no-oos",    action="store_true",
                    help="skip the OOS report (faster when iterating)")
    args = ap.parse_args()

    # Pre-warm bar cache
    tfs_to_load = [args.timeframe] if args.timeframe else list(TF_MINUTES)
    if DEPLOYED["timeframe"] not in tfs_to_load:
        tfs_to_load.append(DEPLOYED["timeframe"])  # need baseline in OOS report
    print("Loading bars …")
    for tf in tfs_to_load:
        b = get_bars(tf)
        print(f"  {tf}: {len(b):,} bars  "
              f"[{b['ts'][0]}  →  {b['ts'][-1]}]")

    # IS/OOS boundary: derive from 5m (or fixed TF), apply same wall-clock
    # timestamp to all timeframes so they have the same OOS period.
    ref_tf  = args.timeframe or "5m"
    ref_b   = get_bars(ref_tf)
    cut_idx = round(len(ref_b) * (1 - args.oos_frac))
    cut_ts  = ref_b["ts"][cut_idx]
    splits  = {
        tf: int(get_bars(tf)["ts"].search_sorted(cut_ts))
        for tf in tfs_to_load
    }
    print(f"\nIS / OOS split at {cut_ts}  "
          f"(last {args.oos_frac*100:.0f}% = OOS)\n")

    # Run optimisation
    warnings.filterwarnings("ignore")
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=args.seed),
    )
    study.optimize(
        make_objective(splits, fixed_tf=args.timeframe),
        n_trials=args.n_trials,
        show_progress_bar=True,
    )

    print_top(study, n=10)
    if not args.no_oos:
        print_oos(study, splits)

    # Quick summary for the best trial
    best = study.best_trial
    print(f"\nBest IS Sharpe: {best.value:.3f}")
    print(f"  timeframe = {best.params['timeframe']}")
    print(f"  entry_n   = {best.user_attrs['entry_n']}  "
          f"({best.params['entry_hours']:.1f} h)")
    print(f"  exit_n    = {best.user_attrs['exit_n']}  "
          f"({best.params['exit_hours']:.1f} h)")
    print(f"  ema_n     = {best.user_attrs['ema_n']}  "
          f"({best.params['ema_days']:.1f} d)")


if __name__ == "__main__":
    main()
