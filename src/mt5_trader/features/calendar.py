"""Economic-calendar blackout: mute trading around scheduled high-impact events.

Gold (XAUUSD) is driven by US real rates / USD, so scheduled macro releases
(FOMC, CPI, PCE, NFP, Powell) produce spread blow-outs, gaps, and spike-reversal
whipsaws that the breakout books are not paid to take. This module is the
defensive overlay: a per-timestamp boolean "are we inside an event window".

No-lookahead by design: scheduled event *times* are known in advance — only the
*outcome* is unknown — so masking bars near a known event time uses no future
information. The same `blackout_mask` primitive feeds both the backtest (mask the
signal array) and live (`RiskManager.in_blackout(now)`), so the two never drift.

Calendar file (`data/calendar/events.{parquet,csv}`) columns:
    ts        event time, UTC
    impact    "high" | "medium" | "low"
    currency  e.g. "USD"           (gold tracks USD)
    title     free text            (e.g. "CPI m/m")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import polars as pl

_IMPACT_RANK = {"low": 0, "medium": 1, "high": 2}
_NS_PER_MIN = 60 * 1_000_000_000

_EMPTY = pl.DataFrame(schema={"ts": pl.Datetime("us"), "impact": pl.Utf8,
                              "currency": pl.Utf8, "title": pl.Utf8})


@dataclass
class BlackoutCfg:
    """Event-mute window. `enabled=False` (or no calendar) => no-op."""
    enabled: bool = False
    calendar: str = "data/calendar/events.parquet"
    before_min: int = 15
    after_min: int = 15
    min_impact: str = "high"
    currencies: list[str] = field(default_factory=lambda: ["USD"])


def load_events(path: str | Path) -> pl.DataFrame:
    """Read the calendar file. Missing file -> empty frame (blackout off)."""
    path = Path(path)
    if not path.exists():
        return _EMPTY
    df = (pl.read_parquet(path) if path.suffix == ".parquet"
          else pl.read_csv(path, try_parse_dates=True))
    return df.with_columns(
        pl.col("ts").cast(pl.Datetime("us")),
        pl.col("impact").cast(pl.Utf8).str.to_lowercase(),
        pl.col("currency").cast(pl.Utf8).str.to_uppercase(),
    )


def blackout_mask(timestamps, events: pl.DataFrame, cfg: BlackoutCfg) -> np.ndarray:
    """Boolean array over `timestamps`: True where a bar falls within
    [event - before_min, event + after_min] of any event passing the impact /
    currency filter. `timestamps` may be a polars Series, list, or array of
    UTC datetimes (pass `[now]` for a single live check)."""
    t = pl.Series(timestamps)
    n = len(t)
    if not cfg.enabled or events.is_empty():
        return np.zeros(n, dtype=bool)

    rank = _IMPACT_RANK.get(cfg.min_impact.lower(), 2)
    keep = np.array([_IMPACT_RANK.get(str(i), 0) >= rank
                     for i in events["impact"]], dtype=bool)
    if cfg.currencies:
        wanted = {c.upper() for c in cfg.currencies}
        keep &= np.array([str(c) in wanted for c in events["currency"]], dtype=bool)
    ev_ns = events["ts"].dt.epoch("ns").to_numpy()[keep]
    if ev_ns.size == 0:
        return np.zeros(n, dtype=bool)

    starts = ev_ns - cfg.before_min * _NS_PER_MIN
    ends = ev_ns + cfg.after_min * _NS_PER_MIN
    tn = t.dt.epoch("ns").to_numpy()
    return ((tn[:, None] >= starts[None, :]) & (tn[:, None] <= ends[None, :])).any(axis=1)
