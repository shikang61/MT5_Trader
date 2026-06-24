#!/usr/bin/env python
"""Capture the broker's live tick stream to parquet (Windows VPS, separate process).

The trading bots only log per-bar decision snapshots — they throw the raw ticks
away. This pulls the terminal's tick history (mt5.copy_ticks_range) on an
interval and appends canonical parquet, so the competition broker's REAL
bid/ask + spreads (unrecoverable later — Dukascopy is a different feed) become
backtest data. Runs as its own process so a logger bug can't touch the trading
loop; it only reads, never trades.

    python scripts/log_ticks.py                       # symbols from portfolio.yaml
    python scripts/log_ticks.py --symbols XAUUSD XAGUSD --flush-seconds 300

Output: data/ticks/<SYMBOL>/<UTC-date>_<ms>.parquet  (many small files, one per
flush; load with load_ticks("data/ticks", "XAUUSD") — same schema as Dukascopy).
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import yaml
from dotenv import load_dotenv

from mt5_trader.data.ingest import drop_market_closed
from mt5_trader.live.mt5_gateway import MT5Gateway
from mt5_trader.live.runner import resilient_loop
from mt5_trader.live.single_instance import acquire_or_exit

OUT = Path("data/ticks")
LOG = Path("logs/log_ticks.jsonl")


def portfolio_symbols(path: Path) -> list[str]:
    """Every symbol any book trades, so the capture covers the live universe."""
    with open(path) as f:
        books = yaml.safe_load(f)["books"]
    syms = {b["symbol"] for b in books} | {b.get("symbol2") for b in books}
    return sorted(s for s in syms if s)


def resolve_symbols(portfolio: list[str], override: list[str] | None,
                    extra: list[str] | None) -> list[str]:
    """--symbols fully overrides; otherwise the portfolio's traded symbols PLUS any
    --extra-symbols — research-only captures no book trades yet (e.g. GBPUSD for the
    diversifier study), so the gold/silver capture keeps auto-following the portfolio
    while extra pairs ride along. Deduped + sorted."""
    if override:
        return sorted(set(override))
    return sorted(set(portfolio) | set(extra or []))


def log(**kw) -> None:
    kw["ts"] = datetime.now(timezone.utc).isoformat()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps(kw, default=str) + "\n")


def last_cursor(symbol: str, lookback_min: int) -> datetime:
    """Resume after the newest tick already on disk; else start lookback_min ago
    (a small backfill from the terminal's retained history)."""
    files = list((OUT / symbol).glob("*.parquet"))
    if files:
        mx = pl.scan_parquet(OUT / symbol / "*.parquet").select(pl.col("ts").max()).collect().item()
        if mx is not None:
            return mx.replace(tzinfo=timezone.utc) if mx.tzinfo is None else mx
    return datetime.now(timezone.utc) - timedelta(minutes=lookback_min)


def write_ticks(df: pl.DataFrame, symbol: str) -> None:
    """One parquet per UTC date in the batch (a flush can straddle midnight)."""
    dest = OUT / symbol
    dest.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time() * 1000)
    for (d,), part in df.with_columns(d=pl.col("ts").dt.date()).partition_by(
            "d", as_dict=True).items():
        part.drop("d").write_parquet(dest / f"{d}_{stamp}.parquet")


def main():
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="+", help="default: all symbols in portfolio.yaml")
    p.add_argument("--extra-symbols", nargs="+",
                   help="extra symbols to capture beyond the portfolio's (research-only, untraded)")
    p.add_argument("--portfolio", type=Path, default=Path("config/portfolio.yaml"))
    p.add_argument("--flush-seconds", type=float, default=300.0)
    p.add_argument("--overlap-seconds", type=float, default=120.0,
                   help="re-request window before the cursor (dedup'd) so a flush gap loses nothing")
    p.add_argument("--lookback-min", type=int, default=60, help="initial backfill on a cold start")
    args = p.parse_args()

    symbols = resolve_symbols(portfolio_symbols(args.portfolio), args.symbols, args.extra_symbols)
    print(f"[log_ticks] tick capture | symbols={','.join(symbols)} "
          f"flush={args.flush_seconds:g}s -> {OUT}", flush=True)
    _lock = acquire_or_exit("log_ticks")
    import os
    gw = MT5Gateway()
    gw.connect(login=int(os.environ["MT5_LOGIN"]), password=os.environ["MT5_PASSWORD"],
               server=os.environ["MT5_SERVER"])
    cursors = {s: last_cursor(s, args.lookback_min) for s in symbols}
    totals = {s: 0 for s in symbols}
    log(event="START", symbols=symbols, cursors=cursors, flush_seconds=args.flush_seconds)

    def step() -> None:
        now = datetime.now(timezone.utc)
        for sym in symbols:
            df = gw.ticks_range(sym, cursors[sym] - timedelta(seconds=args.overlap_seconds), now)
            if not df.is_empty():
                df = df.filter(pl.col("ts") > cursors[sym])
            # Skip synthetic looped ticks the broker streams while the market is
            # closed (Fri 21:00 -> Sun 22:00 UTC) — they'd pollute the capture and,
            # by advancing the cursor, mask the real Sunday reopen. Cursor stays at
            # the Friday close until genuine ticks arrive.
            df = drop_market_closed(df)
            if df.is_empty():
                continue
            write_ticks(df, sym)
            cursors[sym] = df["ts"].max()
            totals[sym] += len(df)
            # ts span + UTC clock on each flush so server-tz alignment is verifiable
            log(event="FLUSH", symbol=sym, new_ticks=len(df), total=totals[sym],
                first=str(df["ts"][0]), last=str(df["ts"][-1]), now_utc=str(now))

    try:
        resilient_loop(step, log, gw.reconnect, args.flush_seconds, lambda: False)
    finally:
        gw.shutdown()


if __name__ == "__main__":
    main()
