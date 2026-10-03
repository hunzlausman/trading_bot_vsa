"""
Simple ATR (Average True Range) computed incrementally and look-ahead-safe:
atr_at(df, i, period) only ever uses bars up to and including index i.

This is a plain rolling average of True Range (not Wilder's smoothed
version) — simpler to reason about and plenty accurate for sizing stops.
"""
from __future__ import annotations
from typing import Optional
import pandas as pd


def atr_at(df: pd.DataFrame, as_of_index: int, period: int) -> Optional[float]:
    """Average True Range as of bar `as_of_index`, using the `period` bars
    ending there. Returns None if there isn't enough history yet."""
    if as_of_index < period:
        return None
    sub = df.iloc[as_of_index - period: as_of_index + 1].reset_index(drop=True)
    trs = []
    for i in range(1, len(sub)):
        h = sub["high"].iloc[i]
        l = sub["low"].iloc[i]
        prev_c = sub["close"].iloc[i - 1]
        trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    if not trs:
        return None
    return sum(trs) / len(trs)
