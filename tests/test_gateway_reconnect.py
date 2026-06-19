"""reconnect(): bridge mode is fatal-to-process (SystemExit so supervisord
relaunches with a fresh rpyc connection); Windows mode re-initializes in place."""
import importlib
import sys

import pytest


def _load_gateway(monkeypatch, bridge: bool):
    monkeypatch.setenv("MT5_BRIDGE", "1") if bridge else monkeypatch.delenv("MT5_BRIDGE", raising=False)
    sys.modules.pop("mt5_trader.live.mt5_backend", None)
    sys.modules.pop("mt5_trader.live.mt5_gateway", None)
    return importlib.import_module("mt5_trader.live.mt5_gateway")


class _FakeMT5:
    def __init__(self):
        self.calls = []
        self.TIMEFRAME_M1 = self.TIMEFRAME_M5 = self.TIMEFRAME_M15 = self.TIMEFRAME_H1 = 0

    def initialize(self, **kw):
        self.calls.append(("initialize", kw))
        return True

    def shutdown(self):
        self.calls.append(("shutdown", {}))


def test_reconnect_bridge_is_fatal(monkeypatch):
    gw_mod = _load_gateway(monkeypatch, bridge=True)
    fake = _FakeMT5()
    monkeypatch.setattr(gw_mod, "mt5", fake)
    gw = gw_mod.MT5Gateway()
    gw._creds = {"login": 1, "password": "x", "server": "s"}
    with pytest.raises(SystemExit):
        gw.reconnect()


def test_reconnect_windows_reinitializes(monkeypatch):
    gw_mod = _load_gateway(monkeypatch, bridge=False)
    fake = _FakeMT5()
    monkeypatch.setattr(gw_mod, "mt5", fake)
    gw = gw_mod.MT5Gateway()
    gw._creds = {"login": 1, "password": "x", "server": "s"}
    gw.reconnect()
    assert ("shutdown", {}) in fake.calls
    assert any(c[0] == "initialize" for c in fake.calls)
