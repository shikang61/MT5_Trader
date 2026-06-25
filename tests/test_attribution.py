import math

from mt5_trader.backtest.attribution import attribute
from mt5_trader.live.runner import chandelier_sl


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


def test_chandelier_anchors_distance_and_trails_on_new_extreme():
    # fresh long: distance locked to entry ATR (2), sl = 100 - 3*2
    sl, st = chandelier_sl(1.0, 100.0, 100.0, 100.0, 2.0, 3.0, None)
    assert sl == 94.0 and st == (1.0, 2.0, 100.0, 100.0)
    # new high to 106, ATR contracts to 1 -> sl trails up but distance stays 3*2
    sl, st = chandelier_sl(1.0, 105.0, 106.0, 104.0, 1.0, 3.0, st)
    assert sl == 100.0 and st == (1.0, 2.0, 100.0, 106.0)
    # no new high + further ATR drop must NOT tighten the existing stop
    sl, _ = chandelier_sl(1.0, 103.0, 105.0, 102.0, 0.5, 3.0, st)
    assert sl == 100.0


def test_chandelier_resets_on_flip_and_is_off_when_invalid():
    prev = (1.0, 2.0, 100.0, 106.0)
    sl, st = chandelier_sl(-1.0, 103.0, 104.0, 102.0, 4.0, 3.0, prev)  # flip
    assert sl == 115.0 and st == (-1.0, 4.0, 103.0, 103.0)
    off = (0.0, 0.0, 0.0, 0.0)
    assert chandelier_sl(0.0, 100.0, 100.0, 100.0, 2.0, 3.0, None) == (0.0, off)
    assert chandelier_sl(1.0, 100.0, 100.0, 100.0, 0.0, 3.0, None) == (0.0, off)
    assert chandelier_sl(1.0, 100.0, 100.0, 100.0, 2.0, 0.0, None) == (0.0, off)
    assert chandelier_sl(1.0, 100.0, 100.0, 100.0, math.nan, 3.0, None) == (0.0, off)


def test_best_worst_trade_per_book():
    steps = [
        step(1000, [{"book": "A", "targets": {"X": 1.0}, "signal": 1.0}], {"X": fill(100.0)}),
        step(1010, [{"book": "A", "targets": {"X": 1.0}, "signal": 1.0}], {"X": fill(110.0)}),  # long +10
        step(1010, [{"book": "A", "targets": {"X": 0.0}, "signal": 0.0}], {"X": fill(110.0)}),  # flat -> close +10
        step(1010, [{"book": "A", "targets": {"X": -1.0}, "signal": -1.0}], {"X": fill(110.0)}),# open short
        step(1005, [{"book": "A", "targets": {"X": -1.0}, "signal": -1.0}], {"X": fill(115.0)}),# short -5
        step(1005, [{"book": "A", "targets": {"X": 0.0}, "signal": 0.0}], {"X": fill(115.0)}),  # flat -> close -5
    ]
    r = attribute(steps)
    assert r["A"]["trades"] == 2
    assert r["A"]["wins"] == 1
    assert r["A"]["best"] == 10.0
    assert r["A"]["worst"] == -5.0
