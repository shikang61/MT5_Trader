from dataclasses import dataclass

from mt5_trader.live.broker_pnl import MAGIC_LABELS, realized_by_magic

OUT = 1   # sentinel for DEAL_ENTRY_OUT; IN deals use a different value


@dataclass
class Deal:
    magic: int
    entry: int
    profit: float
    commission: float
    swap: float
    symbol: str


def test_only_closing_deals_counted():
    deals = [
        Deal(2003, entry=0, profit=99.0, commission=0.0, swap=0.0, symbol="XAGUSD"),  # IN -> ignored
        Deal(2003, entry=OUT, profit=10.0, commission=-1.0, swap=-0.5, symbol="XAGUSD"),
    ]
    out = realized_by_magic(deals, entry_out=OUT)
    assert out[2003]["n"] == 1
    assert abs(out[2003]["net"] - (10.0 - 1.0 - 0.5)) < 1e-9   # profit+commission+swap
    assert abs(out[2003]["gross"] - 10.0) < 1e-9
    assert abs(out[2003]["swap"] - (-0.5)) < 1e-9
    assert out[2003]["wins"] == 1


def test_grouping_and_wins():
    deals = [
        Deal(1001, entry=OUT, profit=5.0, commission=0.0, swap=0.0, symbol="XAUUSD"),
        Deal(1001, entry=OUT, profit=-8.0, commission=0.0, swap=0.0, symbol="XAUUSD"),
        Deal(2003, entry=OUT, profit=2.0, commission=0.0, swap=0.0, symbol="XAGUSD"),
    ]
    out = realized_by_magic(deals, entry_out=OUT)
    assert out[1001]["n"] == 2 and out[1001]["wins"] == 1
    assert abs(out[1001]["net"] - (-3.0)) < 1e-9
    assert out[2003]["n"] == 1 and out[2003]["symbols"] == {"XAGUSD"}


def test_empty():
    assert realized_by_magic([], entry_out=OUT) == {}


def test_labels_present():
    assert MAGIC_LABELS[1001] and MAGIC_LABELS[2003]


def test_best_worst_tracked():
    deals = [
        Deal(1001, OUT, profit=20.0, commission=0.0, swap=0.0, symbol="XAUUSD"),   # net +20
        Deal(1001, OUT, profit=-8.0, commission=-1.0, swap=0.0, symbol="XAUUSD"),  # net  -9
        Deal(1001, OUT, profit=5.0, commission=0.0, swap=0.0, symbol="XAUUSD"),    # net  +5
    ]
    out = realized_by_magic(deals, entry_out=OUT)
    assert out[1001]["best"] == 20.0
    assert out[1001]["worst"] == -9.0
