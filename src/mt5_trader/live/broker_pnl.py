"""Realized P&L by magic from broker deal history (broker truth).

Shared by dashboard/remote_probe.py and scripts/pnl_report.py --broker. The
aggregation is a pure function so it unit-tests without MetaTrader5."""
from __future__ import annotations

from collections import defaultdict

MAGIC_LABELS = {
    1001: "Portfolio (all books)",
    2002: "PDHL passive (retired)",
    2003: "Passive consolidation",
}


def realized_by_magic(deals, entry_out) -> dict[int, dict]:
    """Aggregate CLOSED broker deals by magic. Only deals with `d.entry == entry_out`
    (DEAL_ENTRY_OUT) carry realized P&L. Returns
    {magic: {"net","gross","swap","n","wins","best","worst","symbols"}} where
    net = profit + commission + swap, gross = profit, best = max realized per deal,
    worst = min realized per deal, symbols is a set."""
    agg: dict[int, dict] = defaultdict(
        lambda: {"net": 0.0, "gross": 0.0, "swap": 0.0, "n": 0, "wins": 0,
                 "best": float("-inf"), "worst": float("inf"), "symbols": set()})
    for d in deals:
        if d.entry != entry_out:
            continue
        net_d = d.profit + d.commission + d.swap
        a = agg[d.magic]
        a["net"] += net_d
        a["gross"] += d.profit
        a["swap"] += d.swap
        a["n"] += 1
        a["wins"] += 1 if net_d > 0 else 0
        a["best"] = max(a["best"], net_d)
        a["worst"] = min(a["worst"], net_d)
        a["symbols"].add(d.symbol)
    return dict(agg)
