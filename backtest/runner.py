"""
Bar-by-bar backtest engine — VSA Bank Levels + Wyckoff bot.

Design unchanged from the original: async generator yielding JSON-serialisable
event dicts that the websocket route forwards to the browser in real time.

No look-ahead: bar i only sees df.iloc[:i+1].  Trade exits are evaluated
against bar i's high/low (intrabar resting order assumption).

Position management per bar (in order):
  1. Partial take-profit levels (if enabled)
  2. Breakeven + ATR trailing stop (if enabled)
  3. Full SL/TP check on remaining volume
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Set

import pandas as pd

from strategy import engine as strat_engine
from strategy import indicators as ind_mod
from config import StrategyParams


@dataclass
class OpenPosition:
    id: int
    direction: str
    entry_index: int
    entry_time: int
    entry_price: float
    original_sl: float
    stop_loss: float
    take_profit: float
    volume: float
    original_volume: float
    risk_per_unit: float
    realized_pnl: float = 0.0
    partials_taken: Set[int] = field(default_factory=set)
    breakeven_done: bool = False
    reason: str = ""


@dataclass
class ClosedTrade:
    id: int
    direction: str
    entry_time: int
    entry_price: float
    stop_loss: float
    take_profit: float
    volume: float
    exit_time: int
    exit_price: float
    exit_reason: str
    pnl: float
    partial_pnl: float
    partials: List[Dict[str, Any]]
    reason: str = ""


def _position_size(equity: float, params: StrategyParams, entry: float, sl: float,
                    pip_size: float, pip_value_per_lot: float) -> float:
    if params.fixed_volume:
        return params.fixed_volume
    risk_amount = equity * (params.risk_percent / 100.0)
    stop_pips = abs(entry - sl) / pip_size
    if stop_pips <= 0:
        return params.fixed_volume or 0.01
    volume = risk_amount / (stop_pips * pip_value_per_lot)
    return max(0.01, round(volume, 2))


def _r_multiple(pos: OpenPosition, price: float) -> float:
    if pos.risk_per_unit <= 0:
        return 0.0
    favorable = (price - pos.entry_price) if pos.direction == "buy" \
                else (pos.entry_price - price)
    return favorable / pos.risk_per_unit


def _infer_bar_seconds(df: pd.DataFrame) -> int:
    if len(df) < 2:
        return 60
    return max(1, int(df["time"].iloc[1]) - int(df["time"].iloc[0]))


async def run_backtest(df: pd.DataFrame, params: StrategyParams,
                        pip_size: float, pip_value_per_lot: float = 10.0,
                        playback_ms_per_bar: int = 120,
                        htf_df: pd.DataFrame = None):
    """Async generator — same event protocol as original bot:
      bar | trade_opened | trade_partial | trade_sl_moved |
      trade_update | trade_closed | report
    """
    equity = params.starting_balance
    balance_curve: List[Dict[str, Any]] = []
    trades: List[ClosedTrade] = []
    open_positions: List[OpenPosition] = []
    next_trade_id = 1

    n = len(df)
    # Warmup: enough bars for VSA context (swings, ATR, volume average)
    warmup = max(params.swing_lookback * 2 + 2,
                 params.atr_period + 2,
                 params.volume_avg_period + 2,
                 10)
    if params.enable_ema_filter:
        warmup = max(warmup, params.ema_period + 2)
    if params.enable_rsi_filter:
        warmup = max(warmup, params.rsi_period * 2 + 2)
    if params.enable_volume_filter:
        warmup = max(warmup, params.volume_avg_period + 2)
    if params.enable_htf_trend_filter:
        bars_per_htf = max(1, params.htf_minutes * 60 // max(1, _infer_bar_seconds(df)))
        warmup = max(warmup, (params.htf_ema_period + 2) * bars_per_htf)

    for i in range(n):
        row = df.iloc[i]
        yield {
            "type": "bar",
            "time": int(row.time), "open": float(row.open), "high": float(row.high),
            "low": float(row.low), "close": float(row.close),
        }

        atr_now = None
        if params.use_atr_stops or params.enable_sl_trailing:
            atr_now = ind_mod.atr_at(df, i, params.atr_period)

        still_open: List[OpenPosition] = []
        for pos in open_positions:
            is_buy = pos.direction == "buy"

            # 1. Partial take-profits
            if params.enable_partial_tp:
                for idx, level in enumerate(params.partial_tp_levels):
                    if idx in pos.partials_taken:
                        continue
                    target = (pos.entry_price + level.r_multiple * pos.risk_per_unit if is_buy
                              else pos.entry_price - level.r_multiple * pos.risk_per_unit)
                    reached = (row.high >= target) if is_buy else (row.low <= target)
                    if not reached:
                        continue
                    partial_vol = min(pos.original_volume * (level.percent / 100.0), pos.volume)
                    if partial_vol <= 1e-9:
                        pos.partials_taken.add(idx)
                        continue
                    leg = (target - pos.entry_price) if is_buy else (pos.entry_price - target)
                    pnl_money = leg / pip_size * pip_value_per_lot * partial_vol
                    equity += pnl_money
                    pos.realized_pnl += pnl_money
                    pos.volume -= partial_vol
                    pos.partials_taken.add(idx)
                    yield {
                        "type": "trade_partial", "id": pos.id, "time": int(row.time),
                        "price": target, "volume_closed": round(partial_vol, 2),
                        "pnl": round(pnl_money, 2), "remaining_volume": round(pos.volume, 2),
                        "r_multiple": level.r_multiple, "equity": round(equity, 2),
                    }

            if pos.volume <= 1e-9:
                closed = ClosedTrade(
                    id=pos.id, direction=pos.direction, entry_time=pos.entry_time,
                    entry_price=pos.entry_price, stop_loss=pos.stop_loss,
                    take_profit=pos.take_profit, volume=0.0, exit_time=int(row.time),
                    exit_price=float(row.close), exit_reason="partial_full",
                    pnl=round(pos.realized_pnl, 2), partial_pnl=round(pos.realized_pnl, 2),
                    partials=[], reason=pos.reason,
                )
                trades.append(closed)
                yield {"type": "trade_closed", "trade": closed.__dict__, "equity": round(equity, 2)}
                continue

            # 2. Breakeven + ATR trailing
            if params.enable_sl_trailing and atr_now:
                r_now = _r_multiple(pos, float(row.close))
                if not pos.breakeven_done and r_now >= params.breakeven_at_r:
                    be = pos.entry_price
                    moved = False
                    if is_buy and be > pos.stop_loss:
                        pos.stop_loss = be
                        moved = True
                    elif not is_buy and be < pos.stop_loss:
                        pos.stop_loss = be
                        moved = True
                    pos.breakeven_done = True
                    if moved:
                        yield {"type": "trade_sl_moved", "id": pos.id, "time": int(row.time),
                               "new_sl": pos.stop_loss, "reason": "breakeven"}
                if r_now >= params.trail_start_at_r:
                    if is_buy:
                        candidate = float(row.close) - atr_now * params.trail_atr_multiplier
                        if candidate > pos.stop_loss:
                            pos.stop_loss = candidate
                            yield {"type": "trade_sl_moved", "id": pos.id, "time": int(row.time),
                                   "new_sl": pos.stop_loss, "reason": "trailing"}
                    else:
                        candidate = float(row.close) + atr_now * params.trail_atr_multiplier
                        if candidate < pos.stop_loss:
                            pos.stop_loss = candidate
                            yield {"type": "trade_sl_moved", "id": pos.id, "time": int(row.time),
                                   "new_sl": pos.stop_loss, "reason": "trailing"}

            # 3. Full SL/TP check
            hit_sl = (row.low <= pos.stop_loss) if is_buy else (row.high >= pos.stop_loss)
            hit_tp = (row.high >= pos.take_profit) if is_buy else (row.low <= pos.take_profit)

            if hit_sl or hit_tp:
                exit_price = pos.stop_loss if hit_sl else pos.take_profit
                reason = "stop_loss" if hit_sl else "take_profit"
                leg = (exit_price - pos.entry_price) if is_buy else (pos.entry_price - exit_price)
                leg_pnl = leg / pip_size * pip_value_per_lot * pos.volume
                equity += leg_pnl
                total_pnl = pos.realized_pnl + leg_pnl
                closed = ClosedTrade(
                    id=pos.id, direction=pos.direction, entry_time=pos.entry_time,
                    entry_price=pos.entry_price, stop_loss=pos.stop_loss,
                    take_profit=pos.take_profit, volume=round(pos.volume, 2),
                    exit_time=int(row.time), exit_price=exit_price, exit_reason=reason,
                    pnl=round(total_pnl, 2), partial_pnl=round(pos.realized_pnl, 2),
                    partials=[], reason=pos.reason,
                )
                trades.append(closed)
                yield {"type": "trade_closed", "trade": closed.__dict__, "equity": round(equity, 2)}
            else:
                unreal = (row.close - pos.entry_price) if is_buy else (pos.entry_price - row.close)
                unreal_money = unreal / pip_size * pip_value_per_lot * pos.volume
                yield {
                    "type": "trade_update", "id": pos.id, "time": int(row.time),
                    "unrealized_pnl": round(unreal_money + pos.realized_pnl, 2),
                    "remaining_volume": round(pos.volume, 2), "current_sl": pos.stop_loss,
                }
                still_open.append(pos)
        open_positions = still_open
        balance_curve.append({"time": int(row.time), "equity": round(equity, 2)})

        # New entry signal
        if i >= warmup and len(open_positions) < params.max_concurrent_trades:
            signal = strat_engine.evaluate_bar(df, i, params, pip_size=pip_size, htf_df=htf_df)
            if signal is not None:
                volume = _position_size(equity, params, signal.entry_price,
                                         signal.stop_loss, pip_size, pip_value_per_lot)
                risk_per_unit = abs(signal.entry_price - signal.stop_loss)
                pos = OpenPosition(
                    id=next_trade_id, direction=signal.direction, entry_index=i,
                    entry_time=int(row.time), entry_price=signal.entry_price,
                    original_sl=signal.stop_loss, stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit, volume=volume,
                    original_volume=volume, risk_per_unit=risk_per_unit,
                    reason=signal.reason,
                )
                next_trade_id += 1
                open_positions.append(pos)
                yield {
                    "type": "trade_opened",
                    "trade": {
                        "id": pos.id, "direction": pos.direction, "entry_time": pos.entry_time,
                        "entry_price": pos.entry_price, "stop_loss": pos.stop_loss,
                        "take_profit": pos.take_profit, "volume": pos.volume,
                        "reason": pos.reason, "level_price": signal.level_price,
                        "level_kind": signal.level_kind,
                    },
                }

        if playback_ms_per_bar:
            await asyncio.sleep(playback_ms_per_bar / 1000.0)
        else:
            # Yield to the event loop so websocket keepalive pings still get answered
            await asyncio.sleep(0)

    # Force-close open positions at end of data
    if open_positions:
        last = df.iloc[-1]
        for pos in open_positions:
            exit_price = float(last.close)
            is_buy = pos.direction == "buy"
            leg = (exit_price - pos.entry_price) if is_buy else (pos.entry_price - exit_price)
            leg_pnl = leg / pip_size * pip_value_per_lot * pos.volume
            equity += leg_pnl
            total_pnl = pos.realized_pnl + leg_pnl
            closed = ClosedTrade(
                id=pos.id, direction=pos.direction, entry_time=pos.entry_time,
                entry_price=pos.entry_price, stop_loss=pos.stop_loss,
                take_profit=pos.take_profit, volume=round(pos.volume, 2),
                exit_time=int(last.time), exit_price=exit_price,
                exit_reason="end_of_data", pnl=round(total_pnl, 2),
                partial_pnl=round(pos.realized_pnl, 2), partials=[], reason=pos.reason,
            )
            trades.append(closed)
            yield {"type": "trade_closed", "trade": closed.__dict__, "equity": round(equity, 2)}

    # Final report
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    peak = params.starting_balance
    max_dd = 0.0
    for point in balance_curve:
        peak = max(peak, point["equity"])
        max_dd = max(max_dd, peak - point["equity"])

    yield {
        "type": "report",
        "starting_balance": params.starting_balance,
        "ending_balance": round(equity, 2),
        "net_pnl": round(equity - params.starting_balance, 2),
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(100 * len(wins) / len(trades), 2) if trades else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else None,
        "max_drawdown": round(max_dd, 2),
        "equity_curve": balance_curve,
        "trades": [t.__dict__ for t in trades],
    }
