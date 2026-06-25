#!/usr/bin/env python
"""Daily performance summary to Telegram. Runs ON the VPS at weekday close.

    .venv\\Scripts\\python.exe scripts\\daily_report.py    # gather + send
    uv run scripts/daily_report.py --dry-run               # print, no send
    uv run scripts/daily_report.py --dry-run --no-broker   # off-VPS preview (no MT5)

Window = the current gold trading session: from the most recent 22:00 UTC open
(daily break is ~21:00-22:00 UTC) through now. Reporting at 21:10 UTC therefore
covers exactly one session (22:00 prev day -> 21:00 today) with no gap.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from mt5_trader.live import telegram_send
from mt5_trader.live.broker_pnl import realized_by_magic
from mt5_trader.live.report import build_report

SESSION_OPEN_HOUR = 22  # gold session opens 22:00 UTC (summer/DST; shifts to 23:00 in winter)
PORTFOLIO_CFG = Path("config/portfolio.yaml")


def load_weights(path: Path = PORTFOLIO_CFG) -> list[tuple[str, float]]:
    """[(strategy, weight)] from the portfolio config, heaviest first."""
    if not path.exists():
        return []
    cfg = yaml.safe_load(path.read_text()) or {}
    pairs = [(b["strategy"], float(b.get("weight", 0.0))) for b in cfg.get("books", [])]
    return sorted(pairs, key=lambda kv: -kv[1])


def session_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """(session_start, now) — start = the most recent 22:00 UTC at or before now."""
    now = now or datetime.now(timezone.utc)
    start = now.replace(hour=SESSION_OPEN_HOUR, minute=0, second=0, microsecond=0)
    if now < start:
        start -= timedelta(days=1)
    return start, now


def fetch_broker(start: datetime) -> tuple[dict, float]:
    """(deals_by_magic since `start`, current equity). MetaTrader5 lazy (VPS only)."""
    import MetaTrader5 as m

    from mt5_trader.live.mt5_gateway import MT5Gateway

    gw = MT5Gateway()
    gw.connect(login=int(os.environ["MT5_LOGIN"]),
               password=os.environ["MT5_PASSWORD"],
               server=os.environ["MT5_SERVER"])
    try:
        to = datetime.now(timezone.utc) + timedelta(days=1)
        deals = realized_by_magic(m.history_deals_get(start, to) or [], m.DEAL_ENTRY_OUT)
        return deals, m.account_info().equity
    finally:
        gw.shutdown()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows console is cp1252; the report has an emoji

    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="print the message, do not send")
    p.add_argument("--no-broker", action="store_true",
                   help="skip MT5 (empty preview; for off-VPS testing)")
    args = p.parse_args()

    load_dotenv()
    start, now = session_bounds()
    window = f"{start:%Y-%m-%d %H:%M} to {now:%Y-%m-%d %H:%M} UTC"

    if args.no_broker:
        deals, equity = {}, 0.0
    else:
        deals, equity = fetch_broker(start)

    text = build_report(window, equity, deals, load_weights())

    if args.dry_run:
        print(text)
        return
    telegram_send.send(os.environ["TELEGRAM_BOT_TOKEN"],
                       os.environ["TELEGRAM_CHAT_ID"], text, parse_mode="HTML")
    print("sent")


if __name__ == "__main__":
    main()
