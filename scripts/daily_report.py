#!/usr/bin/env python
"""Daily performance summary to Telegram. Runs ON the VPS at weekday close.

    .venv\\Scripts\\python.exe scripts\\daily_report.py        # gather + send
    uv run scripts/daily_report.py --dry-run                   # print, no send
    uv run scripts/daily_report.py --dry-run --no-broker --log reports/vps_logs/<ts>/portfolio.jsonl
"""
import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from mt5_trader.backtest.attribution import attribute, load_steps
from mt5_trader.live import telegram_send
from mt5_trader.live.broker_pnl import realized_by_magic
from mt5_trader.live.report import build_report

DEFAULT_LOG = Path("logs/portfolio.jsonl")


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_today_steps(log: Path, day: str) -> list[dict]:
    return [s for s in load_steps(log) if s.get("ts", "")[:10] == day]


def fetch_broker(day: str) -> tuple[dict, float]:
    """(deals_by_magic, equity_now). MetaTrader5 imported lazily (VPS only)."""
    import MetaTrader5 as m

    from mt5_trader.live.mt5_gateway import MT5Gateway

    gw = MT5Gateway()
    gw.connect(login=int(os.environ["MT5_LOGIN"]),
               password=os.environ["MT5_PASSWORD"],
               server=os.environ["MT5_SERVER"])
    try:
        frm = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
        to = datetime.now(timezone.utc) + timedelta(days=1)
        deals = realized_by_magic(m.history_deals_get(frm, to) or [], m.DEAL_ENTRY_OUT)
        return deals, m.account_info().equity
    finally:
        gw.shutdown()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="print the message, do not send")
    p.add_argument("--log", type=Path, default=DEFAULT_LOG)
    p.add_argument("--no-broker", action="store_true",
                   help="skip MT5 (modeled-only preview; for off-VPS testing)")
    args = p.parse_args()

    load_dotenv()
    day = today_utc()
    steps = load_today_steps(args.log, day) if args.log.exists() else []
    modeled = attribute(steps) if steps else {}
    acct = modeled.get("_account", {})
    equity_open = acct.get("first_equity", 0.0)

    if args.no_broker:
        deals, equity_now = {}, acct.get("last_equity", equity_open)
    else:
        deals, equity_now = fetch_broker(day)

    if not steps:
        equity_open = equity_now   # no STEP baseline -> show 0 change, not a full-equity jump

    text = build_report(day, equity_open, equity_now, deals, modeled)

    if args.dry_run:
        print(text)
        return
    telegram_send.send(os.environ["TELEGRAM_BOT_TOKEN"],
                       os.environ["TELEGRAM_CHAT_ID"], text)
    print("sent")


if __name__ == "__main__":
    main()
