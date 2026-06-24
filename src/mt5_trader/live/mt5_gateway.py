"""Thin wrapper around the MetaTrader5 package.

Runs on the Windows VPS only — the MetaTrader5 pip package is win32-only and
talks to a running MT5 terminal on the same machine. On macOS this module
imports but MT5Gateway() raises, so research code can import the package tree.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import polars as pl

from .broker_pnl import realized_by_magic

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None


def _struct_to_df(arr) -> pl.DataFrame:
    """Structured ndarray (mt5.copy_ticks_range / copy_rates) -> DataFrame.

    polars 1.41 `from_numpy` panics (AsSliceError, aborts the process below the
    `except Exception` guard) on a length-1 structured array — which a quiet flush
    that picked up a single tick produces. Build column-wise through Python lists,
    sidestepping the numpy zero-copy slice path that panics."""
    if arr.dtype.names:
        return pl.DataFrame({n: arr[n].tolist() for n in arr.dtype.names})
    return pl.from_numpy(arr)


def clamp_sl(side: float, sl: float, bid: float, ask: float, min_dist: float) -> float:
    """Push a protective stop to a valid distance from the live market so the
    broker can't reject it as 10016 "Invalid stops". side>0=long (the stop must
    sit at least `min_dist` below the bid), side<0=short (at least `min_dist`
    above the ask). A 0 SL (no stop) passes through unchanged, and a stop already
    a safe distance away is left alone — this only ever widens a too-tight one."""
    if not sl:
        return sl
    if side > 0:
        return min(sl, bid - min_dist)
    return max(sl, ask + min_dist)


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

    def connect(self, login: int, password: str, server: str,
                path: str | None = None) -> None:
        """`path` = a specific terminal64.exe to attach to (omit for the default /
        running terminal). Needed to drive several ACCOUNTS at once: one MT5
        terminal serves one account, so each account needs its own terminal +
        process. reconnect() reuses these creds, path included."""
        self._creds = {"login": login, "password": password, "server": server}
        if path:
            self._creds["path"] = path
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

    def realized_pnl(self, magic: int, since_utc: datetime) -> float:
        """Net realized P&L (profit+commission+swap) for `magic`'s CLOSED deals
        since `since_utc`, from broker deal history. Broker truth, so it is
        restart-safe (re-derived, not accumulated in-process). Mirrors the
        deal-window pattern in dashboard/remote_probe.py."""
        deals = mt5.history_deals_get(
            since_utc, datetime.now(timezone.utc) + timedelta(days=1)) or []
        agg = realized_by_magic(deals, mt5.DEAL_ENTRY_OUT)
        return agg.get(magic, {}).get("net", 0.0)

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
        df = _struct_to_df(rates)
        return df.with_columns(
            ts=pl.from_epoch(pl.col("time") - offset, time_unit="s").dt.replace_time_zone("UTC"),
            spread_mean=pl.col("spread") * self.point(symbol),
        ).select("ts", "open", "high", "low", "close", "spread_mean", "tick_volume")

    def ticks_range(self, symbol: str, start_utc: datetime, end_utc: datetime) -> pl.DataFrame:
        """Broker ticks in [start_utc, end_utc] as canonical rows: ts (UTC, us),
        symbol, bid, ask, bid_sz/ask_sz (null — MT5 L1 ticks carry no per-side
        size), volume (last-trade). Tick times are server-tz like bars(); the
        same server_offset_s correction maps them (and the request bounds) to
        true UTC. Over-fetch and filter in the caller: the offset rounds to
        30 min, so request a margin wider than your flush interval."""
        mt5.symbol_select(symbol, True)
        offset = self.server_offset_s(symbol)
        frm = datetime.utcfromtimestamp(start_utc.timestamp() + offset)  # server wall clock
        to = datetime.utcfromtimestamp(end_utc.timestamp() + offset)
        ticks = mt5.copy_ticks_range(symbol, frm, to, mt5.COPY_TICKS_ALL)
        if ticks is None:
            raise MT5Error(f"copy_ticks_range failed for {symbol}: {mt5.last_error()}")
        df = _struct_to_df(ticks)
        if df.is_empty():
            return df
        vol = "volume_real" if "volume_real" in df.columns else "volume"
        return df.with_columns(
            ts=(pl.from_epoch(pl.col("time_msc") - offset * 1000, time_unit="ms")
                .dt.replace_time_zone("UTC").dt.cast_time_unit("us")),
            symbol=pl.lit(symbol),
            bid_sz=pl.lit(None, dtype=pl.Float64),
            ask_sz=pl.lit(None, dtype=pl.Float64),
            volume=pl.col(vol).cast(pl.Float64),
        ).select("ts", "symbol", "bid", "ask", "bid_sz", "ask_sz", "volume").sort("ts")

    def point(self, symbol: str) -> float:
        info = mt5.symbol_info(symbol)
        if info is None:
            raise MT5Error(f"symbol_info failed for {symbol}")
        return float(info.point)

    def quote(self, symbol: str) -> tuple[float, float]:
        """Current (bid, ask)."""
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise MT5Error(f"no tick for {symbol}")
        return float(tick.bid), float(tick.ask)

    def _round_price(self, symbol: str, price: float) -> float:
        """Snap a price to the symbol's tick precision. Computed levels (mids,
        ATR offsets) carry sub-tick decimals the broker rejects (10015)."""
        if not price:
            return price
        info = mt5.symbol_info(symbol)
        return round(float(price), info.digits) if info else float(price)

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

    @staticmethod
    def _mine(positions, magic: int | None):
        """Filter broker positions/orders to this strategy's magic. magic=None
        means manage everything on the symbol (single-strategy / legacy)."""
        positions = positions or []
        if magic is None:
            return list(positions)
        return [p for p in positions if getattr(p, "magic", 0) == magic]

    def position_lots(self, symbol: str, magic: int | None = None) -> float:
        """Net signed lots (buy positive) for this magic (None = all)."""
        positions = self._mine(mt5.positions_get(symbol=symbol), magic)
        return sum(p.volume if p.type == mt5.POSITION_TYPE_BUY else -p.volume
                   for p in positions)

    def _send(self, request: dict) -> None:
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = getattr(result, "retcode", None)
            err = MT5Error(
                f"order_send failed: retcode={retcode} "
                f"action={request.get('action')} symbol={request.get('symbol')} "
                f"magic={request.get('magic')} "
                f"comment={getattr(result, 'comment', '')!r} {mt5.last_error()}")
            err.retcode = retcode
            raise err

    def _note(self, **kw) -> None:
        """Emit a structured event to an optional sink (set by the runner). No-op
        if unset — keeps the gateway import-safe and usable without a logger."""
        cb = getattr(self, "on_event", None)
        if cb is not None:
            cb(**kw)

    def _min_stop_dist(self, symbol: str) -> float:
        info = mt5.symbol_info(symbol)
        return float(info.trade_stops_level * info.point) if info else 0.0

    def market_order(self, symbol: str, lots: float, deviation: int = 20,
                     sl: float = 0.0, tp: float = 0.0,
                     position_ticket: int | None = None,
                     magic: int | None = None) -> None:
        """Signed lots: positive buys, negative sells. sl/tp are absolute
        prices (0 = none). position_ticket closes/reduces that position
        (required on hedging accounts, else a counter-order opens a new one).
        magic tags the deal so multiple strategies can share one account."""
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
            dist = max(self._min_stop_dist(symbol), tick.ask - tick.bid)
            sl = clamp_sl(1.0 if buy else -1.0, sl, tick.bid, tick.ask, dist)
            request["sl"] = self._round_price(symbol, sl)
        if tp:
            request["tp"] = self._round_price(symbol, tp)
        if position_ticket is not None:
            request["position"] = position_ticket
        if magic is not None:
            request["magic"] = int(magic)
        self._send(request)

    def limit_order(self, symbol: str, lots: float, price: float,
                    sl: float = 0.0, tp: float = 0.0,
                    expire_minutes: int | None = None,
                    magic: int | None = None) -> None:
        """Pending limit order, signed lots. Optional expiry (minutes from now).
        magic tags the order so strategies can share one account."""
        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": abs(lots),
            "type": mt5.ORDER_TYPE_BUY_LIMIT if lots > 0 else mt5.ORDER_TYPE_SELL_LIMIT,
            "price": self._round_price(symbol, price),
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }
        if sl:
            request["sl"] = self._round_price(symbol, sl)
        if tp:
            request["tp"] = self._round_price(symbol, tp)
        if magic is not None:
            request["magic"] = int(magic)
        if expire_minutes:
            tick = mt5.symbol_info_tick(symbol)
            request["type_time"] = mt5.ORDER_TIME_SPECIFIED
            request["expiration"] = tick.time + expire_minutes * 60
        self._send(request)

    def cancel_pending(self, symbol: str, magic: int | None = None) -> int:
        """Remove this magic's pending orders on symbol (None = all). Returns
        count cancelled."""
        orders = self._mine(mt5.orders_get(symbol=symbol), magic)
        for o in orders:
            self._send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
        return len(orders)

    def set_position_sltp(self, symbol: str, sl: float = 0.0, tp: float = 0.0,
                          magic: int | None = None) -> None:
        """Attach/replace SL/TP on this magic's open positions for symbol."""
        # Round to the symbol's digits first (as market_order/limit_order do): the
        # broker stores SL/TP rounded, so an unrounded sub-tick value always differs
        # from p.sl by >1e-9 and re-sends every bar -> retcode 10025 "No changes".
        tick = mt5.symbol_info_tick(symbol)
        dist = max(self._min_stop_dist(symbol), tick.ask - tick.bid)
        tp = self._round_price(symbol, tp)
        if not hasattr(self, "_sltp_failed"):
            self._sltp_failed = {}   # ticket -> (sl, tp) that last failed invalid-stops
        for p in self._mine(mt5.positions_get(symbol=symbol), magic):
            side = 1.0 if p.type == mt5.POSITION_TYPE_BUY else -1.0
            psl = self._round_price(symbol, clamp_sl(side, sl, tick.bid, tick.ask, dist))
            if abs(p.sl - psl) <= 1e-9 and abs(p.tp - tp) <= 1e-9:
                continue
            if self._sltp_failed.get(p.ticket) == (psl, tp):
                continue  # this exact stop already failed invalid-stops; don't re-send/re-note
            try:
                self._send({"action": mt5.TRADE_ACTION_SLTP, "symbol": symbol,
                            "position": p.ticket, "sl": psl, "tp": tp})
                self._sltp_failed.pop(p.ticket, None)
            except MT5Error as e:
                # A protective-stop modify the broker rejects as an invalid stop
                # (too close / through the freeze level) must not abort the whole
                # trading cycle — clamp_sl already widened it. Memo + surface once
                # (deduped, so it can't re-create the 10016 storm); re-raise anything
                # else (a real broker fault).
                if getattr(e, "retcode", None) != mt5.TRADE_RETCODE_INVALID_STOPS:
                    raise
                self._sltp_failed[p.ticket] = (psl, tp)
                self._note(event="ERROR", symbol=symbol, retcode=e.retcode, error=repr(e))

    def reconcile(self, symbol: str, target_lots: float,
                  sl: float = 0.0, tp: float = 0.0,
                  magic: int | None = None) -> float:
        """Trade the difference between current and target net lots for this
        magic; closes opposing positions by ticket first (hedging-account safe),
        then opens the remainder with optional protective sl/tp (absolute prices).
        With a magic set, only this strategy's positions are seen/touched, so
        several strategies can share one account without netting against each
        other. Returns the net delta actually sent (0.0 if within one lot step)."""
        spec = self.symbol_spec(symbol)
        step = spec["lot_step"]
        delta = target_lots - self.position_lots(symbol, magic)
        delta = round(delta / step) * step
        if abs(delta) < max(step, spec["lot_min"]):
            # No trade needed, but keep a configured disaster stop attached and
            # trailed on the held position — else a steady position (target within
            # one lot step) rides unprotected while the chandelier SL is recomputed
            # every cycle. set_position_sltp only sends when the SL actually changes.
            if (sl or tp) and self.position_lots(symbol, magic) != 0:
                self.set_position_sltp(symbol, sl, tp, magic=magic)
            return 0.0
        delta = max(-spec["lot_max"], min(spec["lot_max"], delta))

        remaining = delta
        positions = self._mine(mt5.positions_get(symbol=symbol), magic)
        for p in positions:
            if remaining == 0:
                break
            signed = p.volume if p.type == mt5.POSITION_TYPE_BUY else -p.volume
            if signed * remaining < 0:  # position opposes the delta -> close it
                close = min(p.volume, abs(remaining))
                self.market_order(symbol, close if remaining > 0 else -close,
                                  position_ticket=p.ticket, magic=magic)
                remaining += close if remaining < 0 else -close

        if abs(remaining) >= max(step, spec["lot_min"]):
            self.market_order(symbol, remaining, sl=sl, tp=tp, magic=magic)
        if (sl or tp) and self.position_lots(symbol, magic) != 0:
            self.set_position_sltp(symbol, sl, tp, magic=magic)
        return delta

    def flatten(self, symbol: str, magic: int | None = None) -> None:
        self.reconcile(symbol, 0.0, magic=magic)
