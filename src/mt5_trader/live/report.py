"""Pure formatter for the daily Telegram performance report.

Headline = broker truth (realized net + equity change). Per-strategy section =
modeled gross attribution (pre-cost) reconstructed from the portfolio log — it
will not exactly tie to account equity.

The body is wrapped in an HTML <pre> block so Telegram renders it monospace and
the columns line up (sent with parse_mode="HTML"). Two separate sections by
design: the broker only tags realized P&L by magic (all portfolio books share
one magic), so a true per-strategy realized split does not exist.
"""
from __future__ import annotations

import html

from mt5_trader.live.broker_pnl import MAGIC_LABELS


def _win(wins: int, n: int) -> str:
    return f"{round(wins / n * 100)}%" if n else "—"


def _short(book: str) -> str:
    """'DonchianBreakout:XAUUSD@M5' -> 'DonchianBreakout'."""
    return book.split(":", 1)[0]


def _hilo(best: float | None, worst: float | None) -> str:
    """Compact best/worst, e.g. '↑+25.17 ↓-32.69'; '' when no closed trade."""
    if best is None or worst is None:
        return ""
    return f"↑{best:+.2f} ↓{worst:+.2f}"


def build_report(day: str, equity_open: float, equity_now: float,
                 deals: dict, modeled: dict, labels: dict = MAGIC_LABELS) -> str:
    change = equity_now - equity_open
    body = [
        f"Equity  {equity_open:,.0f} → {equity_now:,.0f}   ({change:+,.2f})",
        "",
        "REALIZED today — broker truth (net):",
    ]

    if deals:
        total = 0.0
        for mg in sorted(deals, key=int):
            v = deals[mg]
            total += v["net"]
            label = labels.get(int(mg), str(mg))
            body.append(
                f"  {label:<21} {v['net']:>+9.2f}  {v['n']:>2}tr {_win(v['wins'], v['n']):>4}"
                f"  {_hilo(v['best'], v['worst'])}".rstrip())
        body.append(f"  {'TOTAL realized':<21} {total:>+9.2f}")
    else:
        body.append("  no broker data")

    body += ["", "PER-STRATEGY — modeled gross, pre-cost (won't tie to equity):"]
    books = {k: v for k, v in modeled.items() if k != "_account"}
    if books:
        for name, r in sorted(books.items(), key=lambda kv: -kv[1]["gross_pnl"]):
            body.append(
                f"  {_short(name):<16} {r['gross_pnl']:>+9.2f}  {r['trades']:>2}tr "
                f"{_win(r['wins'], r['trades']):>4}  {_hilo(r['best'], r['worst'])}".rstrip())
    else:
        body.append("  no steps today")

    title = f"\U0001F7E1 <b>GOLD DESK — {day} · close</b>"
    return f"{title}\n<pre>{html.escape(chr(10).join(body), quote=False)}</pre>"
