"""Pure formatter for the gold-desk session performance report.

Broker truth only: current account equity + realized net P&L by magic for the
current trading session. Sent with parse_mode="HTML" — bold emoji headings, one
entry per line, reads cleanly in Telegram's proportional font (no monospace
code block).

A per-strategy split is intentionally NOT shown: the broker lumps all portfolio
books under one magic and nets them, so the only per-strategy view possible is a
modeled pre-cost estimate that does not tie to real money — dropped as
misleading.
"""
from __future__ import annotations

from mt5_trader.live.broker_pnl import MAGIC_LABELS


def _trades(n: int) -> str:
    return f"{n} trade" + ("" if n == 1 else "s")


def _detail(wins: int, n: int, best: float | None, worst: float | None) -> str:
    """Trailing '(N trades · 67% win · best +X · worst -Y)'; '' when no trade."""
    if not n:
        return ""
    parts = [_trades(n)]
    if best is not None and worst is not None:
        parts += [f"{round(wins / n * 100)}% win", f"best {best:+.2f}", f"worst {worst:+.2f}"]
    return "  (" + " · ".join(parts) + ")"


def build_report(window: str, equity: float, deals: dict,
                 weights: list[tuple[str, float]] = (), labels: dict = MAGIC_LABELS) -> str:
    out = [
        f"\U0001F7E1 <b>GOLD DESK session</b>",
        f"\U0001F552 {window}",
        f"\U0001F4BC Equity: <b>{equity:,.2f}</b>",
        "",
        "<b>\U0001F4B0 Realized this session — broker truth (net)</b>",
    ]

    if deals:
        total = 0.0
        total_n = 0
        for mg in sorted(deals, key=int):
            v = deals[mg]
            total += v["net"]
            total_n += v["n"]
            label = labels.get(int(mg), str(mg))
            out.append(f"• {label}: <b>{v['net']:+.2f}</b>"
                       f"{_detail(v['wins'], v['n'], v['best'], v['worst'])}")
        out.append(f"• <b>Total: {total:+.2f}</b>  ({_trades(total_n)})")
    else:
        out.append("• no trades closed this session")

    if weights:
        out += ["", "<b>\U0001F4CB Live strategies — portfolio weights</b>"]
        out += [f"• {name}: {round(w * 100)}%" for name, w in weights]

    return "\n".join(out)
