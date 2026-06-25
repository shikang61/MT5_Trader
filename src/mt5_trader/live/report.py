"""Pure formatter for the daily Telegram performance report.

Headline = broker truth (realized net + equity change). Per-strategy section =
modeled gross attribution (pre-cost) reconstructed from the portfolio log — it
will not exactly tie to account equity.

Sent with parse_mode="HTML": bold emoji headings, one entry per line in a
sentence style that reads cleanly in Telegram's proportional font (no monospace
code block). Two separate sections by design: the broker only tags realized P&L
by magic (all portfolio books share one magic), so a true per-strategy realized
split does not exist.
"""
from __future__ import annotations

from mt5_trader.live.broker_pnl import MAGIC_LABELS


def _short(book: str) -> str:
    """'DonchianBreakout:XAUUSD@M5' -> 'DonchianBreakout'."""
    return book.split(":", 1)[0]


def _detail(wins: int, n: int, best: float | None, worst: float | None) -> str:
    """Trailing '(67% win · best +X · worst -Y)'; '' when no closed trade."""
    if not n or best is None or worst is None:
        return ""
    return f"  ({round(wins / n * 100)}% win · best {best:+.2f} · worst {worst:+.2f})"


def build_report(day: str, equity_open: float, equity_now: float,
                 deals: dict, modeled: dict, labels: dict = MAGIC_LABELS) -> str:
    change = equity_now - equity_open
    out = [
        f"\U0001F7E1 <b>GOLD DESK · {day}</b>",
        f"\U0001F4BC Equity  {equity_open:,.0f} → {equity_now:,.0f}  (<b>{change:+,.2f}</b>)",
        "",
        "<b>\U0001F4B0 Realized — broker truth (net)</b>",
    ]

    if deals:
        total = 0.0
        for mg in sorted(deals, key=int):
            v = deals[mg]
            total += v["net"]
            label = labels.get(int(mg), str(mg))
            out.append(f"• {label}: <b>{v['net']:+.2f}</b>"
                       f"{_detail(v['wins'], v['n'], v['best'], v['worst'])}")
        out.append(f"• <b>Total: {total:+.2f}</b>")
    else:
        out.append("• no broker data")

    out += ["", "<b>\U0001F4C8 By strategy — modeled, pre-cost</b>"]
    books = {k: v for k, v in modeled.items() if k != "_account"}
    if books:
        active = sorted((kv for kv in books.items() if kv[1]["trades"]),
                        key=lambda kv: -kv[1]["gross_pnl"])
        idle = [name for name, r in books.items() if not r["trades"]]
        for name, r in active:
            out.append(f"• {_short(name)}: <b>{r['gross_pnl']:+.2f}</b>"
                       f"{_detail(r['wins'], r['trades'], r['best'], r['worst'])}")
        if idle:
            out.append(f"<i>Idle: {', '.join(_short(n) for n in idle)}</i>")
    else:
        out.append("• no steps today")

    return "\n".join(out)
