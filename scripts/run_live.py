#!/usr/bin/env python
"""Live/paper trading entrypoint — run on the Windows VPS next to the MT5 terminal.

Credentials via environment variables (never commit them):
    set MT5_LOGIN=12345678
    set MT5_PASSWORD=...
    set MT5_SERVER=CompetitionBroker-Server

    python scripts/run_live.py --strategy donchian --symbol XAUUSD
    python scripts/run_live.py --strategy ratio_mr --symbol XAUUSD --symbol2 XAGUSD --beta 1.0
"""
import argparse
import os
from pathlib import Path

from mt5_trader.live.mt5_gateway import MT5Gateway
from mt5_trader.live.runner import LiveRunner, PairRunner, PairRunnerCfg, RunnerCfg
from mt5_trader.risk.manager import RiskManager
from mt5_trader.strategies import REGISTRY


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--strategy", required=True, choices=sorted(REGISTRY))
    p.add_argument("--symbol", required=True)
    p.add_argument("--symbol2", help="second leg -> trades the spread with PairRunner")
    p.add_argument("--beta", type=float, default=1.0)
    p.add_argument("--risk-config", type=Path, default=Path("config/risk.yaml"))
    args = p.parse_args()

    gw = MT5Gateway()
    gw.connect(
        login=int(os.environ["MT5_LOGIN"]),
        password=os.environ["MT5_PASSWORD"],
        server=os.environ["MT5_SERVER"],
    )
    try:
        strategy = REGISTRY[args.strategy]()
        risk = RiskManager.from_yaml(args.risk_config)
        if args.symbol2:
            log = Path(f"logs/live_{args.symbol}_{args.symbol2}_{args.strategy}.jsonl")
            runner = PairRunner(gw, strategy, risk, PairRunnerCfg(
                symbol=args.symbol, symbol_b=args.symbol2, beta=args.beta, log_path=log))
        else:
            log = Path(f"logs/live_{args.symbol}_{args.strategy}.jsonl")
            runner = LiveRunner(gw, strategy, risk, RunnerCfg(
                symbol=args.symbol, log_path=log))
        runner.run()
    finally:
        gw.shutdown()


if __name__ == "__main__":
    main()
