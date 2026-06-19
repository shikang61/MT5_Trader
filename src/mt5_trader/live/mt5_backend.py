"""Pick the MetaTrader5 backend for this platform and export it as `mt5`.

Windows (Azure VPS): the native win32 `MetaTrader5` package.
Linux (Northflank): the `mt5linux` RPC client, which proxies the same API to a
MetaTrader5 instance running under Wine on the same host (server on :18812).

Importing this module never raises: if neither backend is reachable (e.g. a dev
mac, or the bridge is not up yet) `mt5` is None and MT5Gateway() raises a clear
error. `IS_BRIDGE` is True when running against the Linux bridge — the gateway
uses it to pick its reconnect strategy.
"""
from __future__ import annotations

import os

IS_BRIDGE = bool(os.environ.get("MT5_BRIDGE"))

try:
    import MetaTrader5 as mt5            # Windows: native win32 package
except ImportError:                      # Linux/Northflank: mt5linux RPC bridge
    try:
        from mt5linux import MetaTrader5
        mt5 = MetaTrader5(
            host=os.environ.get("MT5_BRIDGE_HOST", "localhost"),
            port=int(os.environ.get("MT5_BRIDGE_PORT", "18812")),
        )
    except Exception:                    # mt5linux absent, or bridge unreachable
        mt5 = None
