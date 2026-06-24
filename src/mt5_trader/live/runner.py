"""Live trading loop for the Windows VPS.

Each cycle on a fresh M1 bar: pull bars -> strategy signal -> risk-sized
target -> reconcile MT5 position. Kill switch flattens and halts the round.
All decisions are appended to a JSONL log for the audit trail.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..backtest.engine import BTConfig
from ..features.technicals import atr, realized_vol
from ..risk.manager import RiskManager
from .mt5_gateway import MT5Error, MT5Gateway

TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "H1": 60}


@dataclass
class RunnerCfg:
    symbol: str
    lookback_bars: int = 3000
    vol_window: int = 240
    poll_seconds: float = 2.0   # 5->2: tighter disaster-stop trail (re-priced on
                                # the live forming price each poll). Entries are
                                # unaffected — they only change on a completed bar.
    heartbeat_seconds: float = 60.0
    sl_atr_mult: float = 3.0   # broker-side disaster stop, 0 = off
    tp_atr_mult: float = 0.0   # broker-side take profit, 0 = off
    atr_window: int = 60
    magic: int | None = None   # tag orders so strategies can share one account
    exposure_cap: float = 0.0  # cap |net directional exposure| as a leverage
                               # multiple of equity (lots*contract*price/equity);
                               # 0 = off. PortfolioRunner only.
    log_path: Path = Path("logs/live.jsonl")
    reopen_guard: tuple[str, str] | None = ("21:58", "22:03")  # detach the broker
                               # disaster stop across this UTC window (HH:MM) so the
                               # sub-second 22:00 reopen blowout can't sweep it. None=off.


def protective_levels(side: float, price: float, atr_value: float,
                      sl_mult: float, tp_mult: float) -> tuple[float, float]:
    """Absolute SL/TP prices for a position. Wide by design: the strategy's
    own exit logic fires first; these survive a dead runner/VPS. 0 = off."""
    if side == 0 or atr_value <= 0 or not (atr_value == atr_value):  # NaN guard
        return 0.0, 0.0
    direction = 1.0 if side > 0 else -1.0
    sl = price - direction * sl_mult * atr_value if sl_mult > 0 else 0.0
    tp = price + direction * tp_mult * atr_value if tp_mult > 0 else 0.0
    return sl, tp


def chandelier_sl(side: float, price: float, high: float, low: float,
                  atr_value: float, sl_mult: float,
                  prev: tuple | None) -> tuple[float, tuple]:
    """Trailing disaster stop anchored to the favorable extreme reached since
    entry, at a distance fixed from the ATR *at entry*. While the position side
    is unchanged the stop only moves favorably (up for longs, down for shorts)
    as new extremes print; a later volatility contraction can no longer tighten
    an existing stop. A flip or flat resets the anchor. 0/NaN ATR, sl_mult<=0 or
    flat -> off. Returns (sl, state) with state=(side, atr_entry, entry, extreme)
    to feed back as `prev` next bar."""
    if side == 0 or sl_mult <= 0 or atr_value <= 0 or atr_value != atr_value:
        return 0.0, (0.0, 0.0, 0.0, 0.0)
    direction = 1.0 if side > 0 else -1.0
    held = prev is not None and prev[0] != 0 and (prev[0] > 0) == (side > 0)
    if held:
        _, atr_entry, entry, extreme = prev
        extreme = max(extreme, high) if side > 0 else min(extreme, low)
    else:  # fresh entry or flip: lock the trail distance to entry ATR/price
        atr_entry, entry, extreme = atr_value, price, price
    sl = extreme - direction * sl_mult * atr_entry
    return sl, (side, atr_entry, entry, extreme)


def widen_breached_stop(side: float, sl: float, price: float,
                        atr: float, sl_mult: float) -> float:
    """Push a *breached* trailing stop a full disaster-distance from the current
    market. A chandelier stop anchored to a stale extreme can land on the wrong
    side of price after a bounce (short stop below market / long stop above);
    the gateway would then clamp it to ~spread away — an ultra-tight stop that
    instant-triggers and churns the book open->stop->open (observed live
    2026-06-24). Re-anchoring it to price +/- sl_mult*atr keeps a re-attach wide
    so normal chop can't sweep it. A stop already on the correct side (normal
    trailing) is left untouched; 0 sl / flat / no ATR -> unchanged."""
    if not sl or side == 0 or atr <= 0 or sl_mult <= 0:
        return sl
    floor = sl_mult * atr
    if side > 0:                 # long: protective stop sits below price
        return price - floor if sl >= price else sl
    return price + floor if sl <= price else sl   # short: stop sits above price


def in_reopen_guard(now: datetime, window: tuple[str, str] | None) -> bool:
    """True when `now`'s UTC time-of-day is in the [start, end) reopen-guard window
    (HH:MM strings). The broker disaster stop is detached across this window so the
    sub-second 22:00 gold-reopen spread blowout can't sweep it. None disables."""
    if not window:
        return False
    def _mins(hhmm: str) -> int:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    now_min = now.hour * 60 + now.minute
    return _mins(window[0]) <= now_min < _mins(window[1])


def pair_leg_lots(target_frac: float, beta: float, equity: float,
                  price_a: float, price_b: float,
                  contract_a: float, contract_b: float) -> tuple[float, float]:
    """Split a spread position into leg lots. Long spread (+frac) = long A,
    short beta-scaled B, each leg's notional = frac * equity (B scaled by beta)."""
    lots_a = target_frac * equity / (contract_a * price_a)
    lots_b = -beta * target_frac * equity / (contract_b * price_b)
    return lots_a, lots_b


def cap_lots(lots: float, contract_size: float, price: float,
             equity: float, cap: float) -> tuple[float, bool]:
    """Clip net lots so |exposure| <= cap, where exposure is the leverage
    multiple of equity (lots * contract_size * price / equity). Preserves sign.
    cap <= 0 (or non-positive equity/price) disables. Returns (lots, bound)."""
    if cap <= 0 or equity <= 0 or price <= 0:
        return lots, False
    frac = lots * contract_size * price / equity
    if abs(frac) <= cap:
        return lots, False
    capped = (cap if frac > 0 else -cap) * equity / (contract_size * price)
    return capped, True


def resilient_loop(step, log, reconnect, poll_seconds, should_halt) -> None:
    """Shared live loop: call step() until should_halt(), surviving transient
    errors. After 3 consecutive failures, try to re-attach to the terminal (it
    likely restarted). Used by every runner and the passive-limit book."""
    consec_errors = 0
    while not should_halt():
        try:
            step()
            consec_errors = 0
        except Exception as e:  # log and keep the loop alive
            log(event="ERROR", error=repr(e))
            consec_errors += 1
            if consec_errors >= 3:  # terminal likely restarted — re-attach
                try:
                    reconnect()
                    log(event="RECONNECT_OK")
                except Exception as re_err:
                    log(event="RECONNECT_FAILED", error=repr(re_err))
                consec_errors = 0
        time.sleep(poll_seconds)


class LiveRunner:
    def __init__(self, gateway: MT5Gateway, strategy, risk: RiskManager, cfg: RunnerCfg):
        self.gw = gateway
        self.strategy = strategy
        self.risk = risk
        self.cfg = cfg
        self._stood_down = False   # currently flat for the day on a kill trip
        self._last_bar_ts = None
        self._last_log_t = 0.0
        self.cfg.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _log(self, **kw) -> None:
        kw["ts"] = datetime.now(timezone.utc).isoformat()
        with open(self.cfg.log_path, "a") as f:
            f.write(json.dumps(kw, default=str) + "\n")
        self._last_log_t = time.monotonic()

    def _heartbeat(self) -> None:
        """Prove liveness when no new bar arrives (quiet market / weekend)."""
        if time.monotonic() - self._last_log_t >= self.cfg.heartbeat_seconds:
            self._log(event="HEARTBEAT", equity=self.gw.equity())

    def _kill_check(self, now: datetime) -> tuple[float, bool]:
        """Snapshot equity, roll the daily anchor, test the kill switch. On a
        trip: flatten everything and stand down flat for the rest of the UTC day
        (the loop keeps running; auto-resume next day). Returns (equity, killed)
        so the caller's step() returns immediately when killed. Shared by every
        runner's step()."""
        equity = self.gw.equity()
        self.risk.roll_day(now, equity)
        if self.risk.kill(equity, now):
            self.flatten_all()
            if not self._stood_down:                 # log once on the trip transition
                self._stood_down = True
                self._log(event="KILL_SWITCH", equity=equity)
            return equity, True
        if self._stood_down:                         # a new UTC day cleared the stand-down
            self._stood_down = False
            self._log(event="RESUME", equity=equity)
        return equity, False

    def step(self) -> None:
        now = datetime.now(timezone.utc)
        equity, killed = self._kill_check(now)  # every poll, before the new-bar gate
        if killed:
            return
        bars = self.gw.bars(self.cfg.symbol, "M1", self.cfg.lookback_bars)
        closed = bars[:-1]  # decide on the last COMPLETED bar, not the forming
        bar_ts = closed["ts"][-1]  # one at [-1] (see Book.targets); price stays live
        if bar_ts == self._last_bar_ts:
            self._heartbeat()
            return  # no new completed bar yet
        self._last_bar_ts = bar_ts

        sig = float(self.strategy.signal(closed)[-1])
        vol = float(closed.select(
            realized_vol(self.cfg.vol_window, BTConfig().bars_per_year)
        ).to_series()[-1])
        target_frac = self.risk.size(sig, vol, now)
        halt = self.risk.vol_halt(bars)  # safety breaker stays on the live bar
        if halt:
            target_frac = 0.0  # fast vol circuit breaker -> force flat

        spec = self.gw.symbol_spec(self.cfg.symbol)
        price = float(bars["close"][-1])  # current price for sizing
        target_lots = target_frac * equity / (spec["contract_size"] * price)

        atr_val = float(closed.select(atr(self.cfg.atr_window)).to_series()[-1] or 0.0)
        high, low = float(closed["high"][-1]), float(closed["low"][-1])
        sl, self._trail = chandelier_sl(target_lots, price, high, low, atr_val,
                                        self.cfg.sl_atr_mult,
                                        getattr(self, "_trail", None))
        _, atr_entry, entry_price, _ = self._trail
        _, tp = protective_levels(target_lots, entry_price, atr_entry,
                                  0.0, self.cfg.tp_atr_mult)
        delta = self.gw.reconcile(self.cfg.symbol, target_lots, sl=sl, tp=tp,
                                  magic=self.cfg.magic)

        self._log(event="STEP", bar_ts=bar_ts, equity=equity, signal=sig,
                  realized_vol=vol, target_frac=target_frac, vol_halt=halt,
                  target_lots=target_lots, traded_lots=delta, price=price,
                  sl=sl, tp=tp)

    def flatten_all(self) -> None:
        self.gw.flatten(self.cfg.symbol, magic=self.cfg.magic)

    def run(self) -> None:
        self._log(event="START", symbol=self.cfg.symbol,
                  strategy=type(self.strategy).__name__)
        resilient_loop(self.step, self._log, self.gw.reconnect,
                       self.cfg.poll_seconds, lambda: False)
        self._log(event="HALTED")


@dataclass
class Book:
    """One strategy on one symbol (or symbol pair). weight scales its share
    of the round's risk budget; weights across books should sum to ~1.
    timeframe is the bar size the strategy sees (M1/M5/M15/H1)."""
    strategy: object
    symbol: str
    weight: float = 1.0
    symbol_b: str = ""
    beta: float = 1.0
    trade_symbol_b: bool = True  # False: symbol_b feeds the SIGNAL (e.g. the ratio)
                                 # but only symbol (leg A) is traded — directional,
                                 # not the market-neutral pair.
    timeframe: str = "M1"
    _last_bar_ts: object = None
    _targets: dict = None  # type: ignore[assignment]

    def targets(self, bars_of, equity: float, risk: RiskManager, now,
                vol_window: int) -> tuple[dict[str, float], dict]:
        """Target lots per symbol. Recomputes on a new bar, else returns the
        cached targets so the portfolio keeps holding existing positions."""
        from ..strategies.meanrev_pairs import spread_bars

        bars_a = bars_of(self.symbol, self.timeframe)
        if self.symbol_b:
            sig_bars = spread_bars(bars_a, bars_of(self.symbol_b, self.timeframe), self.beta)
        else:
            sig_bars = bars_a
        # Decide on the last COMPLETED bar. copy_rates_from_pos returns the
        # in-progress bar at [-1]; signalling off it computes the decision at
        # bar-open on a near-stale close and mistimes every entry vs the
        # completed-bar backtest. Sizing below still uses the live forming price.
        closed = sig_bars[:-1]
        bar_ts = closed["ts"][-1]
        if bar_ts == self._last_bar_ts and self._targets is not None:
            return self._targets, {}
        self._last_bar_ts = bar_ts

        sig = float(self.strategy.signal(closed)[-1])
        tf_minutes = TF_MINUTES[self.timeframe]
        vol = float(closed.select(
            realized_vol(vol_window, BTConfig().bars_per_year / tf_minutes)
        ).to_series()[-1])
        frac = risk.size(sig, vol, now) * self.weight

        price_a = float(bars_a["close"][-1])
        if self.symbol_b and self.trade_symbol_b:
            bars_b = bars_of(self.symbol_b, self.timeframe)
            price_b = float(bars_b["close"][-1])
            lots_a, lots_b = pair_leg_lots(
                frac, self.beta, equity, price_a, price_b,
                bars_of.spec(self.symbol)["contract_size"],
                bars_of.spec(self.symbol_b)["contract_size"])
            self._targets = {self.symbol: lots_a, self.symbol_b: lots_b}
        else:  # single leg: outright symbol (signal may still use symbol_b)
            spec = bars_of.spec(self.symbol)
            self._targets = {self.symbol: frac * equity / (spec["contract_size"] * price_a)}
        info = {"book": self._name(), "bar_ts": bar_ts, "signal": sig,
                "realized_vol": vol, "weighted_frac": frac,
                "targets": dict(self._targets)}
        return self._targets, info

    def _name(self) -> str:
        if self.symbol_b and not self.trade_symbol_b:
            leg2 = f"(sig:{self.symbol_b})"  # symbol_b in the signal, not traded
        elif self.symbol_b:
            leg2 = f"+{self.symbol_b}"
        else:
            leg2 = ""
        return f"{type(self.strategy).__name__}:{self.symbol}{leg2}@{self.timeframe}"


class _BarCache:
    """One bars/spec fetch per symbol per cycle, shared across books."""

    def __init__(self, gw: MT5Gateway, lookback: int):
        self.gw, self.lookback = gw, lookback
        self._bars: dict = {}
        self._specs: dict = {}

    def __call__(self, symbol: str, timeframe: str = "M1"):
        key = (symbol, timeframe)
        if key not in self._bars:
            self._bars[key] = self.gw.bars(symbol, timeframe, self.lookback)
        return self._bars[key]

    def spec(self, symbol: str) -> dict:
        if symbol not in self._specs:
            self._specs[symbol] = self.gw.symbol_spec(symbol)
        return self._specs[symbol]

    def new_cycle(self) -> None:
        self._bars.clear()


class PortfolioRunner(LiveRunner):
    """Runs all books in one loop: per-symbol targets are NETTED across books
    (two books disagreeing on XAUUSD partially cancel — intended), risk budget
    is split by book weight, kill switch flattens every symbol."""

    def __init__(self, gateway: MT5Gateway, books: list[Book], risk: RiskManager,
                 cfg: RunnerCfg):
        super().__init__(gateway, books[0].strategy, risk, cfg)
        self.books = books
        self.gw.on_event = self._log    # gateway surfaces deduped errors (e.g. swallowed invalid-stops) here
        self.symbols = sorted({s for b in books for s in (b.symbol, b.symbol_b) if s})
        self._cache = _BarCache(gateway, cfg.lookback_bars)
        self._trail: dict[str, tuple] = {}
        # Net position spans every book on a symbol, so size its disaster stop
        # on the COARSEST timeframe any of those books trade — wide enough that
        # the slowest book is not knocked out by faster-timeframe bar noise.
        self._stop_tf: dict[str, str] = {}
        for b in books:
            for s in (b.symbol, b.symbol_b):
                cur = self._stop_tf.get(s)
                if s and (cur is None or TF_MINUTES[b.timeframe] > TF_MINUTES[cur]):
                    self._stop_tf[s] = b.timeframe

    def flatten_all(self) -> None:
        for s in self.symbols:
            self.gw.flatten(s, magic=self.cfg.magic)

    def step(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        self._cache.new_cycle()
        equity, killed = self._kill_check(now)
        if killed:
            return

        net: dict[str, float] = {s: 0.0 for s in self.symbols}
        infos = []
        for book in self.books:
            targets, info = book.targets(self._cache, equity, self.risk, now,
                                         self.cfg.vol_window)
            for sym, lots in targets.items():
                net[sym] += lots
            if info:
                infos.append(info)
        if not infos:
            self._heartbeat()
            return  # no book saw a new bar

        fills = {}
        for sym, lots in net.items():
            bars = self._cache(sym, self._stop_tf[sym])  # stop ATR on coarsest book TF
            halt = self.risk.vol_halt(bars)
            if halt:
                lots = 0.0  # fast vol circuit breaker -> force flat this symbol
            price = float(bars["close"][-1])
            contract = self._cache.spec(sym)["contract_size"]
            lots, capped = cap_lots(lots, contract, price, equity, self.cfg.exposure_cap)
            high, low = float(bars["high"][-1]), float(bars["low"][-1])
            atr_val = float(bars.select(atr(self.cfg.atr_window)).to_series()[-1] or 0.0)
            # The broker may have closed the net position (the disaster stop fired)
            # without the runner knowing. If it's flat, drop the stale chandelier
            # trail so the re-entry re-anchors a fresh stop at the current price —
            # else we re-open into an already-breached stop and churn (open ->
            # instant stop-out loop, observed live 2026-06-24). Mirrors the engine,
            # which re-anchors after every stop.
            if abs(self.gw.position_lots(sym, self.cfg.magic)) < 1e-9:
                self._trail[sym] = None
            sl, self._trail[sym] = chandelier_sl(lots, price, high, low, atr_val,
                                                 self.cfg.sl_atr_mult,
                                                 self._trail.get(sym))
            sl = widen_breached_stop(lots, sl, price, atr_val, self.cfg.sl_atr_mult)
            _, atr_entry, entry_price, _ = self._trail[sym]
            _, tp = protective_levels(lots, entry_price, atr_entry,
                                      0.0, self.cfg.tp_atr_mult)
            if in_reopen_guard(now, self.cfg.reopen_guard):
                # Reopen window: detach the broker disaster stop so the sub-second
                # 22:00 spread blowout can't sweep it (it reverts; the bot re-attaches
                # the trailed SL after the window). _trail keeps tracking the extreme.
                sl = 0.0
                self.gw.set_position_sltp(sym, 0.0, tp, magic=self.cfg.magic)
            # Isolate each symbol: a broker error on one must not skip the others
            # nor blank the STEP audit line (the whole step runs under one
            # resilient_loop try/except). Record the error on the fill and go on.
            try:
                traded, err = self.gw.reconcile(sym, lots, sl=sl, tp=tp,
                                                magic=self.cfg.magic), None
            except MT5Error as e:
                traded, err = 0.0, repr(e)
            fills[sym] = {"target_lots": lots, "sl": sl, "tp": tp,
                          "price": price, "vol_halt": halt, "cap": capped,
                          "contract_size": contract, "traded_lots": traded,
                          "error": err}

        self._log(event="STEP", equity=equity, books=infos, fills=fills)

    def run(self) -> None:
        self._log(event="START", books=[b._name() for b in self.books],
                  symbols=self.symbols)
        resilient_loop(self.step, self._log, self.gw.reconnect,
                       self.cfg.poll_seconds, lambda: False)
        self._log(event="HALTED")


@dataclass
class PairRunnerCfg(RunnerCfg):
    """symbol = leg A (long when spread long), symbol_b = leg B, beta = B scale."""
    symbol_b: str = ""
    beta: float = 1.0


class PairRunner(LiveRunner):
    """Two-leg spread execution: strategy sees synthetic spread bars
    (meanrev_pairs.spread_bars), positions are reconciled on both legs."""

    def __init__(self, gateway: MT5Gateway, strategy, risk: RiskManager, cfg: PairRunnerCfg):
        if not cfg.symbol_b:
            raise ValueError("PairRunnerCfg.symbol_b required")
        super().__init__(gateway, strategy, risk, cfg)

    def flatten_all(self) -> None:
        self.gw.flatten(self.cfg.symbol, magic=self.cfg.magic)
        self.gw.flatten(self.cfg.symbol_b, magic=self.cfg.magic)

    def step(self) -> None:
        from ..strategies.meanrev_pairs import spread_bars

        now = datetime.now(timezone.utc)
        equity, killed = self._kill_check(now)  # every poll, before the new-bar gate
        if killed:
            return
        bars_a = self.gw.bars(self.cfg.symbol, "M1", self.cfg.lookback_bars)
        bars_b = self.gw.bars(self.cfg.symbol_b, "M1", self.cfg.lookback_bars)
        spread = spread_bars(bars_a, bars_b, self.cfg.beta)
        closed = spread[:-1]  # decide on the last COMPLETED bar (see Book.targets)
        bar_ts = closed["ts"][-1]
        if bar_ts == self._last_bar_ts:
            self._heartbeat()
            return
        self._last_bar_ts = bar_ts

        sig = float(self.strategy.signal(closed)[-1])
        vol = float(closed.select(
            realized_vol(self.cfg.vol_window, BTConfig().bars_per_year)
        ).to_series()[-1])
        target_frac = self.risk.size(sig, vol, now)

        spec_a = self.gw.symbol_spec(self.cfg.symbol)
        spec_b = self.gw.symbol_spec(self.cfg.symbol_b)
        price_a = float(bars_a["close"][-1])
        price_b = float(bars_b["close"][-1])
        lots_a, lots_b = pair_leg_lots(
            target_frac, self.cfg.beta, equity, price_a, price_b,
            spec_a["contract_size"], spec_b["contract_size"],
        )
        traded_a = self.gw.reconcile(self.cfg.symbol, lots_a, magic=self.cfg.magic)
        traded_b = self.gw.reconcile(self.cfg.symbol_b, lots_b, magic=self.cfg.magic)

        self._log(event="STEP", bar_ts=bar_ts, equity=equity, signal=sig,
                  realized_vol=vol, target_frac=target_frac,
                  leg_a=self.cfg.symbol, target_lots_a=lots_a, traded_lots_a=traded_a,
                  leg_b=self.cfg.symbol_b, target_lots_b=lots_b, traded_lots_b=traded_b,
                  price_a=price_a, price_b=price_b)
