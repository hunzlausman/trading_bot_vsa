"""
VSA Bank Levels + Wyckoff Method bot — FastAPI app.

Routes (identical to original bot):
  GET  /                         dashboard: backtest panel
  GET  /live                     live trading control panel
  GET  /api/symbols              MT5 symbol list
  GET  /api/timeframes           timeframe labels
  POST /api/backtest/start       fetch history, create backtest session
  WS   /ws/backtest/{session_id} stream bar/trade/report events
  POST /api/live/start           start live signal/trading loop
  POST /api/live/stop            stop it
  WS   /ws/live/{session_id}     stream live events

Broker: MetaTrader5 only (Windows + MT5 terminal required).
"""
from __future__ import annotations
import asyncio
import uuid
import time
import traceback
from typing import Dict, Any, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import config
import mt5_client
from models import BacktestRequest, LiveStartRequest
from backtest.runner import run_backtest
from strategy import engine as strat_engine
from strategy import indicators as ind_mod

app = FastAPI(title="VSA Bank Levels + Wyckoff Bot")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

ASSET_VERSION = str(int(time.time()))
BACKTEST_SESSIONS: Dict[str, Dict[str, Any]] = {}
LIVE_SESSIONS: Dict[str, Dict[str, Any]] = {}


# ─────────────────────────────────────────────────────── pages ──

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "default_symbol": config.DEFAULT_SYMBOL,
        "timeframes": config.TIMEFRAME_LABELS,
        "default_timeframe": config.DEFAULT_TIMEFRAME,
        "asset_version": ASSET_VERSION,
        "bot_name": "VSA Bank Levels + Wyckoff Method",
    })


@app.get("/live", response_class=HTMLResponse)
async def live_page(request: Request):
    return templates.TemplateResponse("live.html", {
        "request": request,
        "default_symbol": config.DEFAULT_SYMBOL,
        "timeframes": config.TIMEFRAME_LABELS,
        "default_timeframe": config.DEFAULT_TIMEFRAME,
        "asset_version": ASSET_VERSION,
        "bot_name": "VSA Bank Levels + Wyckoff Method",
    })


# ──────────────────────────────────────────────────── metadata ──

@app.get("/api/symbols")
async def api_symbols(search: Optional[str] = None):
    try:
        names = await asyncio.to_thread(mt5_client.list_symbols, search)
        return {"symbols": names}
    except mt5_client.MT5Error as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/timeframes")
async def api_timeframes():
    return {"timeframes": config.TIMEFRAME_LABELS}


# ─────────────────────────────────────────────────── backtest ──

@app.post("/api/backtest/start")
async def start_backtest(req: BacktestRequest):
    print(f"[backtest] {req.symbol} {req.timeframe} {req.date_from}->{req.date_to} "
          f"params={req.strategy.model_dump()}")
    try:
        df = await asyncio.to_thread(
            mt5_client.get_history, req.symbol, req.timeframe, req.date_from, req.date_to
        )
        info = await asyncio.to_thread(mt5_client.symbol_info, req.symbol)
        pip_size = strat_engine.infer_pip_size(info.digits)

        tick_size = getattr(info, "trade_tick_size", 0) or 0
        tick_value = getattr(info, "trade_tick_value", 0) or 0
        pip_value_per_lot = (
            (pip_size / tick_size) * tick_value
            if tick_size > 0 and tick_value > 0
            else pip_size * req.strategy.contract_size
        )
        print(f"[backtest] {req.symbol}: pip_size={pip_size}, "
              f"pip_value_per_lot={pip_value_per_lot}")
    except mt5_client.MT5Error as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not prepare backtest: {e}")

    session_id = str(uuid.uuid4())
    BACKTEST_SESSIONS[session_id] = {
        "df": df, "params": req.strategy, "pip_size": pip_size,
        "pip_value_per_lot": pip_value_per_lot,
        "symbol": req.symbol, "timeframe": req.timeframe,
    }
    return {"session_id": session_id, "bar_count": len(df),
            "params_used": req.strategy.model_dump()}


@app.websocket("/ws/backtest/{session_id}")
async def ws_backtest(websocket: WebSocket, session_id: str):
    await websocket.accept()
    session = BACKTEST_SESSIONS.get(session_id)
    if session is None:
        await websocket.send_json({"type": "error", "message": "Unknown session_id"})
        await websocket.close()
        return
    try:
        async for event in run_backtest(
            session["df"], session["params"], session["pip_size"],
            session["pip_value_per_lot"], session["params"].playback_ms_per_bar,
        ):
            await websocket.send_json(event)
        await websocket.close()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        traceback.print_exc()
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
            await websocket.close()
        except Exception:
            pass
    finally:
        BACKTEST_SESSIONS.pop(session_id, None)


# ──────────────────────────────────────────────────────── live ──

async def _live_loop(session_id: str):
    session = LIVE_SESSIONS[session_id]
    symbol, timeframe = session["symbol"], session["timeframe"]
    params, volume, dry_run = session["params"], session["volume"], session["dry_run"]
    queue: asyncio.Queue = session["queue"]
    last_bar_time = None
    position = None

    try:
        info = await asyncio.to_thread(mt5_client.symbol_info, symbol)
        pip_size = strat_engine.infer_pip_size(info.digits)
        await queue.put({"type": "status",
                          "message": f"Live loop started for {symbol} {timeframe} "
                                     f"({'DRY RUN' if dry_run else 'LIVE ORDERS'})"})
        needed_bars = max(params.level_lookback_bars, 300)

        while session.get("running"):
            df = await asyncio.to_thread(mt5_client.get_recent_bars, symbol, timeframe, needed_bars)
            if df.empty:
                await asyncio.sleep(2)
                continue

            latest_closed_idx = len(df) - 2
            if latest_closed_idx < 1:
                await asyncio.sleep(2)
                continue

            latest_time = int(df.iloc[latest_closed_idx].time)
            await queue.put({
                "type": "bar",
                "time": latest_time,
                "open": float(df.iloc[latest_closed_idx].open),
                "high": float(df.iloc[latest_closed_idx].high),
                "low": float(df.iloc[latest_closed_idx].low),
                "close": float(df.iloc[latest_closed_idx].close),
            })

            # Manage open position (partials + trailing/breakeven)
            if position is not None:
                live_pos = await asyncio.to_thread(mt5_client.get_position, position["ticket"])
                if live_pos is None:
                    await queue.put({"type": "trade_closed", "ticket": position["ticket"],
                                      "message": "Position no longer open (SL/TP hit or closed manually)."})
                    position = None
                else:
                    try:
                        price = await asyncio.to_thread(
                            mt5_client.current_price, symbol, position["direction"])
                        favorable = ((price - position["entry_price"]) if position["direction"] == "buy"
                                     else (position["entry_price"] - price))
                        r_now = (favorable / position["risk_per_unit"]
                                 if position["risk_per_unit"] > 0 else 0.0)

                        if params.enable_partial_tp:
                            for idx, level in enumerate(params.partial_tp_levels):
                                if idx in position["partials_taken"]:
                                    continue
                                if r_now >= level.r_multiple:
                                    partial_vol = round(
                                        min(position["original_volume"] * (level.percent / 100.0),
                                            position["remaining_volume"]), 2)
                                    if partial_vol > 0:
                                        await asyncio.to_thread(
                                            mt5_client.close_position, position["ticket"],
                                            symbol, partial_vol, position["direction"])
                                        position["remaining_volume"] = round(
                                            position["remaining_volume"] - partial_vol, 2)
                                        position["partials_taken"].add(idx)
                                        await queue.put({
                                            "type": "trade_partial",
                                            "ticket": position["ticket"],
                                            "r_multiple": level.r_multiple,
                                            "volume_closed": partial_vol,
                                            "remaining_volume": position["remaining_volume"],
                                            "price": price,
                                        })

                        if params.enable_sl_trailing:
                            atr_now = ind_mod.atr_at(df, latest_closed_idx, params.atr_period)
                            new_sl = position["current_sl"]
                            reason = None

                            if not position["breakeven_done"] and r_now >= params.breakeven_at_r:
                                be = position["entry_price"]
                                if ((position["direction"] == "buy" and be > new_sl) or
                                        (position["direction"] == "sell" and be < new_sl)):
                                    new_sl = be
                                    reason = "breakeven"
                                position["breakeven_done"] = True

                            if atr_now and r_now >= params.trail_start_at_r:
                                if position["direction"] == "buy":
                                    candidate = price - atr_now * params.trail_atr_multiplier
                                    if candidate > new_sl:
                                        new_sl = candidate
                                        reason = "trailing"
                                else:
                                    candidate = price + atr_now * params.trail_atr_multiplier
                                    if candidate < new_sl:
                                        new_sl = candidate
                                        reason = "trailing"

                            if reason is not None:
                                await asyncio.to_thread(
                                    mt5_client.modify_position_sltp,
                                    position["ticket"], symbol, new_sl, position["take_profit"])
                                position["current_sl"] = new_sl
                                await queue.put({"type": "trade_sl_moved",
                                                  "ticket": position["ticket"],
                                                  "new_sl": new_sl, "reason": reason})
                    except mt5_client.MT5Error as e:
                        await queue.put({"type": "error",
                                          "message": f"Position management failed: {e}"})

            # New entry
            if position is None and latest_time != last_bar_time:
                last_bar_time = latest_time
                signal = strat_engine.evaluate_bar(df, latest_closed_idx, params, pip_size)
                if signal is not None:
                    await queue.put({
                        "type": "signal",
                        "direction": signal.direction, "entry_price": signal.entry_price,
                        "stop_loss": signal.stop_loss, "take_profit": signal.take_profit,
                        "reason": signal.reason, "time": latest_time,
                    })
                    if not dry_run:
                        try:
                            result = await asyncio.to_thread(
                                mt5_client.place_market_order, symbol,
                                signal.direction, volume, signal.stop_loss, signal.take_profit)
                            ticket = getattr(result, "order", None)
                            position = {
                                "ticket": ticket, "direction": signal.direction,
                                "entry_price": signal.entry_price,
                                "current_sl": signal.stop_loss,
                                "take_profit": signal.take_profit,
                                "risk_per_unit": abs(signal.entry_price - signal.stop_loss),
                                "original_volume": volume, "remaining_volume": volume,
                                "partials_taken": set(), "breakeven_done": False,
                            }
                            await queue.put({"type": "trade_opened", "ticket": ticket,
                                              "direction": signal.direction,
                                              "price": signal.entry_price, "volume": volume})
                        except mt5_client.MT5Error as e:
                            await queue.put({"type": "error",
                                              "message": f"Order failed: {e}"})

            await asyncio.sleep(5)
    except Exception as e:
        traceback.print_exc()
        await queue.put({"type": "error", "message": str(e)})
    finally:
        await queue.put({"type": "status", "message": "Live loop stopped."})


@app.post("/api/live/start")
async def live_start(req: LiveStartRequest):
    session_id = str(uuid.uuid4())
    LIVE_SESSIONS[session_id] = {
        "symbol": req.symbol, "timeframe": req.timeframe,
        "volume": req.volume, "params": req.strategy,
        "dry_run": req.dry_run, "running": True, "queue": asyncio.Queue(),
    }
    LIVE_SESSIONS[session_id]["task"] = asyncio.create_task(_live_loop(session_id))
    return {"session_id": session_id}


@app.post("/api/live/stop/{session_id}")
async def live_stop(session_id: str):
    session = LIVE_SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Unknown session")
    session["running"] = False
    return {"ok": True}


@app.websocket("/ws/live/{session_id}")
async def ws_live(websocket: WebSocket, session_id: str):
    await websocket.accept()
    session = LIVE_SESSIONS.get(session_id)
    if session is None:
        await websocket.send_json({"type": "error", "message": "Unknown session_id"})
        await websocket.close()
        return
    queue: asyncio.Queue = session["queue"]
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
            if event.get("type") == "status" and "stopped" in event.get("message", ""):
                break
    except WebSocketDisconnect:
        pass


@app.on_event("shutdown")
async def on_shutdown():
    mt5_client.shutdown()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
