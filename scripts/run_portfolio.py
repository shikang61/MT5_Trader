#!/usr/bin/env python
"""Run every book in config/portfolio.yaml in one process (Windows VPS).

Replaces multiple run_live.py processes: risk budget is split by book weight,
per-symbol orders are netted across books, one kill switch flattens all.

    python scripts/run_portfolio.py
    python scripts/run_portfolio.py --portfolio config/portfolio.yaml
"""
import argparse
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

from mt5_trader.live.mt5_gateway import MT5Gateway
from mt5_trader.live.runner import Book, PortfolioRunner, RunnerCfg
from mt5_trader.live.single_instance import acquire_or_exit
from mt5_trader.risk.manager import RiskManager
from mt5_trader.strategies import REGISTRY


def load_books(path: Path) -> tuple[list[Book], dict]:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    books = [
        Book(strategy=REGISTRY[b["strategy"]](**b.get("params", {})),
             symbol=b["symbol"],
             weight=float(b.get("weight", 1.0)),
             symbol_b=b.get("symbol2", ""),
             beta=float(b.get("beta", 1.0)),
             trade_symbol_b=bool(b.get("trade_symbol2", True)),
             timeframe=b.get("timeframe", "M1"))
        for b in cfg["books"]
    ]
    total = sum(b.weight for b in books)
    if not 0.5 <= total <= 1.5:
        raise SystemExit(f"book weights sum to {total:.2f} — expected ~1.0")
    return books, cfg


def main():
    load_dotenv()  # MT5_LOGIN/PASSWORD/SERVER from a .env if present
    p = argparse.ArgumentParser()
    p.add_argument("--portfolio", type=Path, default=Path("config/portfolio.yaml"))
    p.add_argument("--risk-config", type=Path, default=Path("config/risk.yaml"))
    args = p.parse_args()

    books, cfg = load_books(args.portfolio)
    names = ", ".join(f"{b['strategy']}@{b['symbol']}*{float(b.get('weight', 1.0)):g}"
                      for b in cfg["books"])
    print(f"[run_portfolio] portfolio book | {len(books)} books from {args.portfolio} | {names}",
          flush=True)
    _lock = acquire_or_exit("portfolio")  # refuse a duplicate portfolio bot
    gw = MT5Gateway()
    gw.connect(
        login=int(os.environ["MT5_LOGIN"]),
        password=os.environ["MT5_PASSWORD"],
        server=os.environ["MT5_SERVER"],
    )
    try:
        runner = PortfolioRunner(
            gateway=gw,
            books=books,
            risk=RiskManager.from_yaml(args.risk_config),
            cfg=RunnerCfg(symbol="PORTFOLIO",
                          sl_atr_mult=float(cfg.get("sl_atr_mult", 3.0)),
                          tp_atr_mult=float(cfg.get("tp_atr_mult", 0.0)),
                          magic=cfg.get("magic"),
                          exposure_cap=float(cfg.get("exposure_cap", 0.0)),
                          log_path=Path("logs/portfolio.jsonl")),
        )
        runner.run()
    finally:
        gw.shutdown()


if __name__ == "__main__":
    main()
