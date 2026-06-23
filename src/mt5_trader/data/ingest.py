"""Vendor CSV dump -> canonical per-symbol parquet.

Vendor schema is TBD. When it drops, edit config/schema_map.yaml
(vendor column -> canonical column) and re-run scripts/ingest_dump.py.
Timestamps are assumed UTC.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import polars as pl
import yaml

from .schema import ALL_COLUMNS, CORE_COLUMNS


def load_schema_map(path: Path | str) -> dict[str, str]:
    """Returns {vendor_column: canonical_column}."""
    with open(path) as f:
        return yaml.safe_load(f)["columns"]


def _parse_ts(df: pl.DataFrame) -> pl.DataFrame:
    dtype = df.schema["ts"]
    if dtype == pl.Utf8:
        try:  # strings carrying a tz offset / Z suffix
            expr = pl.col("ts").str.to_datetime(time_unit="us", time_zone="UTC")
            return df.with_columns(expr.alias("ts"))
        except pl.exceptions.ComputeError:  # naive strings, assume UTC
            expr = pl.col("ts").str.to_datetime(time_unit="us").dt.replace_time_zone("UTC")
    elif dtype in (pl.Int64, pl.UInt64, pl.Int32):
        sample = df["ts"].drop_nulls()[0]
        unit = "ns" if sample > 1e17 else "us" if sample > 1e14 else "ms" if sample > 1e11 else "s"
        expr = pl.from_epoch("ts", time_unit=unit).dt.replace_time_zone("UTC").dt.cast_time_unit("us")
    elif isinstance(dtype, pl.Datetime):
        expr = pl.col("ts").dt.cast_time_unit("us")
        if dtype.time_zone is None:
            expr = expr.dt.replace_time_zone("UTC")
    else:
        raise ValueError(f"unsupported ts dtype: {dtype}")
    return df.with_columns(expr.alias("ts"))


def normalise(df: pl.DataFrame, schema_map: dict[str, str] | None = None) -> pl.DataFrame:
    """Rename vendor columns to canonical, parse ts, keep known columns, sort."""
    if schema_map:
        df = df.rename({k: v for k, v in schema_map.items() if k in df.columns})
    if "ts" not in df.columns:
        raise ValueError("no 'ts' column after renaming — fix config/schema_map.yaml")
    df = _parse_ts(df)
    keep = [c for c in ALL_COLUMNS if c in df.columns]
    return df.select(keep).sort("ts")


def ingest_parquet_dump(dump_dir: Path | str, out_dir: Path | str,
                        symbols: list[str], schema_map: dict[str, str] | None = None,
                        ) -> list[Path]:
    """Per-symbol-per-day parquet dump (cols `time`,`sym`,`bid`,`ask`,L2 lists) ->
    canonical `out_dir/<SYM>/<stem>.parquet`. Reads only the columns we map +
    bid/ask so the 20GB L2 ladder never loads."""
    dump_dir, out_dir = Path(dump_dir), Path(out_dir)
    schema_map = schema_map or {}
    cols = list(schema_map) + ["bid", "ask"]
    written: list[Path] = []
    for sym in symbols:
        for f in sorted(dump_dir.glob(f"{sym}_*.parquet")):
            present = [c for c in cols if c in pl.scan_parquet(f).collect_schema().names()]
            df = normalise(pl.read_parquet(f, columns=present), schema_map)
            dest = out_dir / sym
            dest.mkdir(parents=True, exist_ok=True)
            path = dest / f"{f.stem}.parquet"
            df.write_parquet(path)
            written.append(path)
    return written


def ingest_csv_dir(csv_dir: Path | str, out_dir: Path | str,
                   schema_map: dict[str, str] | None = None) -> list[Path]:
    """Read every CSV in csv_dir, write data/processed/<SYMBOL>/<file>.parquet."""
    csv_dir, out_dir = Path(csv_dir), Path(out_dir)
    written: list[Path] = []
    for f in sorted(csv_dir.glob("*.csv")):
        df = normalise(pl.read_csv(f, infer_schema_length=10_000), schema_map)
        for (symbol,), part in df.partition_by("symbol", as_dict=True).items():
            dest = out_dir / str(symbol)
            dest.mkdir(parents=True, exist_ok=True)
            path = dest / f"{f.stem}.parquet"
            part.write_parquet(path)
            written.append(path)
    return written


def drop_market_closed(frame, ts_col: str = "ts",
                       fri_close_hour: int = 21, sun_open_hour: int = 22):
    """Strip the spot-metals weekend (UTC) from a tick/bar frame (lazy or eager).

    The broker's real session closes Fri 21:00 UTC and reopens Sun 22:00 (data/real
    confirms: every historical Friday ends 21:00:00 sharp). During the close the
    LIVE feed streams SYNTHETIC, live-timestamped ticks that LOOP the last real hour
    — junk that pollutes bars/backtests. Closed = Fri >= fri_close_hour, all Sat,
    Sun < sun_open_hour. Pass a smaller fri_close_hour when a capture stalled before
    the real close (e.g. the 2026-06-19 18:00 terminal failure)."""
    wd, h = pl.col(ts_col).dt.weekday(), pl.col(ts_col).dt.hour()
    closed = (((wd == 5) & (h >= fri_close_hour))
              | (wd == 6)
              | ((wd == 7) & (h < sun_open_hour)))
    return frame.filter(~closed)


def load_ticks(processed_dir: Path | str | list, symbol: str,
               start: datetime | None = None, end: datetime | None = None,
               drop_weekend: bool = True) -> pl.DataFrame:
    """Load a symbol's canonical ticks from one or many source dirs, merged on
    time. Pass a list to combine feeds (e.g. real historical + live capture):
        load_ticks(["data/real", "data/ticks"], "XAUUSD")
    Each source is projected to CORE_COLUMNS before the merge, so a feed whose
    files carry extra columns (the live logger writes a `volume` col) still
    concats cleanly into one schema. `drop_weekend` (default on) strips the
    market-closed window — see drop_market_closed; pass False for raw capture."""
    dirs = processed_dir if isinstance(processed_dir, (list, tuple)) else [processed_dir]
    lfs = [pl.scan_parquet(Path(d) / symbol / "*.parquet")
           for d in dirs if (Path(d) / symbol).exists()]
    if not lfs:
        raise FileNotFoundError(f"no tick parquet for {symbol} under {[str(d) for d in dirs]}")
    # Uniform schema across feeds: keep CORE columns present in EVERY source, so a
    # thin feed (ts,symbol,bid,ask) and a thick one (+bid_sz/ask_sz) concat cleanly.
    names = [set(lf.collect_schema().names()) for lf in lfs]
    common = [c for c in CORE_COLUMNS if all(c in n for n in names)]
    lf = pl.concat([lf.select(common) for lf in lfs], how="vertical")
    if start is not None:
        lf = lf.filter(pl.col("ts") >= start)
    if end is not None:
        lf = lf.filter(pl.col("ts") < end)
    if drop_weekend:
        lf = drop_market_closed(lf)
    return lf.sort("ts").collect()
