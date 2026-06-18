"""Paper harness: rest passive limit orders at the consolidation ceiling/floor
and let the broker report real fills — the ground-truth test the tick screen
(scripts/eval_passive_limits.py) can't give (it assumes optimistic fills).

While the regime is RANGE, post a buy-limit at the range floor (support) and a
sell-limit at the range ceiling (resistance) — the rolling min/max of close over
range_n bars, the "floor and ceiling of a consolidating regime". Each carries a
take-profit (the bounce) and stop (the level breaking) so the broker manages the
price exit.

Three refinements over the raw screen (addressing its caveats):
  - regime-modulated: post both bounds only while RANGE — the screen showed
    counter-trend / non-range fades get adversely selected. The regime gate
    window must match the band (regime_coarsen=3 -> ~4h, like range_n=48 = 4h),
    else the slow daily regime never arms during short-term consolidations.
  - regime exit: sides are re-evaluated every step, so when a range stops
    consolidating (regime flips, or price breaks the band) the resting limits
    are cancelled, and re-posted if it returns.
  - fast exit: a fill that misses its TP/SL is flattened after max_hold_min —
    the screen's forward edge had decayed (even reversed) by ~60m.
We only post + log; fills, TP/SL outcomes, and P&L come from the broker.

ISOLATION: positions are segregated by magic (this book vs the portfolio bot's
1001) — reconcile/flatten only touch matching tickets, so the two never net or
fight, even on one account. Equity is shared by design: the portfolio risk
manager sees combined account equity, bounded by the small fixed lots (remedy C
below). A separate MT5_LOGIN would also isolate equity, but isn't required.

Pure helpers (consolidation_levels, limit_params) are unit-tested; the runner
loop is a thin broker wrapper.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl

from ..features.regime import RANGE, regime_series
from .mt5_gateway import MT5Gateway
from .runner import resilient_loop


def restable(side: float, level: float, bid: float, ask: float) -> bool:
    """A passive limit can only rest on the far side of the market: a buy limit
    below the ask, a sell limit above the bid. If the level is already breached
    (price has traded through it) the limit can't rest there -> skip it, else the
    broker rejects it (10015 Invalid price)."""
    return level < ask if side > 0 else level > bid


def desired_sides(regime: int) -> tuple[float, ...]:
    """Sides to have resting now. The consolidation bounds only mean-revert
    inside a range, so post BOTH in RANGE and NOTHING otherwise (a regime that
    stops consolidating cancels the orders)."""
    return (1.0, -1.0) if regime == RANGE else ()


def daily_halt(realized_today, cap, halted_day, today):
    """Daily loss-cap decision. Returns (active_halt, new_halted_day).

    Trips when today's realized P&L falls to -cap or below; once tripped, stays
    active for the rest of `today`; a halted_day from a previous day clears
    (auto-resume next UTC day). cap <= 0 disables the cap."""
    if cap > 0 and realized_today <= -cap:
        return True, today
    if halted_day == today:
        return True, today
    return False, halted_day


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
    tp_bp: float = 20.0        # closer target (capture the typical ~15bp reversion) +
    sl_bp: float = 25.0        # WIDER stop = survive the noise-tail before the level reverts.
                               # TP/SL sweep (2026-06-18, live 8h/8h gate): tp20/sl25 = +7.5bp
                               # /73% win, beats the old tp30/sl15 (+3.8/55%). Fades want a wide
                               # stop + close target, not 1:2. n~22, one regime — provisional.
    breakout_bp: float = 15.0  # band-break cancel margin for _broke_out, DECOUPLED from the
                               # position stop (sl_bp): keep the gate's cancel tight even with a
                               # wide stop, else a wide sl would hold limits into breakouts.
    max_hold_min: int = 30     # fast exit: flatten a fill after this long if TP/SL
                               # has not fired — the screen's edge decayed by ~60m
    range_n: int = 48          # consolidation window in bars (M5: 48 = 4h)
    relevel_bp: float = 10.0   # re-post the resting limits once the band (sell/buy
                               # level) drifts more than this (bp of level) from where
                               # they were armed, so the orders track the moving
                               # ceiling/floor instead of sitting stale until midnight.
                               # Only re-levels while flat (a fill is then managed by
                               # TP/SL/time-exit). 0 = off.
    regime_coarsen: int = 3    # regime ER sampling stride on the bars' timeframe;
                               # gate window = er_n(16)*stride. M5*3 = ~4h, matched to
                               # the 4h band so the gate arms during short-term ranges
                               # (15 = ~20h only caught multi-half-day chop).
    magic: int = 2003          # order tag; distinct from the portfolio bot's so
                               # the two don't net against each other on one account
    daily_loss_cap: float = 150.0  # account-ccy realized-loss cap for THIS book's
                               # magic over the UTC day; trip -> cancel+flatten+
                               # stand down till next day. 0 = off. The book's
                               # only aggregate circuit breaker (per-trade is
                               # TP/SL+time-exit; account equity is shared).
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
        self._armed_levels = None  # (sell, buy) the resting limits were armed at
        self._last_pos = 0.0
        self._halted_day = None    # UTC date the daily loss cap tripped (stand down)
        self._cap_day = None       # UTC date of the last realized-pnl query
        self._cap_realized = 0.0   # cached realized P&L for _cap_day
        self._last_cap_check = 0.0 # monotonic time of the last realized-pnl query
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

    def _broke_out(self, price: float, sell_level: float, buy_level: float) -> bool:
        """Price closed beyond the consolidation band (by breakout_bp) — the range has
        broken, so the bounds no longer mean-revert. The slow ER regime can lag a
        short-term trend and keep saying RANGE; this cancels the resting limits
        directly instead of letting them sit into the breakout (adverse fills)."""
        brk = self.cfg.breakout_bp * 1e-4
        return price > sell_level * (1 + brk) or price < buy_level * (1 - brk)

    def _band_drifted(self, sell_level: float, buy_level: float,
                      sides: tuple[float, ...]) -> bool:
        """True if a side we're resting has moved more than relevel_bp (of the
        level) from where it was armed. Only the posted sides count, and only
        while flat — a fill is managed by TP/SL/time-exit, not re-chased."""
        if (self.cfg.relevel_bp <= 0 or not sides or self._armed_levels is None
                or self._last_pos != 0):
            return False
        old_sell, old_buy = self._armed_levels
        tol = self.cfg.relevel_bp * 1e-4
        if -1.0 in sides and old_sell and abs(sell_level - old_sell) / old_sell > tol:
            return True
        if 1.0 in sides and old_buy and abs(buy_level - old_buy) / old_buy > tol:
            return True
        return False

    def arm(self, sell_level: float, buy_level: float,
            sides: tuple[float, ...], now: datetime, reason: str = "day/regime") -> None:
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
        self._log(event="ARM", reason=reason, sell_level=sell_level, buy_level=buy_level,
                  sides=posted, skipped=skipped,
                  bid=bid, ask=ask, lots=self.cfg.lots, tp_bp=self.cfg.tp_bp,
                  sl_bp=self.cfg.sl_bp, expire_min=exp)

    def step(self) -> None:
        now = datetime.now(timezone.utc)
        bars = self.gw.bars(self.cfg.symbol, self.cfg.timeframe, self.cfg.lookback_bars)
        lv = consolidation_levels(bars, self.cfg.range_n)
        if lv is None:
            return
        sell_level, buy_level = lv  # (resistance/ceiling, support/floor)

        # Daily loss cap (this book's own realized P&L, broker truth). Throttled
        # to ~30s; force a refresh when the UTC day rolls over so a stale loss
        # can't re-trip the new day.
        today = now.date()
        if self.cfg.daily_loss_cap > 0:
            mono = time.monotonic()
            if today != self._cap_day or mono - self._last_cap_check >= 30.0:
                sod = now.replace(hour=0, minute=0, second=0, microsecond=0)
                self._cap_realized = self.gw.realized_pnl(self.cfg.magic, sod)
                self._cap_day, self._last_cap_check = today, mono
            was_active = self._halted_day == today
            active, self._halted_day = daily_halt(
                self._cap_realized, self.cfg.daily_loss_cap, self._halted_day, today)
            if active:
                self.gw.cancel_pending(self.cfg.symbol, magic=self.cfg.magic)
                if not was_active:                      # trip transition
                    self.gw.flatten(self.cfg.symbol, magic=self.cfg.magic)
                    self._log(event="KILL", scope="daily_loss_cap",
                              realized=self._cap_realized, cap=self.cfg.daily_loss_cap)
                    self._last_pos = 0.0
                if time.monotonic() - self._last_log_t >= self.cfg.heartbeat_seconds:
                    self._log(event="SNAP", equity=self.gw.equity(), position_lots=0.0,
                              regime=int(regime_series(bars, coarsen=self.cfg.regime_coarsen)[-1]),
                              sell_level=sell_level, buy_level=buy_level, halted=True)
                return  # stand down: no arming this step

        regime = int(regime_series(bars, coarsen=self.cfg.regime_coarsen)[-1])
        sides = desired_sides(regime)
        # "Not consolidating" guard: if price has broken out of the band, cancel
        # both sides now rather than wait for the slow ER regime to flip out of
        # RANGE (it can lag a short-term trend and leave limits resting into it).
        if sides and self._broke_out(float(bars["close"][-1]), sell_level, buy_level):
            sides = ()
        # Re-arm on a new day (refresh levels), when the regime changes the desired
        # sides (cancels pendings once a range stops consolidating, re-posts when it
        # returns), OR when the consolidation band has drifted past relevel_bp from
        # where the resting limits were armed (so they track the moving ceiling/floor
        # instead of sitting stale until midnight).
        new_day = now.date() != self._day
        sides_changed = sides != self._armed_sides
        drifted = self._band_drifted(sell_level, buy_level, sides)
        if new_day or sides_changed or drifted:
            self._day = now.date()
            reason = "new_day" if new_day else "sides" if sides_changed else "relevel"
            self.arm(sell_level, buy_level, sides, now, reason=reason)
            self._armed_sides = sides
            self._armed_levels = (sell_level, buy_level)

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
