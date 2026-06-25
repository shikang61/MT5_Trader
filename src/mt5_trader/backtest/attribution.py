"""Per-book P&L attribution from portfolio runner logs.

The broker only sees net positions per symbol; this reconstructs each book's
gross mark-to-market P&L from the STEP events: the book's target lots (held
since the previous step) times the price change, times contract size.

Gross = before costs. The gap between summed gross P&L and the account's
equity change is spread/slippage/swap — if that gap dwarfs gross P&L, the
portfolio is churning itself to death and you'd want to know that too.
"""
from __future__ import annotations

import json
from pathlib import Path


def load_steps(log_path: Path | str) -> list[dict]:
    steps = []
    with open(log_path) as f:
        for line in f:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("event") == "STEP" and "books" in ev:
                steps.append(ev)
    return steps


def attribute(steps: list[dict]) -> dict:
    """Returns {book_name: {"gross_pnl": float, "turnover_lots": float,
    "steps": int}} plus "_account": {"equity_change", "first_equity", ...}."""
    books: dict[str, dict] = {}
    held: dict[str, dict[str, float]] = {}   # book -> {symbol: lots} after prev step
    prev_prices: dict[str, float] = {}
    contract: dict[str, float] = {}
    cur_pnl: dict[str, float] = {}           # running P&L of each book's open trade
    cur_sign: dict[str, int] = {}            # direction of that trade (0 = flat)

    def close_trade(name: str) -> None:
        if cur_sign.get(name, 0):
            p = cur_pnl.get(name, 0.0)
            b = books[name]
            b["trades"] += 1
            if p > 0:
                b["wins"] += 1
            if b["best"] is None or p > b["best"]:
                b["best"] = p
            if b["worst"] is None or p < b["worst"]:
                b["worst"] = p
        cur_pnl[name] = 0.0

    for ev in steps:
        prices = {s: f["price"] for s, f in ev["fills"].items() if "price" in f}
        for s, f in ev["fills"].items():
            if "contract_size" in f:
                contract[s] = f["contract_size"]

        # mark held positions over the interval ending at this step
        for name, positions in held.items():
            pnl = 0.0
            for sym, lots in positions.items():
                if sym in prices and sym in prev_prices and sym in contract:
                    pnl += lots * (prices[sym] - prev_prices[sym]) * contract[sym]
            books[name]["gross_pnl"] += pnl
            cur_pnl[name] = cur_pnl.get(name, 0.0) + pnl    # accrue to the open trade

        # update holdings from this step's book targets
        for info in ev["books"]:
            name = info["book"]
            books.setdefault(name, {"gross_pnl": 0.0, "turnover_lots": 0.0,
                                    "steps": 0, "trades": 0, "wins": 0,
                                    "best": None, "worst": None})
            books[name]["steps"] += 1
            new = info.get("targets", {})
            old = held.get(name, {})
            books[name]["turnover_lots"] += sum(
                abs(new.get(s, 0.0) - old.get(s, 0.0)) for s in set(new) | set(old))
            held[name] = dict(new)
            # a "trade" is a contiguous same-sign signal run
            sig = info.get("signal", 0.0)
            ns = (sig > 0) - (sig < 0)
            if ns != cur_sign.get(name, 0):
                close_trade(name)
                cur_sign[name] = ns

        prev_prices.update(prices)

    for name in list(cur_sign):              # close trades still open at the end
        close_trade(name)

    out: dict = dict(books)
    if steps:
        out["_account"] = {
            "first_equity": steps[0]["equity"],
            "last_equity": steps[-1]["equity"],
            "equity_change": steps[-1]["equity"] - steps[0]["equity"],
            "n_steps": len(steps),
        }
    return out


def format_report(result: dict) -> str:
    acct = result.pop("_account", None)
    lines = [f"{'book':<40} {'gross_pnl':>12} {'turnover':>10} {'steps':>7}"]
    total = 0.0
    for name, r in sorted(result.items(), key=lambda kv: -kv[1]["gross_pnl"]):
        total += r["gross_pnl"]
        lines.append(f"{name:<40} {r['gross_pnl']:>12,.2f} "
                     f"{r['turnover_lots']:>10.2f} {r['steps']:>7}")
    lines.append(f"{'TOTAL (gross, before costs)':<40} {total:>12,.2f}")
    if acct:
        lines.append(f"{'Account equity change':<40} {acct['equity_change']:>12,.2f}"
                     f"   ({acct['first_equity']:,.2f} -> {acct['last_equity']:,.2f},"
                     f" {acct['n_steps']} steps)")
        lines.append(f"{'Gap (costs/slippage/swap/untracked)':<40} "
                     f"{acct['equity_change'] - total:>12,.2f}")
    return "\n".join(lines)
