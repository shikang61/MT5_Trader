#!/usr/bin/env python
"""Paper-trade the passive-limit experiment on a DEMO account.

Posts buy/sell limits at key levels (prior-day high/low, or the consolidation
ceiling/floor) each day with TP/SL attached, cancels them when the regime stops
consolidating, and logs real fills + outcomes. Validates the tick screen
(scripts/eval_passive_limits.py) against real broker fills.

Run TWO books side by side on one account to compare level sources — distinct
--magic keeps them from netting against each other (and against the portfolio
bot, magic 1001):

    python scripts/run_passive_paper.py --level-source pdhl          --magic 2002
    python scripts/run_passive_paper.py --level-source consolidation --magic 2003

WARNING: positions are segregated by magic, but all books on one account SHARE
equity — the portfolio bot's risk manager (kill switch, vol sizing) sees the
combined equity. For a fully isolated experiment, use a separate demo account.
Set MT5_LOGIN / MT5_PASSWORD / MT5_SERVER before running.
"""
import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from mt5_trader.live.mt5_gateway import MT5Gateway
from mt5_trader.live.passive_limits import PassiveCfg, PassiveLimitRunner
from mt5_trader.live.single_instance import acquire_or_exit


def main():
    load_dotenv()  # MT5_LOGIN/PASSWORD/SERVER from a .env if present
    ap = argparse.ArgumentParser()
    ap.add_argument("--level-source", choices=["pdhl", "consolidation"], default="pdhl")
    ap.add_argument("--magic", type=int, default=None)
    ap.add_argument("--range-n", type=int, default=PassiveCfg.range_n)
    args = ap.parse_args()

    kw = dict(level_source=args.level_source, range_n=args.range_n,
              log_path=Path(f"logs/passive_{args.level_source}.jsonl"))
    if args.magic is not None:
        kw["magic"] = args.magic

    _lock = acquire_or_exit(f"passive_{args.level_source}")  # one per level source
    gw = MT5Gateway()
    gw.connect(
        login=int(os.environ["MT5_LOGIN"]),
        password=os.environ["MT5_PASSWORD"],
        server=os.environ["MT5_SERVER"],
    )
    try:
        PassiveLimitRunner(gw, PassiveCfg(**kw)).run()
    finally:
        gw.shutdown()


if __name__ == "__main__":
    main()
