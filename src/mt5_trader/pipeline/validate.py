"""Generalised validation gate for ANY strategy candidate (not just deployed
books). Reuses the backtest engine and returns a structured per-gate PASS/FAIL
verdict. Generalises scripts/validate_books.py and adds an out-of-sample
hold-out and a deflated-Sharpe haircut.

Gates (all present gates must pass for overall PASS):
  week_by_week    : majority of calendar weeks positive (not one lucky week)
  cost_stress     : total return still positive at 4x slippage
  param_wiggle    : +/-25% on the primary param stays positive (plateau, not cliff)
  turnover        : avg position change per bar under a churn cap
  out_of_sample   : held-out later weeks positive (fixed params — an OOS check,
                    not rolling re-fit; documented honestly)
  deflated_sharpe : per-bar Sharpe survives a multiple-testing haircut for the
                    number of trials logged (Lo-2002 Sharpe SE x expected-max-of-N)

Thresholds live in DEFAULT_THRESHOLDS and can be overridden per call.
"""
from __future__ import annotations

import math

import numpy as np
import polars as pl

from ..backtest.engine import BTConfig, run
from ..backtest.metrics import summary
from ..strategies import REGISTRY
from .data import bars_per_year

DEFAULT_THRESHOLDS = {
    "week_pos_frac": 0.5,     # >= half the weeks positive
    "cost_mult": 4.0,         # slippage multiple for the stress gate
    "base_slippage": 0.5,     # bps at 1x
    "wiggle_pct": 0.25,       # +/- on the primary param
    "max_turnover_per_bar": 0.5,
}


def _bt(name: str, bars: pl.DataFrame, every: str, slippage: float = 0.5,
        **params):
    sig = REGISTRY[name](**params).signal(bars)
    res = run(bars, sig, BTConfig(slippage_bps=slippage,
                                  bars_per_year=bars_per_year(every)))
    return res


def _weekly_returns(res) -> list[float]:
    wk = res.bars.with_columns(w=pl.col("ts").dt.week())
    out = []
    for w in sorted(wk["w"].unique().to_list()):
        pnl = wk.filter(pl.col("w") == w)["pnl"].to_numpy()
        out.append(float(np.prod(1.0 + pnl) - 1.0))
    return out


def _deflated_sharpe(pnl: np.ndarray, n_trials: int) -> tuple[float, float]:
    """Per-bar Sharpe minus a haircut for multiple testing. Returns (raw_sr,
    deflated_sr), both per-bar. SE(SR) ~ sqrt((1 + sr^2/2)/n) (Lo 2002); the
    expected max of N standard normals ~ sqrt(2 ln N) (Bailey & Lopez de Prado
    DSR). deflated = sr - sqrt(2 ln N) * SE."""
    r = pnl[np.isfinite(pnl)]
    if len(r) < 2 or r.std() == 0:
        return 0.0, 0.0
    sr = float(r.mean() / r.std())
    se = math.sqrt((1 + 0.5 * sr * sr) / len(r))
    haircut = math.sqrt(2 * math.log(max(n_trials, 1))) if n_trials > 1 else 0.0
    return sr, sr - haircut * se


def validate_candidate(name: str, bars: pl.DataFrame, every: str = "5m", *,
                       wiggle: tuple[str, list] | None = None, n_trials: int = 1,
                       thresholds: dict | None = None, **params) -> dict:
    """Run all gates on REGISTRY[name](**params) over `bars`. Returns
    {gates: {gate: {pass: bool, ...}}, metrics: {...}, overall: bool}."""
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    full = _bt(name, bars, every, slippage=th["base_slippage"], **params)
    m = full.metrics
    gates: dict[str, dict] = {}

    weeks = _weekly_returns(full)
    pos_frac = (sum(r > 0 for r in weeks) / len(weeks)) if weeks else 0.0
    gates["week_by_week"] = {"pass": pos_frac >= th["week_pos_frac"],
                             "pos_frac": round(pos_frac, 3), "n_weeks": len(weeks)}

    ret_stress = _bt(name, bars, every,
                     slippage=th["base_slippage"] * th["cost_mult"],
                     **params).metrics["total_return"]
    gates["cost_stress"] = {"pass": ret_stress > 0,
                            f"ret_{int(th['cost_mult'])}x": round(ret_stress, 4)}

    if wiggle is not None:
        pkey, vals = wiggle
        lo, hi = vals[0], vals[-1]
        rets = {v: _bt(name, bars, every, **{**params, pkey: v}).metrics["total_return"]
                for v in (lo, hi)}
        gates["param_wiggle"] = {"pass": all(r > 0 for r in rets.values()),
                                 "param": pkey, "rets": {k: round(v, 4) for k, v in rets.items()}}

    tpb = m.get("total_turnover", 0.0) / max(m["n_bars"], 1)
    gates["turnover"] = {"pass": tpb <= th["max_turnover_per_bar"],
                         "per_bar": round(tpb, 4)}

    split = int(len(bars) * 0.6)
    oos = run(bars[split:], REGISTRY[name](**params).signal(bars)[split:],
              BTConfig(slippage_bps=th["base_slippage"], bars_per_year=bars_per_year(every)))
    gates["out_of_sample"] = {"pass": oos.metrics["total_return"] > 0,
                              "oos_ret": round(oos.metrics["total_return"], 4)}

    raw_sr, def_sr = _deflated_sharpe(full.bars["pnl"].to_numpy(), n_trials)
    gates["deflated_sharpe"] = {"pass": def_sr > 0, "raw": round(raw_sr, 4),
                                "deflated": round(def_sr, 4), "n_trials": n_trials}

    return {"gates": gates, "metrics": m,
            "overall": all(g["pass"] for g in gates.values())}
