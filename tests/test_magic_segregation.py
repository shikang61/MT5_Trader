"""Magic-number segregation: with a magic set, the gateway must see and touch
ONLY that strategy's positions, so two strategies can share one account without
fighting. Exercises reconcile/position_lots/flatten against a fake `mt5`."""
import types

import pytest

from mt5_trader.live import mt5_gateway as gwmod
from mt5_trader.live.mt5_gateway import MT5Gateway

BUY, SELL = 0, 1
DONE = 10009


class Pos:
    def __init__(self, ticket, volume, ptype, magic, sl=0.0, tp=0.0):
        self.ticket, self.volume, self.type = ticket, volume, ptype
        self.magic, self.sl, self.tp = magic, sl, tp


class FakeMT5:
    """Minimal MT5 stand-in: positions live in a dict; order_send simulates
    closes (by position ticket) and opens (new tagged position)."""
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = BUY, SELL
    TRADE_RETCODE_DONE = DONE
    TRADE_ACTION_DEAL, TRADE_ACTION_SLTP, TRADE_ACTION_REMOVE, TRADE_ACTION_PENDING = 1, 2, 3, 4
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    ORDER_TYPE_BUY_LIMIT, ORDER_TYPE_SELL_LIMIT = 2, 3
    ORDER_TIME_GTC, ORDER_TIME_SPECIFIED = 0, 1
    ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1

    def __init__(self, positions):
        self.positions = {p.ticket: p for p in positions}
        self._next = 9000
        self.sent = []

    def symbol_info(self, symbol):
        return types.SimpleNamespace(trade_contract_size=100.0, volume_step=0.01,
                                     volume_min=0.01, volume_max=10.0, point=0.01,
                                     digits=2, trade_stops_level=0)

    def orders_get(self, symbol=None):
        return []

    def symbol_info_tick(self, symbol):
        return types.SimpleNamespace(bid=4000.0, ask=4000.1, time=1_700_000_000)

    def positions_get(self, symbol=None):
        return list(self.positions.values())

    def order_send(self, req):
        self.sent.append(req)
        if req["action"] == self.TRADE_ACTION_DEAL:
            tk = req.get("position")
            if tk is not None:                       # close/reduce a position
                p = self.positions.get(tk)
                if p:
                    p.volume = round(p.volume - req["volume"], 8)
                    if p.volume <= 1e-9:
                        del self.positions[tk]
            else:                                    # open a new position
                self._next += 1
                ptype = BUY if req["type"] == self.ORDER_TYPE_BUY else SELL
                self.positions[self._next] = Pos(self._next, req["volume"], ptype,
                                                 req.get("magic", 0))
        return types.SimpleNamespace(retcode=DONE, comment="ok")

    def last_error(self):
        return (0, "ok")


@pytest.fixture
def gw(monkeypatch):
    g = MT5Gateway.__new__(MT5Gateway)  # skip __init__ (which needs real mt5)
    return g


def _install(monkeypatch, positions):
    fake = FakeMT5(positions)
    monkeypatch.setattr(gwmod, "mt5", fake)
    return fake


def test_position_lots_filters_by_magic(monkeypatch, gw):
    _install(monkeypatch, [Pos(1, 0.10, BUY, 1001), Pos(2, 0.05, SELL, 2002)])
    assert gw.position_lots("XAUUSD", magic=1001) == pytest.approx(0.10)
    assert gw.position_lots("XAUUSD", magic=2002) == pytest.approx(-0.05)
    assert gw.position_lots("XAUUSD") == pytest.approx(0.05)  # None = net of all


def test_reconcile_touches_only_its_own_magic(monkeypatch, gw):
    fake = _install(monkeypatch, [Pos(1, 0.10, BUY, 1001), Pos(2, 0.05, SELL, 2002)])
    # portfolio (magic 1001) wants flat -> must close only ticket 1, never ticket 2
    gw.reconcile("XAUUSD", 0.0, magic=1001)
    assert 2 in fake.positions and fake.positions[2].magic == 2002  # untouched
    assert 1 not in fake.positions                                  # closed
    assert all(s.get("position") != 2 for s in fake.sent)           # never referenced


def test_reconcile_trails_sl_on_held_position_without_trade(monkeypatch, gw):
    # Held position already at target (delta < lot step): no trade, but a configured
    # disaster-stop SL must still attach/trail on it — else a steady position rides
    # unprotected while the chandelier stop is recomputed every cycle.
    fake = _install(monkeypatch, [Pos(1, 0.05, SELL, 1001, sl=0.0)])
    delta = gw.reconcile("XAUUSD", -0.05, sl=4400.0, magic=1001)  # target == current
    assert delta == 0.0                                           # no trade sent
    sltp = [s for s in fake.sent if s["action"] == FakeMT5.TRADE_ACTION_SLTP]
    assert len(sltp) == 1 and sltp[0]["position"] == 1
    assert sltp[0]["sl"] == pytest.approx(4400.0)


def test_reconcile_no_sltp_when_stop_disabled(monkeypatch, gw):
    # delta < lot step AND no sl/tp -> nothing sent at all (stop off, no churn).
    fake = _install(monkeypatch, [Pos(1, 0.05, SELL, 1001, sl=0.0)])
    gw.reconcile("XAUUSD", -0.05, magic=1001)
    assert fake.sent == []


def test_new_orders_carry_magic(monkeypatch, gw):
    fake = _install(monkeypatch, [])
    gw.reconcile("XAUUSD", 0.05, magic=2002)
    opened = [p for p in fake.positions.values()]
    assert len(opened) == 1 and opened[0].magic == 2002


def test_flatten_only_its_own_magic(monkeypatch, gw):
    fake = _install(monkeypatch, [Pos(1, 0.10, BUY, 1001), Pos(2, 0.05, SELL, 2002)])
    gw.flatten("XAUUSD", magic=2002)
    assert 1 in fake.positions and 2 not in fake.positions


def test_set_position_sltp_no_resend_when_rounds_to_existing(monkeypatch, gw):
    # Position already carries the broker-rounded SL (4220.49, 2 digits). The bot
    # recomputes a sub-tick chandelier SL (4220.490666) that rounds to the SAME
    # 2-digit value -> set_position_sltp must treat it as no-change and send NOTHING.
    # Else the broker rejects the SLTP with retcode 10025 "No changes" and the live
    # step() throws every bar, freezing the strategy.
    fake = _install(monkeypatch, [Pos(1, 0.03, SELL, 1001, sl=4220.49)])
    gw.set_position_sltp("XAUUSD", sl=4220.490666666667, magic=1001)
    assert fake.sent == []


def test_set_position_sltp_rounds_value_sent(monkeypatch, gw):
    # A genuinely new SL is rounded to the symbol's digits before sending, matching
    # market_order/limit_order, so the value broker-stores equals what we compare next.
    fake = _install(monkeypatch, [Pos(1, 0.03, SELL, 1001, sl=0.0)])
    gw.set_position_sltp("XAUUSD", sl=4220.490666666667, magic=1001)
    sltp = [s for s in fake.sent if s["action"] == FakeMT5.TRADE_ACTION_SLTP]
    assert len(sltp) == 1 and sltp[0]["sl"] == 4220.49


def test_limit_order_rounds_price_to_digits(monkeypatch, gw):
    fake = _install(monkeypatch, [])
    # 3-decimal mid-derived price + sub-tick sl/tp must snap to 2 digits (10015 fix)
    gw.limit_order("XAUUSD", 0.05, price=4321.457, sl=4312.463, tp=4326.248, magic=2002)
    req = fake.sent[-1]
    assert req["price"] == 4321.46 and req["sl"] == 4312.46 and req["tp"] == 4326.25
    assert req["magic"] == 2002
