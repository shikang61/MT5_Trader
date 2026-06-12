from mt5_trader.backtest.attribution import attribute
from mt5_trader.live.runner import ratchet_sl


def step(equity, books, fills):
    return {"event": "STEP", "equity": equity, "books": books, "fills": fills}


def fill(price, cs=1.0):
    return {"price": price, "contract_size": cs, "target_lots": 0, "traded_lots": 0}


def test_single_book_marks_to_market():
    steps = [
        step(1000, [{"book": "A", "targets": {"X": 2.0}}], {"X": fill(100.0)}),
        step(1020, [{"book": "A", "targets": {"X": 2.0}}], {"X": fill(110.0)}),
    ]
    r = attribute(steps)
    assert r["A"]["gross_pnl"] == 2.0 * 10.0
    assert r["_account"]["equity_change"] == 20


def test_two_books_split_same_symbol():
    steps = [
        step(1000, [{"book": "A", "targets": {"X": 1.0}},
                    {"book": "B", "targets": {"X": -0.5}}], {"X": fill(100.0)}),
        step(1005, [{"book": "A", "targets": {"X": 1.0}},
                    {"book": "B", "targets": {"X": -0.5}}], {"X": fill(110.0)}),
    ]
    r = attribute(steps)
    assert r["A"]["gross_pnl"] == 10.0
    assert r["B"]["gross_pnl"] == -5.0


def test_cached_book_carries_position():
    # book emits info only on its first step; position must still earn P&L
    steps = [
        step(1000, [{"book": "A", "targets": {"X": 1.0}}], {"X": fill(100.0)}),
        step(1000, [], {"X": fill(105.0)}),
        step(1000, [], {"X": fill(120.0)}),
    ]
    r = attribute(steps)
    assert r["A"]["gross_pnl"] == 20.0


def test_turnover_counted():
    steps = [
        step(1000, [{"book": "A", "targets": {"X": 1.0}}], {"X": fill(100.0)}),
        step(1000, [{"book": "A", "targets": {"X": -1.0}}], {"X": fill(100.0)}),
    ]
    assert attribute(steps)["A"]["turnover_lots"] == 3.0  # 0->1 then 1->-1


def test_ratchet_long_only_tightens():
    assert ratchet_sl(1.0, 105.0, 1.0, 100.0) == 105.0   # tighten ok
    assert ratchet_sl(1.0, 95.0, 1.0, 100.0) == 100.0    # loosen blocked
    assert ratchet_sl(-1.0, 110.0, -1.0, 105.0) == 105.0  # short mirrored


def test_ratchet_resets_on_flip_or_flat():
    assert ratchet_sl(-1.0, 120.0, 1.0, 100.0) == 120.0  # direction flip
    assert ratchet_sl(0.0, 0.0, 1.0, 100.0) == 0.0       # flat
    assert ratchet_sl(1.0, 95.0, 0.0, 0.0) == 95.0       # fresh position
