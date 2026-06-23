"""Disk-backed bar cache: build once, reuse across runs, auto-invalidate when the
source ticks change. Speeds up param sweeps that rebuild bars from ~100M ticks per
cell (e.g. scripts/research_macro_gate.py)."""
from datetime import datetime, timedelta, timezone

import polars as pl

from mt5_trader.pipeline.data import cached_bars


def _write_ticks(path, n, t0=datetime(2026, 5, 12, tzinfo=timezone.utc)):
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "ts": [t0 + timedelta(seconds=i) for i in range(n)],
        "symbol": ["XAUUSD"] * n,
        "bid": [4000.0 + i * 0.01 for i in range(n)],
        "ask": [4000.1 + i * 0.01 for i in range(n)],
    }).write_parquet(path)


def test_cached_bars_writes_then_reuses(tmp_path):
    data = tmp_path / "data"
    _write_ticks(data / "XAUUSD" / "d1.parquet", 120)   # 2 min of 1s ticks
    cache = tmp_path / "cache"

    first = cached_bars("XAUUSD", "1m", data_dir=data, cache_dir=cache, drop_weekend=False)
    assert first.height == 2                              # two 1-min bars
    cache_files = list(cache.glob("XAUUSD_1m_*.parquet"))
    assert len(cache_files) == 1                          # cache file written

    # overwrite the cache with a sentinel; the 2nd call (source unchanged -> same
    # key) must return the sentinel, proving it read the cache rather than rebuilt
    pl.DataFrame({"ts": [datetime(2030, 1, 1, tzinfo=timezone.utc)], "close": [-1.0]}) \
        .write_parquet(cache_files[0])
    second = cached_bars("XAUUSD", "1m", data_dir=data, cache_dir=cache, drop_weekend=False)
    assert second["close"].to_list() == [-1.0]


def test_cached_bars_invalidates_on_new_source(tmp_path):
    data = tmp_path / "data"
    _write_ticks(data / "XAUUSD" / "d1.parquet", 120)
    cache = tmp_path / "cache"
    first = cached_bars("XAUUSD", "1m", data_dir=data, cache_dir=cache, drop_weekend=False)
    assert first.height == 2

    # add a later day of ticks (newer mtime) -> cache must rebuild with more bars
    import time
    time.sleep(1.1)   # ensure a strictly greater integer mtime
    _write_ticks(data / "XAUUSD" / "d2.parquet", 120,
                 t0=datetime(2026, 5, 13, tzinfo=timezone.utc))
    second = cached_bars("XAUUSD", "1m", data_dir=data, cache_dir=cache, drop_weekend=False)
    assert second.height == 4                             # both days now
    # only the latest cache version is kept (self-cleaning)
    assert len(list(cache.glob("XAUUSD_1m_*.parquet"))) == 1
