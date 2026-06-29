"""Live per-strategy virtual ledger, run inside PortfolioRunner.

The broker only sees one netted position per symbol under one magic, so a single
order can't be pinned to a strategy. This keeps a *virtual* book per strategy and
scores each as if it traded alone, producing two live numbers per step:

  * gross — mark-to-market of the book's own target position, no costs. The live
            twin of backtest.attribution.attribute()'s gross_pnl. ("A")
  * net   — gross minus the spread/slippage the book would pay on its OWN
            turnover, i.e. what the strategy would net executing independently.
            Mirrors the backtest engine cost model. ("virtual B")

Both start from the book's standalone target lots (pre-netting, pre-cap), so the
exposure cap / vol-halt clipping that the real net order suffers does not distort
the per-strategy attribution. Marking uses the same per-step fill price the STEP
log records, so a live run and an after-the-fact replay agree.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class _Book:
    pos: dict[str, float] = field(default_factory=dict)  # symbol -> virtual lots held
    gross: float = 0.0                                   # cumulative mark-to-market
    cost: float = 0.0                                    # cumulative simulated cost


class BookLedger:
    """Accumulates per-book virtual P&L across steps. Call update() once per
    logged STEP with the books that saw a new bar and the current marks."""

    def __init__(self, slippage_bps: float = 0.5) -> None:
        self.slippage_bps = slippage_bps
        self.books: dict[str, _Book] = {}
        self._prev_price: dict[str, float] = {}

    def update(self, infos: list[dict], marks: dict[str, dict]) -> list[dict]:
        """marks: {symbol: {"price", "contract", "spread"}} for this step.
        infos: the per-book info dicts (book, targets, ...) that updated this bar.
        Returns one row per known book for the STEP log."""
        price = {s: m["price"] for s, m in marks.items()}

        # 1. mark every HELD book to market over the interval ending at this step
        #    (positions carried from the previous step, even books with no new bar)
        gross_step: dict[str, float] = {}
        for name, b in self.books.items():
            step = 0.0
            for sym, lots in b.pos.items():
                if sym in price and sym in self._prev_price:
                    step += lots * (price[sym] - self._prev_price[sym]) * marks[sym]["contract"]
            b.gross += step
            gross_step[name] = step

        # 2. roll books that saw a new bar onto their fresh target, charging the
        #    spread/slippage on the turnover (the cost an independent book pays)
        cost_step: dict[str, float] = {}
        for info in infos:
            name = info["book"]
            b = self.books.setdefault(name, _Book())
            new = info.get("targets", {})
            c = 0.0
            for sym in set(new) | set(b.pos):
                turn = abs(new.get(sym, 0.0) - b.pos.get(sym, 0.0))
                if turn and sym in marks:
                    m = marks[sym]
                    half_spread = m["spread"] / 2 + m["price"] * self.slippage_bps * 1e-4
                    c += turn * m["contract"] * half_spread
            b.cost += c
            cost_step[name] = c
            b.pos = dict(new)

        self._prev_price.update(price)

        return [{
            "book": name,
            "pos": {s: round(l, 4) for s, l in b.pos.items() if abs(l) > 1e-9},
            "gross_step": round(gross_step.get(name, 0.0), 2),
            "gross": round(b.gross, 2),
            "cost_step": round(cost_step.get(name, 0.0), 2),
            "cost": round(b.cost, 2),
            "net": round(b.gross - b.cost, 2),
        } for name, b in self.books.items()]
