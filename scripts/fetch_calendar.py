#!/usr/bin/env python
"""Fetch the ForexFactory economic calendar -> data/calendar/events.parquet.

Uses ForexFactory's own JSON feed (faireconomy.media) instead of scraping the
Cloudflare-protected HTML page: same data, stable, polite. The feed publishes
the *current week* only, so run it once per week (a cron) — each run merges and
de-dupes into the parquet, accumulating history going forward. Past weeks before
you started pulling (e.g. the backtest window) need a different source; see
data/calendar/README.md.

    uv run scripts/fetch_calendar.py                      # high-impact USD (default)
    uv run scripts/fetch_calendar.py --impact medium --currency USD EUR

Be polite: at most one pull per day — the endpoint rate-limits (HTTP 429).
"""
from __future__ import annotations

import argparse
import json
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
OUT = Path("data/calendar/events.parquet")
RANK = {"low": 0, "medium": 1, "high": 2}

# Windows Python's ssl can't always build the feed's cert chain (no system CA
# integration), so verification fails there; certifi's CA bundle fixes it. Fall
# back to the default context where certifi isn't installed (e.g. macOS, where
# verification already works). Never disable verification — this feeds live risk.
try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ModuleNotFoundError:
    _SSL_CTX = ssl.create_default_context()


def fetch(url: str, retries: int = 3, backoff: int = 20) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (calendar-fetch)"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                print(f"  429 rate-limited; backing off {backoff}s "
                      f"({attempt + 1}/{retries - 1})")
                time.sleep(backoff)
                continue
            raise


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--impact", default="high", choices=["low", "medium", "high"],
                   help="keep this impact level and above")
    p.add_argument("--currency", nargs="+", default=["USD"],
                   help="currencies to keep (USD = US events)")
    args = p.parse_args()
    floor = RANK[args.impact]
    wanted = {c.upper() for c in args.currency}

    rows = []
    for e in fetch(FEED):
        imp = str(e.get("impact", "")).lower()
        ccy = str(e.get("country", "")).upper()
        if RANK.get(imp, -1) < floor or ccy not in wanted:
            continue
        ts = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
        rows.append({"ts": ts, "impact": imp, "currency": ccy, "title": e.get("title", "")})
    print(f"fetched {len(rows)} matching events (>= {args.impact}, {sorted(wanted)})")

    if not rows:
        raise SystemExit("no matching events in the current-week feed")
    new = pl.DataFrame(rows).with_columns(pl.col("ts").cast(pl.Datetime("us")))
    if OUT.exists():
        new = pl.concat([pl.read_parquet(OUT), new], how="vertical_relaxed")
    new = new.unique(subset=["ts", "currency", "title"]).sort("ts")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    new.write_parquet(OUT)
    print(f"{len(new)} events -> {OUT}")
    print(f"range: {new['ts'].min()} .. {new['ts'].max()}")


if __name__ == "__main__":
    main()
