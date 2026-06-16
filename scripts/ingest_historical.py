#!/usr/bin/env python
"""Convert the real multi-provider depth dump -> canonical tick parquet.

Vendor file = one SYMBOL_YYYY_MM_DD.parquet per symbol/day, with several rows
per instant (one per liquidity provider) and depth ladders as list columns:
    time, sym, provider, valuedate, received, bid, ask,
    bidprices:list, bidsizes:list, askprices:list, asksizes:list

We emit canonical best bid/ask (+ best size from the ladder head) AND keep the
full depth ladders as list columns (bidprices/bidsizes/askprices/asksizes), so
nothing is lost -- load_ticks projects to CORE_COLUMNS for backtests, while the
notebook can read the ladders straight off these files. Every provider row stays
its own tick; time_bars aggregates them (mid robust, per-bar spread a blend
across providers). Streamed with sink_parquet so the big files never load fully
into memory.

    uv run scripts/ingest_historical.py "/Volumes/SK Storage-1/Trading/2026-05-11_2026-06-10" \\
        --symbols XAUUSD XAGUSD --out data/real
"""
import argparse
from pathlib import Path

import polars as pl


def convert(src: Path, out: Path, symbols: list[str]) -> list[Path]:
    written: list[Path] = []
    for sym in symbols:
        files = sorted(src.glob(f"{sym}_*.parquet"))
        if not files:
            print(f"  {sym}: no files in {src}")
            continue
        dest = out / sym
        dest.mkdir(parents=True, exist_ok=True)
        for f in files:
            canonical = (
                pl.scan_parquet(f)
                .select(
                    ts=pl.col("time").str.to_datetime(
                        format="%Y-%m-%d %H:%M:%S%.f", time_unit="us"
                    ).dt.replace_time_zone("UTC"),
                    symbol=pl.col("sym"),
                    bid=pl.col("bid"),
                    ask=pl.col("ask"),
                    bid_sz=pl.col("bidsizes").list.first().cast(pl.Float64),
                    ask_sz=pl.col("asksizes").list.first().cast(pl.Float64),
                    bidprices=pl.col("bidprices"),
                    bidsizes=pl.col("bidsizes"),
                    askprices=pl.col("askprices"),
                    asksizes=pl.col("asksizes"),
                )
                .sort("ts")
            )
            target = dest / f"{f.stem}.parquet"
            canonical.sink_parquet(target)
            written.append(target)
        print(f"  {sym}: {len(files)} files -> {dest}")
    return written


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("src", type=Path, help="vendor dump dir (SYMBOL_DATE.parquet)")
    p.add_argument("--symbols", nargs="+", default=["XAUUSD", "XAGUSD"])
    p.add_argument("--out", type=Path, default=Path("data/real"))
    args = p.parse_args()
    written = convert(args.src, args.out, args.symbols)
    print(f"wrote {len(written)} parquet files to {args.out}")


if __name__ == "__main__":
    main()
