#!/usr/bin/env python
"""Per-strategy P&L attribution from a portfolio log.

    bash scripts/fetch_logs.sh                  # pull fresh logs first
    uv run scripts/pnl_report.py                # newest pulled portfolio.jsonl
    uv run scripts/pnl_report.py path/to/portfolio.jsonl
"""
import argparse
import sys
from pathlib import Path

from mt5_trader.backtest.attribution import attribute, format_report, load_steps


def newest_pulled_log() -> Path:
    candidates = sorted(Path("reports/vps_logs").glob("*/portfolio.jsonl"))
    if not candidates:
        sys.exit("no pulled portfolio.jsonl found — run: bash scripts/fetch_logs.sh")
    return candidates[-1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("log", nargs="?", type=Path, default=None)
    args = p.parse_args()

    log = args.log or newest_pulled_log()
    steps = load_steps(log)
    if not steps:
        sys.exit(f"no STEP events with book data in {log}")
    print(f"source: {log}\n")
    print(format_report(attribute(steps)))


if __name__ == "__main__":
    main()
