"""Shared backtest-data helpers: build bars from ticks, annualize a bar size,
and a deterministic synthetic fallback. One home for the build chain that was
duplicated in scripts/run_backtest.py and scripts/validate_books.py.

`synthetic_bars` lets `diagnose`/`validate` run before the real tick dump lands
(and backs the unit tests) — same columns the engine + strategies expect.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import polars as pl

from ..data.bars import time_bars
from ..data.ingest import load_ticks
from ..features.microstructure import with_micro_features

# Real broker feeds, merged: historical capture (data/real) + live tick logger
# (data/ticks). These carry the TRUE competition spread (~0.3bp vs Dukascopy's
# ~1.4bp in data/processed), so backtests cost against the real book. Pass an
# explicit data_dir / --data to override (e.g. [Path("data/processed")]).
DEFAULT_DATA = [Path("data/real"), Path("data/ticks")]

_EVERY_MINUTES = {"m": 1, "h": 60}


def bars_per_year(every: str) -> float:
    """Annualization factor for a bar size like '5m' or '1h'."""
    unit, n = every[-1].lower(), float(every[:-1])
    return 365 * 24 * 60 / (_EVERY_MINUTES[unit] * n)


def build_bars(symbol: str, every: str = "5m",
               data_dir: Path | str = DEFAULT_DATA) -> pl.DataFrame:
    """Ticks → micro features → OHLC bars with the OFI aggregation."""
    ticks = with_micro_features(load_ticks(data_dir, symbol))
    return time_bars(ticks, every, extra={"ofi": pl.col("ofi").sum()})


def cached_bars(symbol: str, every: str = "5m", data_dir: Path | str = DEFAULT_DATA,
                drop_weekend: bool = True,
                cache_dir: Path | str = "data/bars_cache") -> pl.DataFrame:
    """Plain OHLC bars (time_bars on load_ticks), disk-cached so a param sweep
    doesn't rebuild from ~100M ticks every call. The cache key includes the max
    source-file mtime, so re-ingesting ticks auto-invalidates it; older versions
    for the same key are removed on rebuild (self-cleaning). No micro features —
    use build_bars() if you need the OFI aggregation."""
    dirs = data_dir if isinstance(data_dir, (list, tuple)) else [data_dir]
    src = [p for d in dirs for p in (Path(d) / symbol).glob("*.parquet")]
    if not src:
        raise FileNotFoundError(f"no tick parquet for {symbol} under {[str(d) for d in dirs]}")
    mtime = max(int(p.stat().st_mtime) for p in src)
    cache_dir = Path(cache_dir)
    key = f"{symbol}_{every}_dw{int(drop_weekend)}"
    target = cache_dir / f"{key}_{mtime}.parquet"
    if target.exists():
        return pl.read_parquet(target)
    bars = time_bars(load_ticks(data_dir, symbol, drop_weekend=drop_weekend), every)
    cache_dir.mkdir(parents=True, exist_ok=True)
    for old in cache_dir.glob(f"{key}_*.parquet"):
        old.unlink()
    bars.write_parquet(target)
    return bars


def synthetic_bars(n: int = 8000, every: str = "5m", seed: int = 0,
                   start: datetime | None = None) -> pl.DataFrame:
    """Deterministic random-walk bars for tests and for running the pipeline
    before real data exists. ~28 days of 5m bars by default (enough for
    week-by-week / hold-out splits). Columns: ts, open/high/low/close,
    spread_mean, n_ticks, ofi."""
    rng = np.random.default_rng(seed)
    close = 2000.0 * np.cumprod(1.0 + rng.normal(0, 0.0006, n))
    step = timedelta(minutes=_EVERY_MINUTES[every[-1].lower()] * float(every[:-1]))
    t0 = start or datetime(2026, 5, 1, tzinfo=timezone.utc)
    return pl.DataFrame({
        "ts": [t0 + step * i for i in range(n)],
        "open": close,
        "high": close * (1 + rng.uniform(0, 0.0004, n)),
        "low": close * (1 - rng.uniform(0, 0.0004, n)),
        "close": close,
        "spread_mean": np.full(n, 0.02),
        "n_ticks": np.full(n, 50, dtype=np.int64),
        "ofi": rng.normal(0, 100, n),
    })


def load_or_synthetic(symbol: str, every: str = "5m",
                      data_dir: Path | str = DEFAULT_DATA) -> tuple[pl.DataFrame, bool]:
    """Real bars if the tick dump exists, else synthetic. Returns (bars, is_real)
    so callers can warn that results are illustrative, not tradeable."""
    dirs = data_dir if isinstance(data_dir, (list, tuple)) else [data_dir]
    if any((Path(d) / symbol).exists() for d in dirs):
        return build_bars(symbol, every, data_dir), True
    return synthetic_bars(every=every), False
