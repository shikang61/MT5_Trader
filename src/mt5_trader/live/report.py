"""Pure formatter for the daily Telegram performance report.

Headline = broker truth (realized net + equity change). Per-strategy section =
modeled gross attribution (pre-cost) reconstructed from the portfolio log — it
will not exactly tie to account equity.
"""
from __future__ import annotations

from mt5_trader.live.broker_pnl import MAGIC_LABELS


def _winpct(wins: int, n: int) -> str:
    return f"{round(wins / n * 100)}% W" if n else "0% W"


def _short(book: str) -> str:
    """'DonchianBreakout:XAUUSD@M5' -> 'DonchianBreakout'."""
    return book.split(":", 1)[0]


def build_report(day: str, equity_open: float, equity_now: float,
                 deals: dict, modeled: dict, labels: dict = MAGIC_LABELS) -> str:
    change = equity_now - equity_open
    lines = [
        f"\U0001F7E1 GOLD DESK — {day} (close)",
        f"Equity  {equity_open:,.0f} → {equity_now:,.0f}   ({change:+,.2f})",
        "",
        "REALIZED today (broker truth, net):",
    ]

    if deals:
        total = 0.0
        for mg in sorted(deals, key=int):
            v = deals[mg]
            total += v["net"]
            label = labels.get(int(mg), str(mg))
            lines.append(
                f"  {label:<22} {v['net']:>+9.2f}  {v['n']:>2} tr  {_winpct(v['wins'], v['n'])}"
                f"  best {v['best']:+.2f}  worst {v['worst']:+.2f}")
        lines.append(f"  ── TOTAL realized {total:>+9.2f}")
    else:
        lines.append("  no broker data")

    lines += ["", "PER-STRATEGY (modeled gross, pre-cost — won't tie to equity):"]
    books = {k: v for k, v in modeled.items() if k != "_account"}
    if books:
        for name, r in sorted(books.items(), key=lambda kv: -kv[1]["gross_pnl"]):
            best = f"best {r['best']:+.2f}" if r["best"] is not None else "best —"
            worst = f"worst {r['worst']:+.2f}" if r["worst"] is not None else "worst —"
            lines.append(
                f"  {_short(name):<18} {r['gross_pnl']:>+9.2f}  {_winpct(r['wins'], r['trades'])}"
                f"  {best}  {worst}  {r['trades']} tr")
    else:
        lines.append("  no steps today")

    return "\n".join(lines)
