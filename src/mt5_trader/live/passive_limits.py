"""Paper harness: rest passive limit orders at prior-day high/low and let the
broker report real fills — the ground-truth test the tick screen
(scripts/eval_passive_limits.py) can't give (it assumes optimistic fills).

Each UTC day: cancel yesterday's pendings, then post a buy-limit at the prior
day's low (support) and/or a sell-limit at the prior day's high (resistance),
each with a take-profit (the bounce) and stop (the level breaking) attached, so
the broker manages the price exit. One order per side per day.

Levels come from one of two sources (level_source):
  - pdhl: prior-day high/low (zero params).
  - consolidation: rolling ceiling/floor of the range, only while the regime is
    RANGE (the "ceiling and floor of a consolidating regime").

Three refinements over the raw screen (addressing its caveats):
  - regime-modulated: pdhl posts only the with-regime side (sides_for_regime);
    consolidation posts both bounds but only while RANGE. The screen showed
    counter-trend / non-range fades get adversely selected.
  - regime exit: sides are re-evaluated every step, so when a range stops
    consolidating the resting limits are cancelled (and re-posted if it returns).
  - fast exit: a fill that misses its TP/SL is flattened after max_hold_min —
    the screen's forward edge had decayed (even reversed) by ~60m.
We only post + log; fills, TP/SL outcomes, and P&L come from the broker.

ISOLATION: positions are segregated by magic (this book vs the portfolio bot's
1001) — reconcile/flatten only touch matching tickets, so the two never net or
fight, even on one account. Equity is shared by design: the portfolio risk
manager sees combined account equity, bounded by the small fixed lots (remedy C
below). A separate MT5_LOGIN would also isolate equity, but isn't required.

Pure helpers (prior_day_levels, limit_params) are unit-tested; the runner loop
is a thin broker wrapper.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl

from ..features.regime import RANGE, TREND_DOWN, TREND_UP, regime_series
from .mt5_gateway import MT5Gateway
from .runner import resilient_loop


def sides_for_regime(regime: int) -> tuple[float, ...]:
    """Which limit sides to post in the current regime — only fade the level
    the trend is NOT running through (addresses the screen's adverse-selection
    finding: counter-trend level fades break and bleed).
      TREND_UP   -> buy@support only (pullbacks bounce; resistance breaks).
      TREND_DOWN -> sell@resistance only (support breaks; rallies fade).
      RANGE      -> both (mean-revert inside the range)."""
    if regime == TREND_UP:
        return (1.0,)
    if regime == TREND_DOWN:
        return (-1.0,)
    return (1.0, -1.0)


def restable(side: float, level: float, bid: float, ask: float) -> bool:
    """A passive limit can only rest on the far side of the market: a buy limit
    below the ask, a sell limit above the bid. If the level is already breached
    (price has traded through it) the limit can't rest there -> skip it, else the
    broker rejects it (10015 Invalid price)."""
    return level < ask if side > 0 else level > bid


def desired_sides(level_source: str, regime: int, regime_gate: bool) -> tuple[float, ...]:
    """Sides to have resting now, by mode.
      consolidation: the bounds only mean-revert inside a range — post BOTH in
        RANGE, NOTHING otherwise (so a regime that stops consolidating cancels
        the orders).
      pdhl: with-regime side via sides_for_regime (or both if gate off)."""
    if level_source == "consolidation":
        return (1.0, -1.0) if regime == RANGE else ()
    return sides_for_regime(regime) if regime_gate else (1.0, -1.0)


def prior_day_levels(bars: pl.DataFrame) -> tuple[float, float] | None:
    """(PDH, PDL) = high/low of the latest COMPLETE prior calendar day in bars.
    None until at least two days are present. Uses only past data."""
    df = bars.with_columns(d=pl.col("ts").dt.date())
    days = df["d"].unique().sort()
    if len(days) < 2:
        return None
    prior = df.filter(pl.col("d") == days[-2])
    return float(prior["high"].max()), float(prior["low"].min())


def consolidation_levels(bars: pl.DataFrame, range_n: int) -> tuple[float, float] | None:
    """(ceiling, floor) of the recent consolidation = prior rolling max/min of
    close over range_n bars, at the latest bar (mirrors regime_switch's range
    bounds). shift(1) keeps it causal. None until the window is full."""
    df = bars.select(
        ceiling=pl.col("close").rolling_max(range_n).shift(1),
        floor=pl.col("close").rolling_min(range_n).shift(1),
    )
    c, f = df["ceiling"][-1], df["floor"][-1]
    if c is None or f is None:
        return None
    return float(c), float(f)


def limit_params(side: float, level: float, tp_bp: float,
                 sl_bp: float) -> tuple[float, float, float]:
    """(price, sl, tp) absolute prices for a passive limit at `level`.
    side>0 = buy at support (TP above = bounce, SL below = break);
    side<0 = sell at resistance (TP below, SL above). bp of `level`."""
    tp_d, sl_d = tp_bp * 1e-4 * level, sl_bp * 1e-4 * level
    if side > 0:
        return level, level - sl_d, level + tp_d
    return level, level + sl_d, level - tp_d


@dataclass
class PassiveCfg:
    symbol: str = "XAUUSD"
    lots: float = 0.05         # fixed (NOT vol-scaled) and small on purpose: this
                               # is the shared-equity bound (remedy C) — caps how far
                               # the experiment's P&L can move the account equity the
                               # portfolio kill switch / vol sizing sees. ~0.2x lev.
    tp_bp: float = 15.0        # 1:1 reward:risk (tp_bp == sl_bp) -> breakeven win
    sl_bp: float = 15.0        # rate ~50%, robust if the real win rate disappoints.
                               # Screen edge band was ~11-22bp; tune both from fills.
    max_hold_min: int = 30     # fast exit: flatten a fill after this long if TP/SL
                               # has not fired — the screen's edge decayed by ~60m
    level_source: str = "pdhl"  # "pdhl" (prior-day high/low) | "consolidation"
    range_n: int = 48          # consolidation window in bars (M5: 48 = 4h)
    regime_gate: bool = True   # pdhl: only post the with-regime side (sides_for_regime)
    regime_coarsen: int = 15   # regime ER sampling on the bars' timeframe
    magic: int = 2002          # order tag; distinct from the portfolio bot's so
                               # the two don't net against each other on one account
    timeframe: str = "M5"
    lookback_bars: int = 3000
    poll_seconds: float = 5.0
    heartbeat_seconds: float = 60.0
    log_path: Path = Path("logs/passive_paper.jsonl")


class PassiveLimitRunner:
    def __init__(self, gateway: MT5Gateway, cfg: PassiveCfg):
        self.gw = gateway
        self.cfg = cfg
        self._day = None
        self._armed_sides = None  # sides currently resting, to detect regime changes
        self._last_pos = 0.0
        self._fill_t = None  # monotonic time the current position was opened
        self._last_log_t = 0.0
        self.cfg.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _log(self, **kw) -> None:
        kw["ts"] = datetime.now(timezone.utc).isoformat()
        with open(self.cfg.log_path, "a") as f:
            f.write(json.dumps(kw, default=str) + "\n")
        self._last_log_t = time.monotonic()

    @staticmethod
    def _minutes_to_midnight(now: datetime) -> int:
        nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max(1, int((nxt - now).total_seconds() // 60))

    def arm(self, sell_level: float, buy_level: float,
            sides: tuple[float, ...], now: datetime) -> None:
        """Cancel this strategy's stale pendings, then post `sides` (decided by
        the caller per regime/mode). buy rests at buy_level (support/floor),
        sell at sell_level (resistance/ceiling). sides=() just cancels — used
        when the consolidation regime breaks."""
        self.gw.cancel_pending(self.cfg.symbol, magic=self.cfg.magic)
        exp = self._minutes_to_midnight(now)
        bid, ask = self.gw.quote(self.cfg.symbol)
        posted, skipped = [], []
        for side in sides:
            level = buy_level if side > 0 else sell_level
            if not restable(side, level, bid, ask):
                skipped.append(side)  # level already breached — can't rest here
                continue
            price, sl, tp = limit_params(side, level, self.cfg.tp_bp, self.cfg.sl_bp)
            self.gw.limit_order(self.cfg.symbol, side * self.cfg.lots,
                                price=price, sl=sl, tp=tp, expire_minutes=exp,
                                magic=self.cfg.magic)
            posted.append(side)
        self._log(event="ARM", sell_level=sell_level, buy_level=buy_level,
                  level_source=self.cfg.level_source, sides=posted, skipped=skipped,
                  bid=bid, ask=ask, lots=self.cfg.lots, tp_bp=self.cfg.tp_bp,
                  sl_bp=self.cfg.sl_bp, expire_min=exp)

    def step(self) -> None:
        now = datetime.now(timezone.utc)
        bars = self.gw.bars(self.cfg.symbol, self.cfg.timeframe, self.cfg.lookback_bars)
        if self.cfg.level_source == "consolidation":
            lv = consolidation_levels(bars, self.cfg.range_n)
        else:
            lv = prior_day_levels(bars)
        if lv is None:
            return
        sell_level, buy_level = lv  # (resistance/ceiling, support/floor)

        regime = int(regime_series(bars, coarsen=self.cfg.regime_coarsen)[-1])
        sides = desired_sides(self.cfg.level_source, regime, self.cfg.regime_gate)
        # Re-arm on a new day (refresh levels) OR when the regime changes the
        # desired sides — the latter cancels pendings once a range stops
        # consolidating (sides -> ()), and re-posts when conditions return.
        if now.date() != self._day or sides != self._armed_sides:
            self._day = now.date()
            self.arm(sell_level, buy_level, sides, now)
            self._armed_sides = sides

        pos = self.gw.position_lots(self.cfg.symbol, magic=self.cfg.magic)
        if pos != self._last_pos:  # a limit filled, or a TP/SL closed a position
            if pos == 0:
                self._fill_t = None
            elif self._last_pos == 0 or (pos > 0) != (self._last_pos > 0):
                self._fill_t = time.monotonic()  # new entry / flip — start the clock
            self._log(event="POSITION_CHANGE", position_lots=pos, prev=self._last_pos,
                      equity=self.gw.equity(), sell_level=sell_level, buy_level=buy_level)
            self._last_pos = pos

        # Fast exit: the screen's edge decayed by ~60m, so don't let a fill that
        # missed its TP/SL sit open indefinitely.
        if (pos != 0 and self._fill_t is not None
                and time.monotonic() - self._fill_t >= self.cfg.max_hold_min * 60):
            self.gw.flatten(self.cfg.symbol, magic=self.cfg.magic)
            self._log(event="TIME_EXIT", position_lots=pos, equity=self.gw.equity(),
                      held_min=self.cfg.max_hold_min)
            self._fill_t = None

        if time.monotonic() - self._last_log_t >= self.cfg.heartbeat_seconds:
            self._log(event="SNAP", equity=self.gw.equity(), position_lots=pos,
                      regime=regime, sell_level=sell_level, buy_level=buy_level)

    def run(self) -> None:
        self._log(event="START", symbol=self.cfg.symbol, lots=self.cfg.lots,
                  tp_bp=self.cfg.tp_bp, sl_bp=self.cfg.sl_bp)
        resilient_loop(self.step, self._log, self.gw.reconnect,
                       self.cfg.poll_seconds, lambda: False)
