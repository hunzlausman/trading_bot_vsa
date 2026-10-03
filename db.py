"""
MongoDB persistence for backtest runs (collection: backtest_runs).

Each document holds the request (symbol, timeframe, dates), the full strategy
parameter set used, the overall metrics, the segregated session/day report, the
trades and a down-sampled equity curve — enough to reopen the run later.

Calls are synchronous (pymongo); the FastAPI routes wrap them in asyncio.to_thread
like the MT5 calls.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import MongoClient, DESCENDING

import config

_client: Optional[MongoClient] = None
MAX_EQUITY_POINTS = 2000


class DBError(RuntimeError):
    pass


def _runs():
    global _client
    if _client is None:
        _client = MongoClient(config.MONGODB_URI, serverSelectionTimeoutMS=3000)
    return _client[config.MONGODB_DB]["backtest_runs"]


def _downsample(points: List[Dict[str, Any]], limit: int = MAX_EQUITY_POINTS) -> List[Dict[str, Any]]:
    if len(points) <= limit:
        return points
    step = len(points) / limit
    sampled = [points[int(i * step)] for i in range(limit)]
    sampled[-1] = points[-1]
    return sampled


def _serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
    doc["id"] = str(doc.pop("_id"))
    if isinstance(doc.get("created_at"), datetime):
        doc["created_at"] = doc["created_at"].replace(tzinfo=timezone.utc).isoformat()
    return doc


def _oid(run_id: str) -> ObjectId:
    try:
        return ObjectId(run_id)
    except InvalidId:
        raise DBError(f"Invalid run id: {run_id}")


def save_run(meta: Dict[str, Any], params: Dict[str, Any], report: Dict[str, Any],
             segmented: Dict[str, Any]) -> str:
    doc = {
        "created_at": datetime.now(timezone.utc),
        "name": meta.get("name") or f"{meta['symbol']} {meta['timeframe']} "
                                     f"{meta['date_from']} → {meta['date_to']}",
        "symbol": meta["symbol"],
        "timeframe": meta["timeframe"],
        "date_from": meta["date_from"],
        "date_to": meta["date_to"],
        "bar_count": meta.get("bar_count"),
        "params": params,
        "metrics": {
            "starting_balance": report["starting_balance"],
            "ending_balance": report["ending_balance"],
            **segmented["overall"],
            # use the runner's equity-based figures so they match the balance exactly
            "net_pnl": report["net_pnl"],
            "max_drawdown": report["max_drawdown"],
        },
        "by_session": segmented["by_session"],
        "by_weekday": segmented["by_weekday"],
        "by_date": segmented["by_date"],
        "trades": segmented["trades"],
        "equity_curve": _downsample(report.get("equity_curve", [])),
    }
    return str(_runs().insert_one(doc).inserted_id)


def list_runs(limit: int = 100) -> List[Dict[str, Any]]:
    projection = {"name": 1, "created_at": 1, "symbol": 1, "timeframe": 1,
                  "date_from": 1, "date_to": 1, "metrics": 1}
    cursor = _runs().find({}, projection).sort("created_at", DESCENDING).limit(limit)
    return [_serialize(d) for d in cursor]


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    doc = _runs().find_one({"_id": _oid(run_id)})
    return _serialize(doc) if doc else None


def rename_run(run_id: str, name: str) -> bool:
    return _runs().update_one({"_id": _oid(run_id)}, {"$set": {"name": name}}).matched_count == 1


def delete_run(run_id: str) -> bool:
    return _runs().delete_one({"_id": _oid(run_id)}).deleted_count == 1
