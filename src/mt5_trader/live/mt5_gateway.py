"""Thin wrapper around the MetaTrader5 package.

Runs on the Windows VPS only — the MetaTrader5 pip package is win32-only and
talks to a running MT5 terminal on the same machine. On macOS this module
imports but MT5Gateway() raises, so research code can import the package tree.
"""
from __future__ import annotations

import time

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
        self._creds = {"login": login, "password": password, "server": server}
        if not mt5.initialize(**self._creds):
            raise MT5Error(f"initialize failed: {mt5.last_error()}")

    def reconnect(self) -> None:
        """Re-attach after the terminal restarted (e.g. auto-update) — the
        IPC pipe dies with (-10001, 'IPC send failed')."""
        mt5.shutdown()
        if not mt5.initialize(**self._creds):
            raise MT5Error(f"reconnect failed: {mt5.last_error()}")

    def shutdown(self) -> None:
        mt5.shutdown()

    def equity(self) -> float:
        info = mt5.account_info()
        if info is None:
            raise MT5Error(f"account_info failed: {mt5.last_error()}")
        return float(info.equity)

    def server_offset_s(self, symbol: str) -> int:
        """Broker server clock minus UTC, rounded to 30 min. MT5 timestamps are
        server time (often UTC+2/+3); strategies need true UTC for session logic."""
        tick = mt5.symbol_info_tick(symbol)
        if tick is None or tick.time == 0:
            return 0
        return round((tick.time - time.time()) / 1800) * 1800

    def bars(self, symbol: str, timeframe: str = "M1", n: int = 3000) -> pl.DataFrame:
        mt5.symbol_select(symbol, True)  # add to Market Watch (non-visible symbols, e.g. XAGUSD)
        rates = mt5.copy_rates_from_pos(symbol, self._tf[timeframe], 0, n)
        if rates is None:
            raise MT5Error(f"copy_rates failed for {symbol}: {mt5.last_error()}")
        offset = self.server_offset_s(symbol)
        df = pl.from_numpy(rates)
        return df.with_columns(
            ts=pl.from_epoch(pl.col("time") - offset, time_unit="s").dt.replace_time_zone("UTC"),
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

    def _send(self, request: dict) -> None:
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            raise MT5Error(f"order_send failed: {getattr(result, 'retcode', None)} "
                           f"{getattr(result, 'comment', '')} {mt5.last_error()}")

    def _min_stop_dist(self, symbol: str) -> float:
        info = mt5.symbol_info(symbol)
        return float(info.trade_stops_level * info.point) if info else 0.0

    def market_order(self, symbol: str, lots: float, deviation: int = 20,
                     sl: float = 0.0, tp: float = 0.0,
                     position_ticket: int | None = None) -> None:
        """Signed lots: positive buys, negative sells. sl/tp are absolute
        prices (0 = none). position_ticket closes/reduces that position
        (required on hedging accounts, else a counter-order opens a new one)."""
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
        if sl:
            request["sl"] = sl
        if tp:
            request["tp"] = tp
        if position_ticket is not None:
            request["position"] = position_ticket
        self._send(request)

    def limit_order(self, symbol: str, lots: float, price: float,
                    sl: float = 0.0, tp: float = 0.0,
                    expire_minutes: int | None = None) -> None:
        """Pending limit order, signed lots. Optional expiry (minutes from now)."""
        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": abs(lots),
            "type": mt5.ORDER_TYPE_BUY_LIMIT if lots > 0 else mt5.ORDER_TYPE_SELL_LIMIT,
            "price": price,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }
        if sl:
            request["sl"] = sl
        if tp:
            request["tp"] = tp
        if expire_minutes:
            tick = mt5.symbol_info_tick(symbol)
            request["type_time"] = mt5.ORDER_TIME_SPECIFIED
            request["expiration"] = tick.time + expire_minutes * 60
        self._send(request)

    def cancel_pending(self, symbol: str) -> int:
        """Remove all pending orders on symbol. Returns count cancelled."""
        orders = mt5.orders_get(symbol=symbol) or []
        for o in orders:
            self._send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
        return len(orders)

    def set_position_sltp(self, symbol: str, sl: float = 0.0, tp: float = 0.0) -> None:
        """Attach/replace SL/TP on every open position for symbol."""
        for p in mt5.positions_get(symbol=symbol) or []:
            if abs(p.sl - sl) > 1e-9 or abs(p.tp - tp) > 1e-9:
                self._send({"action": mt5.TRADE_ACTION_SLTP, "symbol": symbol,
                            "position": p.ticket, "sl": sl, "tp": tp})

    def reconcile(self, symbol: str, target_lots: float,
                  sl: float = 0.0, tp: float = 0.0) -> float:
        """Trade the difference between current and target net lots; closes
        opposing positions by ticket first (hedging-account safe), then opens
        the remainder with optional protective sl/tp (absolute prices).
        Returns the net delta actually sent (0.0 if within one lot step)."""
        spec = self.symbol_spec(symbol)
        step = spec["lot_step"]
        delta = target_lots - self.position_lots(symbol)
        delta = round(delta / step) * step
        if abs(delta) < max(step, spec["lot_min"]):
            return 0.0
        delta = max(-spec["lot_max"], min(spec["lot_max"], delta))

        remaining = delta
        positions = mt5.positions_get(symbol=symbol) or []
        for p in positions:
            if remaining == 0:
                break
            signed = p.volume if p.type == mt5.POSITION_TYPE_BUY else -p.volume
            if signed * remaining < 0:  # position opposes the delta -> close it
                close = min(p.volume, abs(remaining))
                self.market_order(symbol, close if remaining > 0 else -close,
                                  position_ticket=p.ticket)
                remaining += close if remaining < 0 else -close

        if abs(remaining) >= max(step, spec["lot_min"]):
            self.market_order(symbol, remaining, sl=sl, tp=tp)
        if (sl or tp) and self.position_lots(symbol) != 0:
            self.set_position_sltp(symbol, sl, tp)
        return delta

    def flatten(self, symbol: str) -> None:
        self.reconcile(symbol, 0.0)
