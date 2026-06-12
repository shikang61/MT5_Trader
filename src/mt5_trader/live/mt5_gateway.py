"""Thin wrapper around the MetaTrader5 package.

Runs on the Windows VPS only — the MetaTrader5 pip package is win32-only and
talks to a running MT5 terminal on the same machine. On macOS this module
imports but MT5Gateway() raises, so research code can import the package tree.
"""
from __future__ import annotations

import polars as pl

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None


class MT5Error(RuntimeError):
    pass


class MT5Gateway:
    def __init__(self):
        if mt5 is None:
            raise MT5Error("MetaTrader5 package not available — run on the Windows VPS")
        self._tf = {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "H1": mt5.TIMEFRAME_H1,
        }

    def connect(self, login: int, password: str, server: str) -> None:
        if not mt5.initialize(login=login, password=password, server=server):
            raise MT5Error(f"initialize failed: {mt5.last_error()}")

    def shutdown(self) -> None:
        mt5.shutdown()

    def equity(self) -> float:
        info = mt5.account_info()
        if info is None:
            raise MT5Error(f"account_info failed: {mt5.last_error()}")
        return float(info.equity)

    def bars(self, symbol: str, timeframe: str = "M1", n: int = 3000) -> pl.DataFrame:
        rates = mt5.copy_rates_from_pos(symbol, self._tf[timeframe], 0, n)
        if rates is None:
            raise MT5Error(f"copy_rates failed for {symbol}: {mt5.last_error()}")
        df = pl.from_numpy(rates)
        return df.with_columns(
            ts=pl.from_epoch("time", time_unit="s").dt.replace_time_zone("UTC"),
            spread_mean=pl.col("spread") * self.point(symbol),
        ).select("ts", "open", "high", "low", "close", "spread_mean", "tick_volume")

    def point(self, symbol: str) -> float:
        info = mt5.symbol_info(symbol)
        if info is None:
            raise MT5Error(f"symbol_info failed for {symbol}")
        return float(info.point)

    def symbol_spec(self, symbol: str) -> dict:
        info = mt5.symbol_info(symbol)
        if info is None:
            raise MT5Error(f"symbol_info failed for {symbol}")
        return {
            "contract_size": float(info.trade_contract_size),
            "lot_step": float(info.volume_step),
            "lot_min": float(info.volume_min),
            "lot_max": float(info.volume_max),
        }

    def position_lots(self, symbol: str) -> float:
        """Net signed lots (buy positive)."""
        positions = mt5.positions_get(symbol=symbol) or []
        return sum(p.volume if p.type == mt5.POSITION_TYPE_BUY else -p.volume
                   for p in positions)

    def market_order(self, symbol: str, lots: float, deviation: int = 20) -> None:
        """Signed lots: positive buys, negative sells."""
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise MT5Error(f"no tick for {symbol}")
        buy = lots > 0
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": abs(lots),
            "type": mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL,
            "price": tick.ask if buy else tick.bid,
            "deviation": deviation,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            raise MT5Error(f"order_send failed: {getattr(result, 'retcode', None)} "
                           f"{getattr(result, 'comment', '')} {mt5.last_error()}")

    def reconcile(self, symbol: str, target_lots: float) -> float:
        """Trade the difference between current and target net lots.
        Returns the delta actually sent (0.0 if within one lot step)."""
        spec = self.symbol_spec(symbol)
        step = spec["lot_step"]
        delta = target_lots - self.position_lots(symbol)
        delta = round(delta / step) * step
        if abs(delta) < step:
            return 0.0
        delta = max(-spec["lot_max"], min(spec["lot_max"], delta))
        if abs(delta) < spec["lot_min"]:
            return 0.0
        self.market_order(symbol, delta)
        return delta

    def flatten(self, symbol: str) -> None:
        self.reconcile(symbol, 0.0)
