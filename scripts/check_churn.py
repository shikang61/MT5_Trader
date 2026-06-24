#!/usr/bin/env python
"""Scan a portfolio log for stop-out churn (the open->stop->open loop fixed by
widen_breached_stop, 2026-06-24).

Two churn signatures:
  1. retcode 10016 "Invalid stops" bursts — a breached stop the gateway rejects.
  2. Rapid re-entry — |traded_lots| ~ full target size on many consecutive steps
     while the target sign is steady (the book keeps rebuilding a position that
     keeps getting stopped out).

    bash scripts/fetch_logs.sh                 # pull a fresh log first
    uv run scripts/check_churn.py              # newest pulled portfolio.jsonl
    uv run scripts/check_churn.py path.jsonl --since 2026-06-24T20:19
"""
import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

DEPLOY = "2026-06-24T20:19"  # widen_breached_stop went live


def newest() -> Path:
    c = sorted(Path("reports/vps_logs").glob("*/portfolio.jsonl"))
    if not c:
        raise SystemExit("no pulled log — run: bash scripts/fetch_logs.sh")
    return c[-1]


def scan(path: Path, since: str) -> None:
    errs = defaultdict(int)                 # (day, hhmm-bucket) -> 10016 count
    reentry = defaultdict(int)              # day -> rapid full-size re-entries
    steps = 0
    for line in open(path):
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("event") != "STEP" or ev.get("ts", "") < since:
            continue
        steps += 1
        for sym, f in ev.get("fills", {}).items():
            tgt = f.get("target_lots", 0.0) or 0.0
            traded = f.get("traded_lots", 0.0) or 0.0
            # a re-entry rebuilds ~the whole target in one step while holding a side
            if tgt and abs(traded) >= 0.9 * abs(tgt) and (traded > 0) == (tgt > 0):
                reentry[ev["ts"][:10]] += 1
            err = f.get("error") or ""
            if "10016" in err:
                errs[(ev["ts"][:10], ev["ts"][11:15])] += 1

    print(f"source: {path}\nwindow: ts >= {since}   ({steps} steps)\n")
    if errs:
        print("10016 'Invalid stops' (churn) by 10-min bucket:")
        for (d, hm), n in sorted(errs.items()):
            print(f"  {d} {hm}x  {n}")
    else:
        print("10016 'Invalid stops': NONE")
    print("\nrapid full-size re-entries (open->stop->open) by day:")
    if reentry:
        for d, n in sorted(reentry.items()):
            flag = "  <-- CHURN" if n >= 10 else ""
            print(f"  {d}  {n}{flag}")
    else:
        print("  none")
    churn = bool(errs) or any(n >= 10 for n in reentry.values())
    print(f"\nverdict: {'CHURN PRESENT' if churn else 'clean — no churn signature'}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("log", nargs="?", type=Path, default=None)
    p.add_argument("--since", default="0000", help="only steps with ts >= this (e.g. 2026-06-24T20:19)")
    a = p.parse_args()
    scan(a.log or newest(), a.since)
