#!/usr/bin/env python
"""Post-process a portfolio runner log into per-strategy P&L + a trade blotter.

    bash scripts/fetch_logs.sh                     # pull fresh logs first
    uv run scripts/post_processing.py              # newest pulled portfolio.jsonl
    uv run scripts/post_processing.py path/to/portfolio.jsonl
    uv run scripts/post_processing.py --no-blotter # summary table only

Each STEP logs every book's *target* position and the per-symbol price. The
broker only ever sees the net position per symbol, so individual fills can't be
split back to a book; instead we rebuild each strategy's own position series
from its targets, mark it to the logged prices, and cut it into trades (a trade
runs from flat -> open until the strategy goes flat or flips). Per-trade P&L is
the marked-to-market sum over the bars it was held (gross, before costs).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

from mt5_trader.backtest.attribution import load_steps

EPS = 1e-9


@dataclass
class Trade:
    book: str
    side: int                       # +1 long, -1 short (sign of the primary leg)
    open_ts: str
    entry: dict[str, float]         # target lots per symbol at entry
    close_ts: str = ""
    bars: int = 0
    pnl: float = 0.0                # marked-to-market, gross


@dataclass
class BookAcc:
    trades: list[Trade] = field(default_factory=list)
    open: Trade | None = None


def _side(targets: dict[str, float]) -> int:
    """Direction of the strategy, taken from its primary (first) leg."""
    if not targets:
        return 0
    v = next(iter(targets.values()))
    return 1 if v > EPS else (-1 if v < -EPS else 0)


def build_trades(steps: list[dict]) -> tuple[dict[str, BookAcc], dict]:
    """Reconstruct per-book trades. Returns ({book: BookAcc}, account_info)."""
    accs: dict[str, BookAcc] = {}
    held: dict[str, dict[str, float]] = {}     # book -> targets carried since prev step
    prev_prices: dict[str, float] = {}
    contract: dict[str, float] = {}
    fills_per_sym: dict[str, dict] = {}        # sym -> {"orders": n, "lots": float}

    for ev in steps:
        prices = {s: f["price"] for s, f in ev["fills"].items() if "price" in f}
        for s, f in ev["fills"].items():
            if "contract_size" in f:
                contract[s] = f["contract_size"]
            traded = f.get("traded_lots", 0.0)
            if abs(traded) > EPS:
                rec = fills_per_sym.setdefault(s, {"orders": 0, "lots": 0.0})
                rec["orders"] += 1
                rec["lots"] += abs(traded)

        # 1) mark every held position over the interval ending at this step,
        #    accruing into whichever trade is currently open for that book.
        for book, positions in held.items():
            acc = accs[book]
            if acc.open is None:
                continue
            for sym, lots in positions.items():
                if sym in prices and sym in prev_prices and sym in contract:
                    acc.open.pnl += lots * (prices[sym] - prev_prices[sym]) * contract[sym]
            acc.open.bars += 1

        # 2) apply this step's new targets, opening/closing trades on a flip.
        for info in ev["books"]:
            name = info["book"]
            acc = accs.setdefault(name, BookAcc())
            new = info.get("targets", {})
            new_side = _side(new)
            cur_side = acc.open.side if acc.open else 0
            if new_side != cur_side:
                if acc.open is not None:                       # close the running trade
                    acc.open.close_ts = info.get("bar_ts", ev["ts"])
                    acc.trades.append(acc.open)
                    acc.open = None
                if new_side != 0:                              # open a fresh one
                    acc.open = Trade(book=name, side=new_side,
                                     open_ts=info.get("bar_ts", ev["ts"]),
                                     entry=dict(new))
            held[name] = dict(new)

        prev_prices.update(prices)

    # close trades still open at the end of the log (mark as open)
    for acc in accs.values():
        if acc.open is not None:
            acc.open.close_ts = "(open)"
            acc.trades.append(acc.open)
            acc.open = None

    account = {}
    if steps:
        account = {"first_ts": steps[0]["ts"], "last_ts": steps[-1]["ts"],
                   "first_equity": steps[0]["equity"], "last_equity": steps[-1]["equity"],
                   "n_steps": len(steps), "fills_per_sym": fills_per_sym}
    return accs, account


def _ts(s: str) -> str:
    return s[:16].replace("T", " ") if s and s[0].isdigit() else s


def format_report(accs: dict[str, BookAcc], account: dict, blotter: bool) -> str:
    out = []
    if account:
        out.append(f"span: {_ts(account['first_ts'])} -> {_ts(account['last_ts'])} "
                   f"({account['n_steps']} steps)")
        out.append(f"account equity: {account['first_equity']:,.2f} -> "
                   f"{account['last_equity']:,.2f} "
                   f"({account['last_equity'] - account['first_equity']:+,.2f})\n")

    out.append("=== Per-strategy P&L & trades (gross, before costs) ===")
    out.append(f"{'strategy':<34}{'net_pnl':>11}{'trades':>8}{'wins':>6}"
               f"{'win%':>7}{'avg/trade':>11}")
    total_pnl = 0.0
    total_tr = 0
    for name in sorted(accs, key=lambda n: sum(t.pnl for t in accs[n].trades)):
        trades = accs[name].trades
        pnl = sum(t.pnl for t in trades)
        wins = sum(1 for t in trades if t.pnl > 0)
        n = len(trades)
        total_pnl += pnl
        total_tr += n
        winpct = f"{100 * wins / n:.0f}%" if n else "-"
        avg = f"{pnl / n:+.2f}" if n else "-"
        out.append(f"{name:<34}{pnl:>+11.2f}{n:>8}{wins:>6}{winpct:>7}{avg:>11}")
    out.append(f"{'TOTAL':<34}{total_pnl:>+11.2f}{total_tr:>8}")

    if account.get("fills_per_sym"):
        out.append("\nbroker fills per symbol (net across all books — not separable "
                   "per strategy):")
        for sym, r in sorted(account["fills_per_sym"].items()):
            out.append(f"  {sym}: {r['orders']} orders, {r['lots']:.2f} lots traded")

    if blotter:
        out.append("\n=== Trade blotter ===")
        for name in sorted(accs):
            trades = accs[name].trades
            if not trades:
                continue
            out.append(f"\n{name}  ({len(trades)} trades)")
            out.append(f"  {'open':<17}{'close':<17}{'side':>6}{'lots':>9}"
                       f"{'bars':>6}{'pnl':>11}")
            for t in trades:
                lots = next(iter(t.entry.values()))
                side = "long" if t.side > 0 else "short"
                out.append(f"  {_ts(t.open_ts):<17}{_ts(t.close_ts):<17}{side:>6}"
                           f"{lots:>9.3f}{t.bars:>6}{t.pnl:>+11.2f}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Passive limit-order books (run_passive_paper.py) — different log schema.
# Trades come from POSITION_CHANGE / TIME_EXIT events; there is no per-deal P&L
# in the log, and equity is the SHARED account equity (passive_limits.py docstring),
# so per-trade P&L can only be approximated as the account equity move over the
# (short, <=max_hold) hold — contaminated by whatever the portfolio bot did
# meanwhile. Reported as approximate and flagged.
# ---------------------------------------------------------------------------
def load_events(path: Path) -> list[dict]:
    import json
    out = []
    with open(path) as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def is_portfolio_log(events: list[dict]) -> bool:
    return any(e.get("event") == "STEP" and "books" in e for e in events)


def build_passive_trades(events: list[dict]) -> list[dict]:
    """Pair POSITION_CHANGE opens with their closing event into round trips."""
    trades: list[dict] = []
    cur: dict | None = None

    def close(cur, ts, eq, reason):
        cur["close_ts"], cur["eq_close"], cur["exit"] = ts, eq, reason
        cur["pnl"] = (eq - cur["eq_open"]
                      if eq is not None and cur["eq_open"] is not None else None)
        trades.append(cur)

    for e in events:
        ev = e.get("event")
        if ev == "POSITION_CHANGE":
            pos, prev, eq = e.get("position_lots", 0.0), e.get("prev", 0.0), e.get("equity")
            opening = abs(prev) < EPS <= abs(pos)
            closing = abs(pos) < EPS <= abs(prev)
            flip = abs(pos) > EPS and abs(prev) > EPS and (pos > 0) != (prev > 0)
            if (closing or flip) and cur is not None:
                close(cur, e["ts"], eq, "flip" if flip else "tp/sl")
                cur = None
            if opening or flip:
                side = 1 if pos > 0 else -1
                cur = {"open_ts": e["ts"], "side": side, "lots": abs(pos),
                       "level": e.get("buy_level") if side > 0 else e.get("sell_level"),
                       "eq_open": eq, "close_ts": "", "eq_close": None,
                       "pnl": None, "exit": ""}
        elif ev == "TIME_EXIT" and cur is not None:
            close(cur, e["ts"], e.get("equity"), "time")
            cur = None
    if cur is not None:
        close(cur, "(open)", None, "open")
    return trades


def report_passive(events: list[dict], blotter: bool) -> str:
    trades = build_passive_trades(events)
    eqs = [(e["ts"], e["equity"]) for e in events if "equity" in e]
    counts = {}
    for e in events:
        counts[e.get("event")] = counts.get(e.get("event"), 0) + 1

    out = []
    if eqs:
        out.append(f"span: {_ts(eqs[0][0])} -> {_ts(eqs[-1][0])}")
        out.append(f"account equity (SHARED): {eqs[0][1]:,.2f} -> {eqs[-1][1]:,.2f} "
                   f"({eqs[-1][1] - eqs[0][1]:+,.2f})")
    out.append(f"events: {counts.get('POSITION_CHANGE', 0)} position-changes, "
               f"{counts.get('ARM', 0)} arms, {counts.get('TIME_EXIT', 0)} time-exits, "
               f"{counts.get('ERROR', 0)} errors, {counts.get('RECONNECT_OK', 0)} reconnects")

    closed = [t for t in trades if t["pnl"] is not None]
    approx = sum(t["pnl"] for t in closed)
    wins = sum(1 for t in closed if t["pnl"] > 0)
    out.append(f"\ntrades placed: {len(trades)}   "
               f"approx net P&L: {approx:+,.2f} (account-global Δ — approximate)   "
               f"wins: {wins}/{len(closed)}")

    if blotter and trades:
        out.append(f"\n  {'open':<17}{'close':<17}{'side':>6}{'lots':>7}"
                   f"{'level':>10}{'exit':>7}{'approx_pnl':>12}")
        for t in trades:
            pnl = f"{t['pnl']:+.2f}" if t["pnl"] is not None else "-"
            lvl = f"{t['level']:.2f}" if t["level"] is not None else "-"
            side = "long" if t["side"] > 0 else "short"
            out.append(f"  {_ts(t['open_ts']):<17}{_ts(t['close_ts']):<17}{side:>6}"
                       f"{t['lots']:>7.2f}{lvl:>10}{t['exit']:>7}{pnl:>12}")
    return "\n".join(out)


def analyse(path: Path, blotter: bool) -> str:
    header = f"{'='*70}\n{path.name}\n{'='*70}"
    events = load_events(path)
    if not events:
        return f"{header}\n(empty / unreadable)"
    if is_portfolio_log(events):
        accs, account = build_trades([e for e in events
                                      if e.get("event") == "STEP" and "books" in e])
        return f"{header}\n{format_report(accs, account, blotter)}"
    return f"{header}\n{report_passive(events, blotter)}"


def discover_logs() -> list[Path]:
    """Newest pulled dir's portfolio + passive logs (the files to analyse)."""
    portfolios = sorted(Path("reports/vps_logs").glob("*/portfolio.jsonl"))
    if not portfolios:
        sys.exit("no pulled logs found — run: bash scripts/fetch_logs.sh")
    d = portfolios[-1].parent
    names = ["portfolio.jsonl", "passive_consolidation.jsonl", "passive_pdhl.jsonl"]
    return [d / n for n in names if (d / n).exists()]


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("logs", nargs="*", type=Path,
                   help="log files (default: newest pulled portfolio + passive logs)")
    p.add_argument("--no-blotter", action="store_true", help="summary tables only")
    args = p.parse_args()

    logs = args.logs or discover_logs()
    print("\n\n".join(analyse(log, blotter=not args.no_blotter) for log in logs))


if __name__ == "__main__":
    main()
