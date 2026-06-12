#!/usr/bin/env python
"""Fetch Binance 1m klines into a bars parquet (crypto research data).

    uv run scripts/fetch_binance.py --start 2026-05-12 --end 2026-06-11

Output: data/bars/<SYMBOL>_1m.parquet with the standard bar columns
(plus real volume + taker_buy_ratio — crypto has true traded volume).
Spread is synthesized (klines carry none): --spread-bp of close, default 2.
Backtest with:  uv run scripts/run_backtest.py --bars-file data/bars/BTCUSDT_1m.parquet ...
"""
from __future__ import annotations

import argparse
import io
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path

import polars as pl

BASE = "https://data.binance.vision/data/spot"
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "n_trades", "taker_buy_vol", "taker_buy_quote_vol", "ignore"]


def fetch_zip(url: str) -> pl.DataFrame | None:
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            raw = r.read()
    except Exception:
        return None
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        data = z.read(z.namelist()[0])
    df = pl.read_csv(io.BytesIO(data), has_header=False, new_columns=COLS)
    if df["open_time"].dtype == pl.Utf8:  # some archives ship a header row
        df = df.filter(pl.col("open_time").str.contains(r"^\d+$")).cast({c: pl.Float64 for c in COLS})
    return df


def month_urls(symbol: str, start: date, end: date) -> list[str]:
    urls = []
    m = date(start.year, start.month, 1)
    while m <= end:
        urls.append(f"{BASE}/monthly/klines/{symbol}/1m/{symbol}-1m-{m.year}-{m.month:02d}.zip")
        m = (m.replace(day=28) + timedelta(days=5)).replace(day=1)
    return urls


def day_urls(symbol: str, start: date, end: date) -> list[str]:
    return [f"{BASE}/daily/klines/{symbol}/1m/{symbol}-1m-{start + timedelta(d)}.zip"
            for d in range((end - start).days + 1)]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--start", type=date.fromisoformat, required=True)
    p.add_argument("--end", type=date.fromisoformat, required=True)
    p.add_argument("--spread-bp", type=float, default=2.0)
    p.add_argument("--out", type=Path, default=Path("data/bars"))
    args = p.parse_args()

    frames = []
    for url in month_urls(args.symbol, args.start, args.end):
        df = fetch_zip(url)
        print(f"{'ok' if df is not None else 'miss'}: {url.rsplit('/', 1)[-1]}", flush=True)
        if df is not None:
            frames.append(df)
    # daily files cover what monthly archives don't have yet
    for url in day_urls(args.symbol, max(args.start, date(args.end.year, args.end.month, 1)), args.end):
        df = fetch_zip(url)
        if df is not None:
            frames.append(df)
            print(f"ok: {url.rsplit('/', 1)[-1]}", flush=True)
    if not frames:
        raise SystemExit("nothing downloaded")

    bars = (
        pl.concat(frames)
        .unique(subset="open_time")
        .with_columns(
            # epoch may be ms or us depending on archive vintage
            ts=pl.when(pl.col("open_time") > 1e14)
              .then(pl.from_epoch((pl.col("open_time") // 1000).cast(pl.Int64), time_unit="ms"))
              .otherwise(pl.from_epoch(pl.col("open_time").cast(pl.Int64), time_unit="ms"))
              .dt.replace_time_zone("UTC").dt.cast_time_unit("us"),
        )
        .filter((pl.col("ts").dt.date() >= args.start) & (pl.col("ts").dt.date() <= args.end))
        .sort("ts")
        .select(
            "ts",
            open=pl.col("open"), high=pl.col("high"),
            low=pl.col("low"), close=pl.col("close"),
            spread_mean=pl.col("close") * args.spread_bp * 1e-4,
            n_ticks=pl.col("n_trades"),
            volume=pl.col("volume"),
            taker_buy_ratio=pl.col("taker_buy_vol") / pl.col("volume"),
        )
    )
    args.out.mkdir(parents=True, exist_ok=True)
    dest = args.out / f"{args.symbol}_1m.parquet"
    bars.write_parquet(dest)
    print(f"{len(bars):,} bars -> {dest}")


if __name__ == "__main__":
    main()
