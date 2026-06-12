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
    log_path: Path = Path("logs/live.jsonl")


class LiveRunner:
    def __init__(self, gateway: MT5Gateway, strategy, risk: RiskManager, cfg: RunnerCfg):
        self.gw = gateway
        self.strategy = strategy
        self.risk = risk
        self.cfg = cfg
        self.halted = False
        self._last_bar_ts = None
        self.cfg.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _log(self, **kw) -> None:
        kw["ts"] = datetime.now(timezone.utc).isoformat()
        with open(self.cfg.log_path, "a") as f:
            f.write(json.dumps(kw, default=str) + "\n")

    def step(self) -> None:
        now = datetime.now(timezone.utc)
        bars = self.gw.bars(self.cfg.symbol, "M1", self.cfg.lookback_bars)
        bar_ts = bars["ts"][-1]
        if bar_ts == self._last_bar_ts:
            return  # no new bar yet
        self._last_bar_ts = bar_ts

        equity = self.gw.equity()
        self.risk.mark_round_start(now, equity)

        if self.risk.kill(equity, now):
            self.gw.flatten(self.cfg.symbol)
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
