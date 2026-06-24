"""Vectorized bar-level backtester.

Conventions (no lookahead by construction):
  - signal[t] is the target position in [-1, 1], decided on bar t's CLOSE
    using only data up to and including bar t.
  - The position is held during bar t+1 (i.e. filled at ~bar t close /
    bar t+1 open) and earns bar t+1's close-to-close return.
  - Costs: each unit of turnover pays half the bar's mean spread plus
    slippage_bps. Calibrate slippage_bps against paper-trading fills.

Stops: off by default (exits are signal-only). Set sl_atr_mult/tp_atr_mult > 0
to model the live broker-side disaster stop intrabar — see _simulate_stops for
the model and its assumptions. Single instrument per run (pairs are backtested
as a synthetic spread series).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import polars as pl

from ..features.technicals import atr
from .metrics import summary
from ..stops import scaleout_remaining


@dataclass
class BTConfig:
    leverage: float = 1.0
    slippage_bps: float = 0.5
    init_equity: float = 1_000_000.0
    bars_per_year: float = 365 * 24 * 60  # 1-minute bars, ~24h markets
    sl_atr_mult: float = 0.0   # intrabar trailing disaster stop; 0 = off
    tp_atr_mult: float = 0.0   # intrabar take profit; 0 = off
    atr_window: int = 60       # ATR lookback for the stop distance
    scaleout_trigger: float = 0.0  # bank a fraction once open profit hits this many ATRs; 0 = off
    scaleout_frac: float = 0.0     # fraction of the position to close at the trigger; 0 = off


@dataclass
class BTResult:
    bars: pl.DataFrame
    metrics: dict = field(default_factory=dict)


def run(bars: pl.DataFrame, signal: np.ndarray, cfg: BTConfig | None = None) -> BTResult:
    cfg = cfg or BTConfig()
    n = len(bars)
    if len(signal) != n:
        raise ValueError(f"signal length {len(signal)} != bars length {n}")

    close = bars["close"].to_numpy().astype(float)
    if "spread_mean" in bars.columns:
        spread_frac = (bars["spread_mean"].fill_null(0).to_numpy().astype(float)
                       / np.where(close == 0, 1.0, close))
    else:
        spread_frac = np.zeros(n)

    sig = np.clip(np.nan_to_num(signal.astype(float)), -1.0, 1.0)
    pos = np.empty(n)
    pos[0] = 0.0
    pos[1:] = sig[:-1]  # executed one bar after the signal

    if cfg.sl_atr_mult > 0 or cfg.tp_atr_mult > 0:
        if not {"high", "low"}.issubset(bars.columns):
            raise ValueError("intrabar stops need 'high' and 'low' columns")
        atr_arr = bars.select(atr(cfg.atr_window)).to_series().to_numpy().astype(float)
        ret, extra_turn, size_mult = _simulate_stops(
            pos, close, bars["high"].to_numpy().astype(float),
            bars["low"].to_numpy().astype(float), atr_arr, cfg)
    else:
        ret = np.zeros(n)
        ret[1:] = close[1:] / close[:-1] - 1.0
        extra_turn = np.zeros(n)
        size_mult = np.ones(n)

    eff_pos = pos * size_mult
    turnover = np.abs(np.diff(eff_pos, prepend=0.0)) + extra_turn
    cost = turnover * (spread_frac / 2 + cfg.slippage_bps * 1e-4)

    pnl = cfg.leverage * (eff_pos * ret - cost)
    equity = cfg.init_equity * np.cumprod(1.0 + pnl)

    out = bars.with_columns(
        pos=pl.Series(pos),
        bar_ret=pl.Series(ret),
        pnl=pl.Series(pnl),
        equity=pl.Series(equity),
    )
    return BTResult(bars=out, metrics=summary(pnl, equity, cfg.bars_per_year, turnover))


def _simulate_stops(pos, close, high, low, atr_arr, cfg):
    """Bar-by-bar model of the live broker disaster stop. The position each bar
    stays the one-bar-delayed signal (pos[t]); when the strategy stays in, a
    stopped trade is re-entered the next bar exactly as the live runner
    re-reconciles. So the stop only (a) caps a bar's loss at the stop price and
    forfeits the rest of that bar's move, and (b) re-anchors the trail. SL trails
    chandelier-style at a distance fixed from the ATR at entry (mirrors
    runner.chandelier_sl); TP is a fixed target from entry. If both are touched
    in one bar the stop is assumed to fire first (conservative). A stop adds a
    round-trip (exit + re-entry) to turnover. Returns (ret, extra_turnover)."""
    n = len(close)
    ret = np.zeros(n)
    extra_turn = np.zeros(n)
    size_mult = np.ones(n)            # scale-out: fraction of position carried each bar
    atr_entry = entry = extreme = side = 0.0
    re_anchor = True
    for t in range(1, n):
        s = float(pos[t])
        prev = close[t - 1]
        if s == 0.0:
            side, re_anchor = 0.0, True
            continue
        long = s > 0
        if re_anchor or long != (side > 0):  # fresh entry, flip, or post-stop
            atr_entry, entry, extreme = atr_arr[t - 1], prev, prev
            re_anchor = False
        side = s
        extreme = max(extreme, high[t]) if long else min(extreme, low[t])
        valid = atr_entry > 0 and atr_entry == atr_entry
        direction = 1.0 if long else -1.0
        if valid:
            opa = abs(extreme - entry) / atr_entry
            size_mult[t] = scaleout_remaining(opa, cfg.scaleout_trigger, cfg.scaleout_frac)
        sl = (extreme - direction * cfg.sl_atr_mult * atr_entry
              if cfg.sl_atr_mult > 0 and valid else None)
        tp = (entry + direction * cfg.tp_atr_mult * atr_entry
              if cfg.tp_atr_mult > 0 and valid else None)
        exit_px = None
        if sl is not None and (low[t] <= sl if long else high[t] >= sl):
            exit_px = sl
        elif tp is not None and (high[t] >= tp if long else low[t] <= tp):
            exit_px = tp
        if exit_px is not None:
            ret[t] = exit_px / prev - 1.0
            extra_turn[t] = 2.0 * abs(s)  # exit + next-bar re-entry round trip
            re_anchor = True
        else:
            ret[t] = close[t] / prev - 1.0
    return ret, extra_turn, size_mult
