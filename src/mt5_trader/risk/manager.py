"""Round-aware risk management: vol-targeted sizing + loss kill switch.

Tournament logic lives in config/risk.yaml: moderate risk while the goal is
surviving a cutoff (rounds 1-2), higher vol budget when ranking matters
(round 3). All times UTC.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml


@dataclass
class RoundCfg:
    name: str
    start: datetime | None
    end: datetime | None
    max_leverage: float
    loss_limit: float      # kill switch: fraction of round-start equity
    target_vol: float      # annualized vol budget


def _parse_dt(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


class RiskManager:
    def __init__(self, rounds: list[RoundCfg], default: RoundCfg):
        self.rounds = rounds
        self.default = default
        self._round_start_equity: dict[str, float] = {}

    @classmethod
    def from_yaml(cls, path: Path | str) -> "RiskManager":
        with open(path) as f:
            cfg = yaml.safe_load(f)

        def mk(d: dict, name: str) -> RoundCfg:
            return RoundCfg(
                name=d.get("name", name),
                start=_parse_dt(d.get("start")),
                end=_parse_dt(d.get("end")),
                max_leverage=float(d["max_leverage"]),
                loss_limit=float(d["loss_limit"]),
                target_vol=float(d["target_vol"]),
            )

        default = mk(cfg["default"], "default")
        rounds = [mk(r, f"round{i}") for i, r in enumerate(cfg.get("rounds", []), 1)]
        return cls(rounds, default)

    def round_at(self, now: datetime) -> RoundCfg:
        for r in self.rounds:
            if r.start and r.end and r.start <= now < r.end:
                return r
        return self.default

    def size(self, signal: float, realized_vol_ann: float, now: datetime) -> float:
        """Target position as a leverage multiple of equity (signed)."""
        r = self.round_at(now)
        lev = min(r.target_vol / max(realized_vol_ann, 1e-4), r.max_leverage)
        return float(signal) * lev

    def mark_round_start(self, now: datetime, equity: float) -> None:
        r = self.round_at(now)
        self._round_start_equity.setdefault(r.name, equity)

    def kill(self, equity: float, now: datetime) -> bool:
        """True -> flatten everything and stop trading for this round."""
        r = self.round_at(now)
        start_eq = self._round_start_equity.get(r.name)
        if start_eq is None:
            return False
        return equity / start_eq - 1.0 <= -r.loss_limit
