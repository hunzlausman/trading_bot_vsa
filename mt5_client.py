"""
Thin wrapper around the official `MetaTrader5` Python package.

IMPORTANT: This package only works on Windows, with a MetaTrader 5 terminal
installed and logged in to a broker account (demo or live). It cannot be
installed or run on Linux/macOS. Run this whole app on the same Windows
machine as your MT5 terminal.

No other broker/data source is used anywhere in this project.
"""
from __future__ import annotations
import threading
from datetime import datetime, timezone
from typing import Optional, List

import MetaTrader5 as mt5
import pandas as pd

import config

_lock = threading.Lock()
_initialized = False

TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
    "W1": mt5.TIMEFRAME_W1,
    "MN1": mt5.TIMEFRAME_MN1,
}


class MT5Error(RuntimeError):
    pass


def ensure_connected() -> None:
    """Initialize the MT5 terminal connection once per process."""
    global _initialized
    with _lock:
        if _initialized:
            return
        kwargs = {}
        if config.MT5_PATH:
            kwargs["path"] = config.MT5_PATH
        ok = mt5.initialize(**kwargs)
        if not ok:
            raise MT5Error(f"mt5.initialize() failed: {mt5.last_error()}")
        if config.MT5_LOGIN and config.MT5_PASSWORD and config.MT5_SERVER:
            ok = mt5.login(config.MT5_LOGIN, password=config.MT5_PASSWORD, server=config.MT5_SERVER)
            if not ok:
                raise MT5Error(f"mt5.login() failed: {mt5.last_error()}")
        _initialized = True


def shutdown() -> None:
    global _initialized
    with _lock:
        if _initialized:
            mt5.shutdown()
            _initialized = False


def list_symbols(search: Optional[str] = None) -> List[str]:
    ensure_connected()
    syms = mt5.symbols_get()
    if syms is None:
        raise MT5Error(f"symbols_get failed: {mt5.last_error()}")
    names = [s.name for s in syms]
    if search:
        search = search.upper()
        names = [n for n in names if search in n.upper()]
    return sorted(names)


def symbol_info(symbol: str):
    ensure_connected()
    if not mt5.symbol_select(symbol, True):
        raise MT5Error(f"symbol_select({symbol}) failed: {mt5.last_error()}")
    info = mt5.symbol_info(symbol)
    if info is None:
        raise MT5Error(f"symbol_info({symbol}) returned None: {mt5.last_error()}")
    return info


def get_history(symbol: str, timeframe: str, date_from: str, date_to: str) -> pd.DataFrame:
    """Fetch historical OHLCV between two ISO dates (inclusive), as a DataFrame
    with columns: time (unix seconds UTC), open, high, low, close, volume.
    """
    ensure_connected()
    if timeframe not in TIMEFRAME_MAP:
        raise ValueError(f"Unknown timeframe {timeframe}")
    if not mt5.symbol_select(symbol, True):
        raise MT5Error(f"symbol_select({symbol}) failed: {mt5.last_error()}")

    dt_from = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    dt_to = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    rates = mt5.copy_rates_range(symbol, TIMEFRAME_MAP[timeframe], dt_from, dt_to)
    if rates is None or len(rates) == 0:
        raise MT5Error(f"No rates returned for {symbol} {timeframe} {date_from}->{date_to}: {mt5.last_error()}")

    df = pd.DataFrame(rates)
    df.rename(columns={"tick_volume": "volume"}, inplace=True)
    return df[["time", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def get_recent_bars(symbol: str, timeframe: str, count: int) -> pd.DataFrame:
    """Used by the live loop to poll the most recent N closed bars."""
    ensure_connected()
    if not mt5.symbol_select(symbol, True):
        raise MT5Error(f"symbol_select({symbol}) failed: {mt5.last_error()}")
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME_MAP[timeframe], 0, count)
    if rates is None:
        raise MT5Error(f"copy_rates_from_pos failed: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df.rename(columns={"tick_volume": "volume"}, inplace=True)
    return df[["time", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def place_market_order(symbol: str, direction: str, volume: float,
                        sl: float, tp: float, comment: str = "bank-levels-bot"):
    """Send a REAL market order to MT5. `direction` is 'buy' or 'sell'."""
    ensure_connected()
    info = symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise MT5Error(f"symbol_info_tick({symbol}) failed: {mt5.last_error()}")

    order_type = mt5.ORDER_TYPE_BUY if direction == "buy" else mt5.ORDER_TYPE_SELL
    price = tick.ask if direction == "buy" else tick.bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(volume),
        "type": order_type,
        "price": price,
        "sl": float(sl),
        "tp": float(tp),
        "deviation": 20,
        "magic": 990321,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        raise MT5Error(f"order_send failed: {result}")
    return result


def close_position(ticket: int, symbol: str, volume: float, direction: str):
    """Closes `volume` lots of an open position. Pass less than the full
    position size for a PARTIAL close — MT5 handles this natively."""
    ensure_connected()
    tick = mt5.symbol_info_tick(symbol)
    close_type = mt5.ORDER_TYPE_SELL if direction == "buy" else mt5.ORDER_TYPE_BUY
    price = tick.bid if direction == "buy" else tick.ask
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(volume),
        "type": close_type,
        "position": ticket,
        "price": price,
        "deviation": 20,
        "magic": 990321,
        "comment": "bank-levels-bot close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        raise MT5Error(f"close order_send failed: {result}")
    return result


def modify_position_sltp(ticket: int, symbol: str, sl: float, tp: float):
    """Modify the SL/TP of an already-open position (used for breakeven
    moves and ATR trailing) without closing or re-opening it."""
    ensure_connected()
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "symbol": symbol,
        "sl": float(sl),
        "tp": float(tp),
    }
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        raise MT5Error(f"SL/TP modify failed: {result}")
    return result


def get_position(ticket: int):
    """Returns the live position object for `ticket`, or None if it no
    longer exists (i.e. it was closed — by SL, TP, or manually)."""
    ensure_connected()
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        return None
    return positions[0]


def current_price(symbol: str, direction: str) -> float:
    """The price you'd exit at right now: bid for a long, ask for a short."""
    ensure_connected()
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise MT5Error(f"symbol_info_tick({symbol}) failed: {mt5.last_error()}")
    return tick.bid if direction == "buy" else tick.ask
