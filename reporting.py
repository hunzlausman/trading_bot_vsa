"""
Segregated backtest reports: overall, per trading session, per weekday and per
calendar day — plus filtering by date range, time-of-day range, sessions and
weekdays.

Everything works on the list of closed-trade dicts the backtest runner emits,
so the same code serves a just-finished run and a run reloaded from MongoDB.
Trades are bucketed by their ENTRY time, converted to UTC.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta, date
from typing import List, Dict, Any, Optional, Iterable

import config

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def to_utc(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc) - timedelta(hours=config.BROKER_UTC_OFFSET_HOURS)


def sessions_at(dt: datetime) -> List[str]:
    hour = dt.hour + dt.minute / 60.0
    return [key for key, s in config.TRADING_SESSIONS.items() if s["start"] <= hour < s["end"]]


def annotate(trade: Dict[str, Any]) -> Dict[str, Any]:
    """Add UTC entry date/time, weekday and session tags to a trade dict."""
    dt = to_utc(int(trade["entry_time"]))
    return {
        **trade,
        "entry_date": dt.strftime("%Y-%m-%d"),
        "entry_hhmm": dt.strftime("%H:%M"),
        "weekday": WEEKDAYS[dt.weekday()],
        "sessions": sessions_at(dt) or ["off_hours"],
    }


def compute_metrics(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    pnls = [float(t["pnl"]) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_profit = sum(wins)
    gross_loss = -sum(losses)

    # Drawdown over this subset's own cumulative PnL (in trade-exit order)
    running = peak = max_dd = 0.0
    for t in sorted(trades, key=lambda t: t["exit_time"]):
        running += float(t["pnl"])
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)

    n = len(pnls)
    return {
        "total_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "net_pnl": round(sum(pnls), 2),
        "win_rate": round(100 * len(wins) / n, 2) if n else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else None,
        "avg_win": round(gross_profit / len(wins), 2) if wins else 0.0,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
        "expectancy": round(sum(pnls) / n, 2) if n else 0.0,
        "best_trade": round(max(pnls), 2) if pnls else 0.0,
        "worst_trade": round(min(pnls), 2) if pnls else 0.0,
        "max_drawdown": round(max_dd, 2),
    }


def _hhmm_to_minutes(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def filter_trades(trades: Iterable[Dict[str, Any]],
                  date_from: Optional[str] = None, date_to: Optional[str] = None,
                  time_from: Optional[str] = None, time_to: Optional[str] = None,
                  sessions: Optional[List[str]] = None,
                  weekdays: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """All bounds are inclusive and in UTC. A time window whose end is before its
    start wraps past midnight (e.g. 22:00 → 02:00)."""
    t_from = _hhmm_to_minutes(time_from) if time_from else None
    t_to = _hhmm_to_minutes(time_to) if time_to else None
    out = []
    for t in trades:
        if date_from and t["entry_date"] < date_from:
            continue
        if date_to and t["entry_date"] > date_to:
            continue
        if t_from is not None or t_to is not None:
            m = _hhmm_to_minutes(t["entry_hhmm"])
            lo = t_from if t_from is not None else 0
            hi = t_to if t_to is not None else 24 * 60 - 1
            inside = (lo <= m <= hi) if lo <= hi else (m >= lo or m <= hi)
            if not inside:
                continue
        if sessions and not set(sessions) & set(t["sessions"]):
            continue
        if weekdays and t["weekday"] not in weekdays:
            continue
        out.append(t)
    return out


def build_report(trades: List[Dict[str, Any]], **filters) -> Dict[str, Any]:
    annotated = [t if "sessions" in t else annotate(t) for t in trades]
    selected = filter_trades(annotated, **filters)

    session_keys = list(config.TRADING_SESSIONS) + ["off_hours"]
    by_session = []
    for key in session_keys:
        subset = [t for t in selected if key in t["sessions"]]
        if key == "off_hours" and not subset:
            continue
        s = config.TRADING_SESSIONS.get(key)
        by_session.append({
            "key": key,
            "label": s["label"] if s else "Off hours",
            "hours_utc": f"{s['start']:02d}:00–{s['end']:02d}:00" if s else "outside sessions",
            **compute_metrics(subset),
        })

    by_weekday = [{"key": wd, **compute_metrics([t for t in selected if t["weekday"] == wd])}
                  for wd in WEEKDAYS if any(t["weekday"] == wd for t in selected)]

    by_date = []
    for d in sorted({t["entry_date"] for t in selected}):
        subset = [t for t in selected if t["entry_date"] == d]
        by_date.append({"key": d, "weekday": WEEKDAYS[date.fromisoformat(d).weekday()],
                        **compute_metrics(subset)})

    return {
        "filters": {k: v for k, v in filters.items() if v},
        "overall": compute_metrics(selected),
        "by_session": by_session,
        "by_weekday": by_weekday,
        "by_date": by_date,
        "trades": selected,
        "sessions_config": config.TRADING_SESSIONS,
        "broker_utc_offset_hours": config.BROKER_UTC_OFFSET_HOURS,
    }
