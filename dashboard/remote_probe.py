"""Runs ON the VPS (mt5-vps) inside the project venv; prints one JSON blob to
stdout for the Mac-side dashboard. Attaches to the already-running, logged-in
MT5 terminal (same .env creds as the bot) and reports the broker truth:

    .venv\\Scripts\\python.exe dashboard/remote_probe.py [SINCE_ISO]

SINCE_ISO (default 2026-06-12) bounds the realized-deal window. Bars come from
the SAME MT5Gateway.bars() the live bot signals on (UTC ts, spread_mean in price
terms), so the dashboard's fill-quality check compares apples to apples.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import MetaTrader5 as m
from dotenv import load_dotenv

from mt5_trader.live.broker_pnl import realized_by_magic
from mt5_trader.live.mt5_gateway import MT5Gateway


def main() -> None:
    since = sys.argv[1] if len(sys.argv) > 1 else "2026-06-12"
    frm = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
    to = datetime.now(timezone.utc) + timedelta(days=1)

    load_dotenv()
    gw = MT5Gateway()
    gw.connect(login=int(os.environ["MT5_LOGIN"]),
               password=os.environ["MT5_PASSWORD"],
               server=os.environ["MT5_SERVER"])

    acc = m.account_info()
    positions = [
        {"magic": p.magic, "symbol": p.symbol, "lots": p.volume,
         "side": "BUY" if p.type == 0 else "SELL",
         "price_open": p.price_open, "profit": p.profit}
        for p in (m.positions_get() or [])
    ]

    agg = realized_by_magic(m.history_deals_get(frm, to) or [], m.DEAL_ENTRY_OUT)
    deals = {str(k): {**v, "symbols": sorted(v["symbols"])} for k, v in agg.items()}

    # Same bars the bot signals on. XAU M1+M5 + XAG M5 cover every book.
    bars = {}
    for sym, tf in (("XAUUSD", "M5"), ("XAUUSD", "M1"), ("XAGUSD", "M5")):
        df = gw.bars(sym, tf, 1000).select(
            "ts", "open", "high", "low", "close", "spread_mean")
        bars[f"{sym}@{tf}"] = {
            "ts": [t.isoformat() for t in df["ts"]],
            **{c: df[c].to_list() for c in ("open", "high", "low", "close", "spread_mean")},
        }

    print(json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "account": {"login": acc.login, "balance": acc.balance,
                    "equity": acc.equity, "margin_free": acc.margin_free},
        "positions": positions,
        "deals": deals,
        "bars": bars,
    }, default=str))


if __name__ == "__main__":
    main()
