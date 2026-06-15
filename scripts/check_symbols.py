#!/usr/bin/env python
"""Print broker symbol availability (run on the VPS, runner stopped).

Verifies the names in config/instruments.yaml exist and are tradeable before a
book goes live on them — a missing symbol errors the whole portfolio loop.

    python scripts/check_symbols.py XAUUSD XAGUSD
"""
import os
import sys

import MetaTrader5 as mt5

syms = sys.argv[1:] or ["XAUUSD", "XAGUSD"]
if not mt5.initialize(login=int(os.environ["MT5_LOGIN"]),
                      password=os.environ["MT5_PASSWORD"],
                      server=os.environ["MT5_SERVER"]):
    raise SystemExit(f"initialize failed: {mt5.last_error()}")
try:
    for s in syms:
        info = mt5.symbol_info(s)
        if info is None:
            print(f"{s}: MISSING")
        else:
            print(f"{s}: FOUND  visible={info.visible}  "
                  f"trade_mode={info.trade_mode}  spread={info.spread}  "
                  f"contract={info.trade_contract_size}")
finally:
    mt5.shutdown()
