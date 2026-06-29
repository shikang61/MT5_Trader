from mt5_trader.backtest.attribution import attribute
from mt5_trader.live.ledger import BookLedger


def marks(price, cs=1.0, spread=0.0):
    return {"X": {"price": price, "contract": cs, "spread": spread}}


def info(book, lots):
    return {"book": book, "targets": {"X": lots}}


def row(rows, book):
    return next(r for r in rows if r["book"] == book)


def test_gross_marks_to_market_no_cost():
    led = BookLedger(slippage_bps=0.0)
    led.update([info("A", 2.0)], marks(100.0))          # open 2 lots at 100, no spread
    rows = led.update([info("A", 2.0)], marks(110.0))   # +10 move on 2 lots = +20
    r = row(rows, "A")
    assert r["gross"] == 20.0
    assert r["cost"] == 0.0
    assert r["net"] == 20.0


def test_held_book_marked_when_no_new_bar():
    # book emits info only on its first step; the carried position still earns P&L
    led = BookLedger(slippage_bps=0.0)
    led.update([info("A", 1.0)], marks(100.0))
    led.update([], marks(105.0))                        # no info, position held
    rows = led.update([], marks(120.0))
    assert row(rows, "A")["gross"] == 20.0


def test_cost_charged_on_own_turnover():
    # slippage only (no spread): cost = turnover * contract * price * bps*1e-4
    led = BookLedger(slippage_bps=1.0)                  # 1bp
    rows = led.update([info("A", 2.0)], marks(100.0))   # 0->2 lots, turnover 2
    # 2 lots * 1 contract * (0/2 + 100 * 1e-4) = 2 * 0.01 = 0.02
    assert row(rows, "A")["cost"] == 0.02
    assert row(rows, "A")["net"] == round(0.0 - 0.02, 2)


def test_spread_half_charged():
    led = BookLedger(slippage_bps=0.0)
    rows = led.update([info("A", 1.0)], marks(100.0, spread=0.4))  # half-spread 0.2 * 1 lot
    assert row(rows, "A")["cost"] == 0.20


def test_net_equals_gross_minus_cost():
    led = BookLedger(slippage_bps=0.5)
    led.update([info("A", 1.0)], marks(100.0, spread=0.2))
    rows = led.update([info("A", 0.0)], marks(110.0, spread=0.2))  # +10 gross, then flatten
    r = row(rows, "A")
    assert r["net"] == round(r["gross"] - r["cost"], 2)
    assert r["gross"] == 10.0


def test_two_books_scored_independently():
    led = BookLedger(slippage_bps=0.0)
    led.update([info("A", 1.0), info("B", -0.5)], marks(100.0))
    rows = led.update([info("A", 1.0), info("B", -0.5)], marks(110.0))
    assert row(rows, "A")["gross"] == 10.0
    assert row(rows, "B")["gross"] == -5.0


def test_live_gross_matches_offline_attribute():
    # The live ledger's gross must equal backtest.attribution.attribute()'s gross_pnl
    # on the same sequence, so a live run and a log replay never disagree.
    seq = [
        ([info("A", 1.0), info("B", -0.5)], 100.0),
        ([info("A", 1.0), info("B", -0.5)], 110.0),
        ([info("A", -1.0), info("B", -0.5)], 105.0),
        ([info("A", -1.0)], 108.0),                 # B holds, no new bar
    ]
    led = BookLedger(slippage_bps=0.0)
    last = {}
    for infos, price in seq:
        for r in led.update(infos, marks(price)):
            last[r["book"]] = r["gross"]

    steps = [{"event": "STEP", "equity": 1000, "books": infos,
              "fills": {"X": {"price": price, "contract_size": 1.0}}}
             for infos, price in seq]
    ref = attribute(steps)
    assert last["A"] == round(ref["A"]["gross_pnl"], 2)
    assert last["B"] == round(ref["B"]["gross_pnl"], 2)
