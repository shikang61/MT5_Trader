#!/usr/bin/env python
"""Paper-trade the consolidation passive-limit book on a DEMO account.

Rests buy/sell limits at the consolidation ceiling/floor while the regime is
RANGE, with TP/SL attached, cancels them when the range stops consolidating, and
logs real fills + outcomes. Validates the tick screen
(scripts/eval_passive_limits.py) against real broker fills.

    python scripts/run_passive_paper.py --magic 2003

--magic keeps this book from netting against the portfolio bot (magic 1001) on
one account.

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
    ap.add_argument("--magic", type=int, default=None)
    ap.add_argument("--range-n", type=int, default=PassiveCfg.range_n)
    ap.add_argument("--relevel-bp", type=float, default=PassiveCfg.relevel_bp,
                    help="re-post resting limits when the band drifts this far (bp); 0 = off")
    ap.add_argument("--regime-coarsen", type=int, default=PassiveCfg.regime_coarsen,
                    help="regime ER sampling stride on M5 bars; gate window = 16*stride*5min "
                         "(3 = ~4h, matches the 4h consolidation band; 15 = ~20h).")
    args = ap.parse_args()

    kw = dict(range_n=args.range_n, relevel_bp=args.relevel_bp,
              regime_coarsen=args.regime_coarsen,
              log_path=Path("logs/passive_consolidation.jsonl"))
    if args.magic is not None:
        kw["magic"] = args.magic

    _lock = acquire_or_exit("passive_consolidation")
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
