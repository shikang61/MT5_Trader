"""Protective-stop clamping + non-fatal SLTP modify.

A chandelier disaster stop computed on a fast (M1) book can land within a tick of
the live market. The broker then rejects the SLTP modify with retcode 10016
"Invalid stops"; because a failed send never updates the position's stored SL, the
runner re-sends the same too-tight stop every cycle — a 10016 storm that also
aborts the whole portfolio step (the STEP log and any later symbol are skipped).

`clamp_sl` widens a too-tight/through-market stop to a valid distance so the
broker accepts it; and an invalid-stops rejection on a protective stop must not
abort the trading cycle.
"""
import types

import pytest

from mt5_trader.live import mt5_gateway as gwmod
from mt5_trader.live.mt5_gateway import MT5Error, MT5Gateway, clamp_sl

BUY, SELL = 0, 1
DONE, INVALID_STOPS, MARKET_CLOSED = 10009, 10016, 10018


# --- clamp_sl: pure ---------------------------------------------------------

def test_clamp_widens_tight_short_above_ask():
    # short (side<0): SL must sit >= ask + min_dist; a too-tight stop is widened up.
    assert clamp_sl(-1.0, 4116.974, bid=4116.75, ask=4116.97, min_dist=0.2) == pytest.approx(4117.17)


def test_clamp_widens_tight_long_below_bid():
    # long (side>0): SL must sit <= bid - min_dist; widened down.
    assert clamp_sl(1.0, 3999.95, bid=4000.0, ask=4000.1, min_dist=0.2) == pytest.approx(3999.8)


def test_clamp_leaves_safe_stop_unchanged():
    # short stop already well above the ask -> untouched.
    assert clamp_sl(-1.0, 4130.0, bid=4116.75, ask=4116.97, min_dist=0.2) == 4130.0


def test_clamp_zero_passes_through():
    # 0 = no stop -> never invent one.
    assert clamp_sl(-1.0, 0.0, bid=4116.75, ask=4116.97, min_dist=0.2) == 0.0


# --- set_position_sltp: wiring ----------------------------------------------

class Pos:
    def __init__(self, ticket, volume, ptype, magic=0, sl=0.0, tp=0.0):
        self.ticket, self.volume, self.type = ticket, volume, ptype
        self.magic, self.sl, self.tp = magic, sl, tp


class FakeMT5:
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = BUY, SELL
    TRADE_RETCODE_DONE, TRADE_RETCODE_INVALID_STOPS = DONE, INVALID_STOPS
    TRADE_ACTION_DEAL, TRADE_ACTION_SLTP, TRADE_ACTION_REMOVE, TRADE_ACTION_PENDING = 1, 2, 3, 4
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 0

    def __init__(self, positions, bid=4116.75, ask=4116.97, stops_level=0, reject=0):
        self.positions = {p.ticket: p for p in positions}
        self.bid, self.ask, self.stops_level, self.reject = bid, ask, stops_level, reject
        self.sent = []

    def symbol_info(self, s):
        return types.SimpleNamespace(trade_contract_size=100.0, volume_step=0.01,
                                     volume_min=0.01, volume_max=10.0, point=0.01,
                                     digits=2, trade_stops_level=self.stops_level)

    def symbol_info_tick(self, s):
        return types.SimpleNamespace(bid=self.bid, ask=self.ask, time=1_700_000_000)

    def positions_get(self, symbol=None):
        return list(self.positions.values())

    def orders_get(self, symbol=None):
        return []

    def order_send(self, req):
        self.sent.append(req)
        if req["action"] == self.TRADE_ACTION_SLTP:
            if self.reject:
                return types.SimpleNamespace(retcode=self.reject, comment="rejected")
            p = self.positions.get(req["position"])
            if p:
                p.sl, p.tp = req.get("sl", p.sl), req.get("tp", p.tp)
        return types.SimpleNamespace(retcode=DONE, comment="ok")

    def last_error(self):
        return (0, "ok")


def _gw(monkeypatch, fake):
    monkeypatch.setattr(gwmod, "mt5", fake)
    return MT5Gateway.__new__(MT5Gateway)


def test_sltp_sends_valid_clamped_stop(monkeypatch):
    # Short position; requested SL sits at the ask (invalid). Gateway clamps it
    # above the ask by the spread so the broker accepts it.
    fake = FakeMT5([Pos(1, 0.02, SELL, magic=7)])  # bid 4116.75 / ask 4116.97
    gw = _gw(monkeypatch, fake)
    gw.set_position_sltp("XAUUSD", sl=4116.97, magic=7)
    sltp = [r for r in fake.sent if r["action"] == FakeMT5.TRADE_ACTION_SLTP]
    assert len(sltp) == 1
    assert sltp[0]["sl"] > fake.ask                      # valid distance above market
    assert fake.positions[1].sl == sltp[0]["sl"]         # attached


def test_sltp_no_restorm_when_unchanged(monkeypatch):
    # After a valid stop attaches, the same request next cycle sends NOTHING —
    # the storm is gone because the (clamped) SL now matches the stored one.
    fake = FakeMT5([Pos(1, 0.02, SELL, magic=7)])
    gw = _gw(monkeypatch, fake)
    gw.set_position_sltp("XAUUSD", sl=4116.97, magic=7)
    gw.set_position_sltp("XAUUSD", sl=4116.97, magic=7)
    sltp = [r for r in fake.sent if r["action"] == FakeMT5.TRADE_ACTION_SLTP]
    assert len(sltp) == 1


def test_sltp_invalid_stops_does_not_abort_cycle(monkeypatch):
    # Broker still rejects the protective stop as invalid (e.g. freeze level):
    # set_position_sltp must swallow it so the trading cycle is not aborted.
    fake = FakeMT5([Pos(1, 0.02, SELL, magic=7)], reject=INVALID_STOPS)
    gw = _gw(monkeypatch, fake)
    gw.set_position_sltp("XAUUSD", sl=4200.0, magic=7)   # must not raise


def test_sltp_other_rejection_still_raises(monkeypatch):
    # A non-invalid-stops rejection is a real error and must still propagate.
    fake = FakeMT5([Pos(1, 0.02, SELL, magic=7)], reject=MARKET_CLOSED)
    gw = _gw(monkeypatch, fake)
    with pytest.raises(MT5Error):
        gw.set_position_sltp("XAUUSD", sl=4200.0, magic=7)


def test_send_error_message_carries_request_context(monkeypatch):
    class RejectMT5:
        TRADE_RETCODE_DONE = DONE
        def order_send(self, req):
            return types.SimpleNamespace(retcode=MARKET_CLOSED, comment="closed")
        def last_error(self):
            return (0, "")
    monkeypatch.setattr(gwmod, "mt5", RejectMT5())
    gw = MT5Gateway.__new__(MT5Gateway)
    with pytest.raises(MT5Error) as ei:
        gw._send({"action": 1, "symbol": "XAUUSD", "magic": 1001})
    msg = str(ei.value)
    assert "XAUUSD" in msg and "1001" in msg and str(MARKET_CLOSED) in msg
    assert ei.value.retcode == MARKET_CLOSED


def test_swallowed_invalid_stops_notes_once_and_does_not_resend(monkeypatch):
    fake = FakeMT5([Pos(1, 0.02, SELL, magic=7)], reject=INVALID_STOPS)
    gw = _gw(monkeypatch, fake)
    events = []
    gw.on_event = lambda **kw: events.append(kw)
    gw.set_position_sltp("XAUUSD", sl=4200.0, magic=7)   # fails invalid-stops, swallowed
    gw.set_position_sltp("XAUUSD", sl=4200.0, magic=7)   # same stop -> no re-send, no re-note
    sltp = [r for r in fake.sent if r["action"] == FakeMT5.TRADE_ACTION_SLTP]
    assert len(sltp) == 1                 # second call did not re-send
    assert len(events) == 1               # noted exactly once
    assert events[0]["symbol"] == "XAUUSD" and events[0]["retcode"] == INVALID_STOPS


def test_changed_failing_stop_notes_again(monkeypatch):
    fake = FakeMT5([Pos(1, 0.02, SELL, magic=7)], reject=INVALID_STOPS)
    gw = _gw(monkeypatch, fake)
    events = []
    gw.on_event = lambda **kw: events.append(kw)
    gw.set_position_sltp("XAUUSD", sl=4200.0, magic=7)
    gw.set_position_sltp("XAUUSD", sl=4250.0, magic=7)   # different stop -> note again
    assert len(events) == 2


def test_non_invalid_rejection_still_raises_in_sltp(monkeypatch):
    fake = FakeMT5([Pos(1, 0.02, SELL, magic=7)], reject=MARKET_CLOSED)
    gw = _gw(monkeypatch, fake)
    with pytest.raises(MT5Error):
        gw.set_position_sltp("XAUUSD", sl=4200.0, magic=7)
