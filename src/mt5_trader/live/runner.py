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
from ..features.technicals import realized_vol
from ..risk.manager import RiskManager
from .mt5_gateway import MT5Gateway


@dataclass
class RunnerCfg:
    symbol: str
    lookback_bars: int = 3000
    vol_window: int = 240
    poll_seconds: float = 5.0
    heartbeat_seconds: float = 60.0
    log_path: Path = Path("logs/live.jsonl")


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
        delta = self.gw.reconcile(self.cfg.symbol, target_lots)

        self._log(event="STEP", bar_ts=bar_ts, equity=equity, signal=sig,
                  realized_vol=vol, target_frac=target_frac,
                  target_lots=target_lots, traded_lots=delta, price=price)

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
