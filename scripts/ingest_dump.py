#!/usr/bin/env python
"""Ingest the historical tick dump: CSV dir -> per-symbol parquet.

    uv run scripts/ingest_dump.py data/raw --out data/processed
"""
import argparse
from pathlib import Path

from mt5_trader.data.ingest import ingest_csv_dir, load_schema_map


def main():
    p = argparse.ArgumentParser()
    p.add_argument("csv_dir", type=Path)            # also used as the parquet dump dir
    p.add_argument("--out", type=Path, default=Path("data/processed"))
    p.add_argument("--schema-map", type=Path, default=Path("config/schema_map.yaml"))
    p.add_argument("--parquet", action="store_true", help="dump is per-symbol parquet, not CSV")
    p.add_argument("--symbols", nargs="+", default=["EURUSD", "XAUUSD"])
    args = p.parse_args()
    schema_map = load_schema_map(args.schema_map) if args.schema_map.exists() else None
    if args.parquet:
        from mt5_trader.data.ingest import ingest_parquet_dump
        written = ingest_parquet_dump(args.csv_dir, args.out, args.symbols, schema_map)
    else:
        written = ingest_csv_dir(args.csv_dir, args.out, schema_map)
    print(f"wrote {len(written)} parquet files to {args.out}")


if __name__ == "__main__":
    main()
