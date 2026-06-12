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
from .mt5_gateway import MT5Gateway


@dataclass
class RunnerCfg:
    symbol: str
    lookback_bars: int = 3000
    vol_window: int = 240
    poll_seconds: float = 5.0
    heartbeat_seconds: float = 60.0
    sl_atr_mult: float = 3.0   # broker-side disaster stop, 0 = off
    tp_atr_mult: float = 0.0   # broker-side take profit, 0 = off
    atr_window: int = 60
    log_path: Path = Path("logs/live.jsonl")


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


def ratchet_sl(side: float, sl: float, prev_side: float, prev_sl: float) -> float:
    """Trailing stop: while the position direction is unchanged, the SL only
    tightens (up for longs, down for shorts), never loosens. Direction flip
    or flat resets the ratchet."""
    if side == 0 or sl == 0.0:
        return sl
    if prev_side == 0 or prev_sl == 0.0 or (side > 0) != (prev_side > 0):
        return sl
    return max(sl, prev_sl) if side > 0 else min(sl, prev_sl)


def pair_leg_lots(target_frac: float, beta: float, equity: float,
                  price_a: float, price_b: float,
                  contract_a: float, contract_b: float) -> tuple[float, float]:
    """Split a spread position into leg lots. Long spread (+frac) = long A,
    short beta-scaled B, each leg's notional = frac * equity (B scaled by beta)."""
    lots_a = target_frac * equity / (contract_a * price_a)
    lots_b = -beta * target_frac * equity / (contract_b * price_b)
    return lots_a, lots_b


class LiveRunner:
    def __init__(self, gateway: MT5Gateway, strategy, risk: RiskManager, cfg: RunnerCfg):
        self.gw = gateway
        self.strategy = strategy
        self.risk = risk
        self.cfg = cfg
        self.halted = False
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

    def step(self) -> None:
        now = datetime.now(timezone.utc)
        bars = self.gw.bars(self.cfg.symbol, "M1", self.cfg.lookback_bars)
        bar_ts = bars["ts"][-1]
        if bar_ts == self._last_bar_ts:
            self._heartbeat()
            return  # no new bar yet
        self._last_bar_ts = bar_ts

        equity = self.gw.equity()
        self.risk.mark_round_start(now, equity)

        if self.risk.kill(equity, now):
            self.flatten_all()
            self.halted = True
            self._log(event="KILL_SWITCH", equity=equity)
            return

        sig = float(self.strategy.signal(bars)[-1])
        vol = float(bars.select(
            realized_vol(self.cfg.vol_window, BTConfig().bars_per_year)
        ).to_series()[-1])
        target_frac = self.risk.size(sig, vol, now)

        spec = self.gw.symbol_spec(self.cfg.symbol)
        price = float(bars["close"][-1])
        target_lots = target_frac * equity / (spec["contract_size"] * price)

        atr_val = float(bars.select(atr(self.cfg.atr_window)).to_series()[-1] or 0.0)
        sl, tp = protective_levels(target_lots, price, atr_val,
                                   self.cfg.sl_atr_mult, self.cfg.tp_atr_mult)
        prev_side, prev_sl = getattr(self, "_trail", (0.0, 0.0))
        sl = ratchet_sl(target_lots, sl, prev_side, prev_sl)
        self._trail = (target_lots, sl)
        delta = self.gw.reconcile(self.cfg.symbol, target_lots, sl=sl, tp=tp)

        self._log(event="STEP", bar_ts=bar_ts, equity=equity, signal=sig,
                  realized_vol=vol, target_frac=target_frac,
                  target_lots=target_lots, traded_lots=delta, price=price,
                  sl=sl, tp=tp)

    def flatten_all(self) -> None:
        self.gw.flatten(self.cfg.symbol)

    def run(self) -> None:
        self._log(event="START", symbol=self.cfg.symbol,
                  strategy=type(self.strategy).__name__)
        consec_errors = 0
        while not self.halted:
            try:
                self.step()
                consec_errors = 0
            except Exception as e:  # log and keep the loop alive
                self._log(event="ERROR", error=repr(e))
                consec_errors += 1
                if consec_errors >= 3:  # terminal likely restarted — re-attach
                    try:
                        self.gw.reconnect()
                        self._log(event="RECONNECT_OK")
                    except Exception as re_err:
                        self._log(event="RECONNECT_FAILED", error=repr(re_err))
                    consec_errors = 0
            time.sleep(self.cfg.poll_seconds)
        self._log(event="HALTED")


@dataclass
class Book:
    """One strategy on one symbol (or symbol pair). weight scales its share
    of the round's risk budget; weights across books should sum to ~1."""
    strategy: object
    symbol: str
    weight: float = 1.0
    symbol_b: str = ""
    beta: float = 1.0
    _last_bar_ts: object = None
    _targets: dict = None  # type: ignore[assignment]

    def targets(self, bars_of, equity: float, risk: RiskManager, now,
                vol_window: int) -> tuple[dict[str, float], dict]:
        """Target lots per symbol. Recomputes on a new bar, else returns the
        cached targets so the portfolio keeps holding existing positions."""
        from ..strategies.meanrev_pairs import spread_bars

        bars_a = bars_of(self.symbol)
        if self.symbol_b:
            sig_bars = spread_bars(bars_a, bars_of(self.symbol_b), self.beta)
        else:
            sig_bars = bars_a
        bar_ts = sig_bars["ts"][-1]
        if bar_ts == self._last_bar_ts and self._targets is not None:
            return self._targets, {}
        self._last_bar_ts = bar_ts

        sig = float(self.strategy.signal(sig_bars)[-1])
        vol = float(sig_bars.select(
            realized_vol(vol_window, BTConfig().bars_per_year)).to_series()[-1])
        frac = risk.size(sig, vol, now) * self.weight

        price_a = float(bars_a["close"][-1])
        if self.symbol_b:
            bars_b = bars_of(self.symbol_b)
            price_b = float(bars_b["close"][-1])
            lots_a, lots_b = pair_leg_lots(
                frac, self.beta, equity, price_a, price_b,
                bars_of.spec(self.symbol)["contract_size"],
                bars_of.spec(self.symbol_b)["contract_size"])
            self._targets = {self.symbol: lots_a, self.symbol_b: lots_b}
        else:
            spec = bars_of.spec(self.symbol)
            self._targets = {self.symbol: frac * equity / (spec["contract_size"] * price_a)}
        info = {"book": self._name(), "bar_ts": bar_ts, "signal": sig,
                "realized_vol": vol, "weighted_frac": frac,
                "targets": dict(self._targets)}
        return self._targets, info

    def _name(self) -> str:
        leg2 = f"+{self.symbol_b}" if self.symbol_b else ""
        return f"{type(self.strategy).__name__}:{self.symbol}{leg2}"


class _BarCache:
    """One bars/spec fetch per symbol per cycle, shared across books."""

    def __init__(self, gw: MT5Gateway, lookback: int):
        self.gw, self.lookback = gw, lookback
        self._bars: dict = {}
        self._specs: dict = {}

    def __call__(self, symbol: str):
        if symbol not in self._bars:
            self._bars[symbol] = self.gw.bars(symbol, "M1", self.lookback)
        return self._bars[symbol]

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
        self.symbols = sorted({s for b in books for s in (b.symbol, b.symbol_b) if s})
        self._cache = _BarCache(gateway, cfg.lookback_bars)
        self._trail: dict[str, tuple[float, float]] = {}

    def flatten_all(self) -> None:
        for s in self.symbols:
            self.gw.flatten(s)

    def step(self) -> None:
        now = datetime.now(timezone.utc)
        self._cache.new_cycle()
        equity = self.gw.equity()
        self.risk.mark_round_start(now, equity)

        if self.risk.kill(equity, now):
            self.flatten_all()
            self.halted = True
            self._log(event="KILL_SWITCH", equity=equity)
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
            bars = self._cache(sym)
            price = float(bars["close"][-1])
            atr_val = float(bars.select(atr(self.cfg.atr_window)).to_series()[-1] or 0.0)
            sl, tp = protective_levels(lots, price, atr_val,
                                       self.cfg.sl_atr_mult, self.cfg.tp_atr_mult)
            prev_side, prev_sl = self._trail.get(sym, (0.0, 0.0))
            sl = ratchet_sl(lots, sl, prev_side, prev_sl)
            self._trail[sym] = (lots, sl)
            fills[sym] = {"target_lots": lots, "sl": sl, "tp": tp,
                          "price": price,
                          "contract_size": self._cache.spec(sym)["contract_size"],
                          "traded_lots": self.gw.reconcile(sym, lots, sl=sl, tp=tp)}

        self._log(event="STEP", equity=equity, books=infos, fills=fills)

    def run(self) -> None:
        self._log(event="START", books=[b._name() for b in self.books],
                  symbols=self.symbols)
        consec_errors = 0
        while not self.halted:
            try:
                self.step()
                consec_errors = 0
            except Exception as e:
                self._log(event="ERROR", error=repr(e))
                consec_errors += 1
                if consec_errors >= 3:
                    try:
                        self.gw.reconnect()
                        self._log(event="RECONNECT_OK")
                    except Exception as re_err:
                        self._log(event="RECONNECT_FAILED", error=repr(re_err))
                    consec_errors = 0
            time.sleep(self.cfg.poll_seconds)
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
        self.gw.flatten(self.cfg.symbol)
        self.gw.flatten(self.cfg.symbol_b)

    def step(self) -> None:
        from ..strategies.meanrev_pairs import spread_bars

        now = datetime.now(timezone.utc)
        bars_a = self.gw.bars(self.cfg.symbol, "M1", self.cfg.lookback_bars)
        bars_b = self.gw.bars(self.cfg.symbol_b, "M1", self.cfg.lookback_bars)
        spread = spread_bars(bars_a, bars_b, self.cfg.beta)
        bar_ts = spread["ts"][-1]
        if bar_ts == self._last_bar_ts:
            self._heartbeat()
            return
        self._last_bar_ts = bar_ts

        equity = self.gw.equity()
        self.risk.mark_round_start(now, equity)

        if self.risk.kill(equity, now):
            self.flatten_all()
            self.halted = True
            self._log(event="KILL_SWITCH", equity=equity)
            return

        sig = float(self.strategy.signal(spread)[-1])
        vol = float(spread.select(
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
        traded_a = self.gw.reconcile(self.cfg.symbol, lots_a)
        traded_b = self.gw.reconcile(self.cfg.symbol_b, lots_b)

        self._log(event="STEP", bar_ts=bar_ts, equity=equity, signal=sig,
                  realized_vol=vol, target_frac=target_frac,
                  leg_a=self.cfg.symbol, target_lots_a=lots_a, traded_lots_a=traded_a,
                  leg_b=self.cfg.symbol_b, target_lots_b=lots_b, traded_lots_b=traded_b,
                  price_a=price_a, price_b=price_b)
