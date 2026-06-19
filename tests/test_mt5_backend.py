"""mt5_backend selects the right MT5 backend. On a dev box with neither the
win32 MetaTrader5 package nor a reachable bridge, mt5 is None and IS_BRIDGE
reflects the env flag — and the gateway raises a clear error rather than
crashing at import."""
import importlib
import sys

import pytest


def _reload_backend(monkeypatch, bridge: bool):
    if bridge:
        monkeypatch.setenv("MT5_BRIDGE", "1")
    else:
        monkeypatch.delenv("MT5_BRIDGE", raising=False)
    sys.modules.pop("mt5_trader.live.mt5_backend", None)
    return importlib.import_module("mt5_trader.live.mt5_backend")


def test_backend_none_without_mt5_or_bridge(monkeypatch):
    # No MetaTrader5 wheel on the dev mac, no bridge up -> mt5 is None, no crash.
    backend = _reload_backend(monkeypatch, bridge=False)
    assert backend.mt5 is None
    assert backend.IS_BRIDGE is False


def test_is_bridge_reads_env(monkeypatch):
    backend = _reload_backend(monkeypatch, bridge=True)
    assert backend.IS_BRIDGE is True


def test_gateway_raises_clear_error_when_backend_none(monkeypatch):
    _reload_backend(monkeypatch, bridge=False)
    sys.modules.pop("mt5_trader.live.mt5_gateway", None)
    gw_mod = importlib.import_module("mt5_trader.live.mt5_gateway")
    with pytest.raises(gw_mod.MT5Error):
        gw_mod.MT5Gateway()
