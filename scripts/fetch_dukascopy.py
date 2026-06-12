#!/usr/bin/env python
"""Download Dukascopy free tick data into canonical parquet.

Research stand-in until the competition dump arrives: real tick-level
bid/ask + L1 volumes. OFI works (needs only the best level); 5-level
depth_imbalance won't exist until the real dump.

    uv run scripts/fetch_dukascopy.py --start 2026-05-12 --end 2026-06-11
"""
from __future__ import annotations

import argparse
import lzma
import struct
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import polars as pl

URL = "https://datafeed.dukascopy.com/datafeed/{sym}/{y}/{m:02d}/{d:02d}/{h:02d}h_ticks.bi5"
# Dukascopy price ints: metals 3 decimals, these FX pairs 5, BTC 1
SCALE = {"XAUUSD": 1e3, "XAGUSD": 1e3, "EURUSD": 1e5, "GBPUSD": 1e5, "EURGBP": 1e5,
         "BTCUSD": 10.0}
REC = struct.Struct(">IIIff")  # ms_offset, ask, bid, ask_vol, bid_vol


def fetch_hour(sym: str, day: date, hour: int) -> list[tuple]:
    # Dukascopy months are 0-indexed
    url = URL.format(sym=sym, y=day.year, m=day.month - 1, d=day.day, h=hour)
    for _ in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                raw = r.read()
            break
        except Exception:
            raw = None
    if not raw:
        return []
    try:
        data = lzma.decompress(raw)
    except lzma.LZMAError:
        return []
    base = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
    scale = SCALE[sym]
    return [
        (base + timedelta(milliseconds=ms), bid / scale, ask / scale, bv, av)
        for ms, ask, bid, av, bv in REC.iter_unpack(data)
    ]


def fetch_day(sym: str, day: date, pool: ThreadPoolExecutor) -> pl.DataFrame | None:
    rows: list[tuple] = []
    for hour_rows in pool.map(lambda h: fetch_hour(sym, day, h), range(24)):
        rows.extend(hour_rows)
    if not rows:
        return None
    ts, bid, ask, bid_sz, ask_sz = zip(*rows)
    return pl.DataFrame({
        "ts": ts, "symbol": sym, "bid": bid, "ask": ask,
        "bid_sz": bid_sz, "ask_sz": ask_sz,
    }).with_columns(pl.col("ts").dt.cast_time_unit("us")).sort("ts")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=date.fromisoformat, required=True)
    p.add_argument("--end", type=date.fromisoformat, required=True)
    p.add_argument("--symbols", nargs="+", default=list(SCALE))
    p.add_argument("--out", type=Path, default=Path("data/processed"))
    args = p.parse_args()

    with ThreadPoolExecutor(max_workers=12) as pool:
        for sym in args.symbols:
            dest = args.out / sym
            dest.mkdir(parents=True, exist_ok=True)
            day = args.start
            while day <= args.end:
                path = dest / f"duka_{day.isoformat()}.parquet"
                if not path.exists():
                    df = fetch_day(sym, day, pool)
                    if df is not None:
                        df.write_parquet(path)
                        print(f"{sym} {day}: {len(df):>8,} ticks", flush=True)
                    else:
                        print(f"{sym} {day}: market closed / no data", flush=True)
                day += timedelta(days=1)
    print("done")


if __name__ == "__main__":
    main()
