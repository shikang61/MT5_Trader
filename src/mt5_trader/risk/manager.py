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

from ..features.calendar import BlackoutCfg, blackout_mask, load_events
from ..features.vol_guard import VolGuardCfg, vol_spike_mask


@dataclass
class RoundCfg:
    name: str
    start: datetime | None
    end: datetime | None
    max_leverage: float
    loss_limit: float      # kill switch: drawdown fraction from round-START equity
    target_vol: float      # annualized vol budget
    give_back: float = 0.0  # kill switch: drawdown fraction from round PEAK (0 = off)


def _parse_dt(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


class RiskManager:
    def __init__(self, rounds: list[RoundCfg], default: RoundCfg,
                 blackout: BlackoutCfg | None = None, events=None,
                 vol_guard: VolGuardCfg | None = None):
        self.rounds = rounds
        self.default = default
        self._blackout = blackout or BlackoutCfg()
        self._events = (events if events is not None
                        else load_events(self._blackout.calendar) if self._blackout.enabled
                        else None)
        self._vol_guard = vol_guard or VolGuardCfg()
        self._round_start_equity: dict[str, float] = {}
        self._round_peak_equity: dict[str, float] = {}

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
                give_back=float(d.get("give_back", 0.0)),
            )

        default = mk(cfg["default"], "default")
        rounds = [mk(r, f"round{i}") for i, r in enumerate(cfg.get("rounds", []), 1)]

        b = cfg.get("blackout") or {}
        blackout = BlackoutCfg(
            enabled=bool(b.get("enabled", False)),
            calendar=b.get("calendar", BlackoutCfg.calendar),
            before_min=int(b.get("before_min", BlackoutCfg.before_min)),
            after_min=int(b.get("after_min", BlackoutCfg.after_min)),
            min_impact=str(b.get("min_impact", BlackoutCfg.min_impact)),
            currencies=list(b.get("currencies", ["USD"])),
        )

        vg = cfg.get("vol_guard") or {}
        vol_guard = VolGuardCfg(
            enabled=bool(vg.get("enabled", False)),
            atr_n=int(vg.get("atr_n", VolGuardCfg.atr_n)),
            atr_base_n=int(vg.get("atr_base_n", VolGuardCfg.atr_base_n)),
            atr_mult=float(vg.get("atr_mult", VolGuardCfg.atr_mult)),
            ret_limit_bps=float(vg.get("ret_limit_bps", VolGuardCfg.ret_limit_bps)),
        )
        return cls(rounds, default, blackout, vol_guard=vol_guard)

    def round_at(self, now: datetime) -> RoundCfg:
        for r in self.rounds:
            if r.start and r.end and r.start <= now < r.end:
                return r
        return self.default

    def in_blackout(self, now: datetime) -> bool:
        """Inside an economic-calendar event window (defensive mute)?"""
        if not self._blackout.enabled:
            return False
        return bool(blackout_mask([now], self._events, self._blackout)[0])

    def vol_halt(self, bars) -> bool:
        """True if the latest bar is an abnormal-volatility spike (fast circuit
        breaker) -> caller forces flat. No-op when vol_guard is disabled. Takes
        bars (not `now`) because the trip is price-derived, not time-derived."""
        if not self._vol_guard.enabled:
            return False
        return bool(vol_spike_mask(bars, self._vol_guard)[-1])

    def size(self, signal: float, realized_vol_ann: float, now: datetime) -> float:
        """Target position as a leverage multiple of equity (signed).
        Forced flat inside an event blackout window."""
        if self.in_blackout(now):
            return 0.0
        r = self.round_at(now)
        lev = min(r.target_vol / max(realized_vol_ann, 1e-4), r.max_leverage)
        return float(signal) * lev

    def mark_round_start(self, now: datetime, equity: float) -> None:
        r = self.round_at(now)
        self._round_start_equity.setdefault(r.name, equity)
        self._round_peak_equity.setdefault(r.name, equity)

    def kill(self, equity: float, now: datetime) -> bool:
        """True -> flatten everything and stop trading for this round. Trips on
        either drawdown from round-start (loss_limit) or, once give_back is set,
        drawdown from the round's peak equity (protects an intra-round lead)."""
        r = self.round_at(now)
        start_eq = self._round_start_equity.get(r.name)
        if start_eq is None:
            return False
        peak = max(self._round_peak_equity.get(r.name, start_eq), equity)
        self._round_peak_equity[r.name] = peak
        hit_floor = equity / start_eq - 1.0 <= -r.loss_limit
        gave_back = r.give_back > 0 and equity / peak - 1.0 <= -r.give_back
        return hit_floor or gave_back
