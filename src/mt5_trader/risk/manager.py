"""Risk management: vol-targeted sizing + daily-reset kill switch.

A single standing posture (config/risk.yaml `posture:`). The kill switch anchors
to each UTC day's start equity, stands down flat on a trip, and auto-resumes the
next UTC day. All times UTC.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from ..features.calendar import BlackoutCfg, blackout_mask, load_events
from ..features.vol_guard import VolGuardCfg, vol_spike_mask


@dataclass
class Posture:
    max_leverage: float
    loss_limit: float      # daily kill: drawdown fraction from day-START equity
    target_vol: float      # annualized vol budget
    give_back: float = 0.0  # daily trailing kill: drawdown fraction from day-PEAK (0 = off)


class RiskManager:
    def __init__(self, posture: Posture,
                 blackout: BlackoutCfg | None = None, events=None,
                 vol_guard: VolGuardCfg | None = None):
        self.posture = posture
        self._blackout = blackout or BlackoutCfg()
        self._events = (events if events is not None
                        else load_events(self._blackout.calendar) if self._blackout.enabled
                        else None)
        self._vol_guard = vol_guard or VolGuardCfg()
        self._day = None                      # UTC date the anchor was set for
        self._day_start_equity: float | None = None
        self._day_peak_equity: float | None = None
        self._halted_day = None               # UTC date the kill tripped (stand down)

    @classmethod
    def from_yaml(cls, path: Path | str) -> "RiskManager":
        with open(path) as f:
            cfg = yaml.safe_load(f)

        p = cfg["posture"]
        posture = Posture(
            max_leverage=float(p["max_leverage"]),
            loss_limit=float(p["loss_limit"]),
            target_vol=float(p["target_vol"]),
            give_back=float(p.get("give_back", 0.0)),
        )

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
        return cls(posture, blackout, vol_guard=vol_guard)

    def in_blackout(self, now: datetime) -> bool:
        """Inside an economic-calendar event window (defensive mute)?"""
        if not self._blackout.enabled:
            return False
        return bool(blackout_mask([now], self._events, self._blackout)[0])

    def vol_halt(self, bars) -> bool:
        """True if the latest bar is an abnormal-volatility spike (fast circuit
        breaker) -> caller forces flat. No-op when vol_guard is disabled."""
        if not self._vol_guard.enabled:
            return False
        return bool(vol_spike_mask(bars, self._vol_guard)[-1])

    def size(self, signal: float, realized_vol_ann: float, now: datetime) -> float:
        """Target position as a leverage multiple of equity (signed).
        Forced flat inside an event blackout window."""
        if self.in_blackout(now):
            return 0.0
        p = self.posture
        lev = min(p.target_vol / max(realized_vol_ann, 1e-4), p.max_leverage)
        return float(signal) * lev

    def roll_day(self, now: datetime, equity: float) -> None:
        """Reset the daily anchor at each new UTC day; otherwise track the peak."""
        d = now.date()
        if d != self._day:
            self._day = d
            self._day_start_equity = equity
            self._day_peak_equity = equity
        else:
            self._day_peak_equity = max(self._day_peak_equity, equity)

    def kill(self, equity: float, now: datetime) -> bool:
        """True -> flatten everything and stand down for the rest of this UTC day.
        Sticky once tripped today; auto-resumes next day (a fresh roll_day clears
        the anchor and the date no longer matches _halted_day). Trips on drawdown
        from day-start (loss_limit) or, when give_back is set, from the day's peak."""
        today = now.date()
        if self._halted_day == today:
            return True
        if self._day_start_equity is None:
            return False
        self._day_peak_equity = max(self._day_peak_equity, equity)
        hit_floor = equity / self._day_start_equity - 1.0 <= -self.posture.loss_limit
        gave_back = (self.posture.give_back > 0
                     and equity / self._day_peak_equity - 1.0 <= -self.posture.give_back)
        if hit_floor or gave_back:
            self._halted_day = today
            return True
        return False
