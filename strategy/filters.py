"""
Optional additional confluence filters.

These are OFF by default. Turn one on and a signal only survives if it also
passes that check — think of them as extra votes a trade needs before it's
allowed through, on top of the bank-level + price-action confluence.

Every function here takes (df, as_of_index, ...) and only ever reads
df.iloc[:as_of_index+1] — no look-ahead, safe for both backtest and live.

--- Adding your own indicator filter ---
1. Write a `your_indicator_at(df, as_of_index, ...)` function that returns
   a value (or None if there isn't enough history yet).
2. Add `enable_your_filter: bool` and any parameters it needs to
   `StrategyParams` in config.py.
3. Add a check for it inside `passes_filters()` below, following the same
   pattern as the EMA/RSI/volume checks: compute the value, and return
   (False, "reason") if it fails.
That's it — engine.py calls passes_filters() automatically for every
candidate signal, from every signal source (sweep or pattern-based).
"""
from __future__ import annotations
from typing import Optional, Tuple
import pandas as pd


def ema_at(df: pd.DataFrame, as_of_index: int, period: int) -> Optional[float]:
    if as_of_index < period:
        return None
    closes = df["close"].iloc[: as_of_index + 1]
    return float(closes.ewm(span=period, adjust=False).mean().iloc[-1])


def rsi_at(df: pd.DataFrame, as_of_index: int, period: int) -> Optional[float]:
    if as_of_index < period + 1:
        return None
    window_start = max(0, as_of_index - period * 4)
    closes = df["close"].iloc[window_start: as_of_index + 1]
    delta = closes.diff().dropna()
    if len(delta) < period:
        return None
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean().iloc[-1]
    avg_loss = loss.rolling(period).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100 - (100 / (1 + rs)))


def avg_volume_at(df: pd.DataFrame, as_of_index: int, period: int) -> Optional[float]:
    if as_of_index < period:
        return None
    return float(df["volume"].iloc[as_of_index - period + 1: as_of_index + 1].mean())


def htf_trend_at(df: pd.DataFrame, as_of_index: int, ema_period: int, htf_minutes: int = 60) -> Optional[str]:
    """Higher-timeframe trend direction ('bullish' / 'bearish' / 'neutral'),
    built by resampling the CURRENT dataframe up into `htf_minutes` buckets
    (default 60 = 1 hour) and checking the last bucket's close against an
    EMA of bucket closes.

    Look-ahead safety: only buckets that are fully in the past relative to
    the bucket containing the current bar are used — i.e. if the current
    bar sits inside the still-forming 1H candle, that 1H candle is never
    used, only the last one that had actually closed. This works for any
    base timeframe at or below htf_minutes (M1..M30 into H1, etc.); if the
    base timeframe is already >= htf_minutes, every base bar is its own
    closed bucket and this just measures trend on the base series itself.
    """
    sub = df.iloc[: as_of_index + 1]
    htf_seconds = htf_minutes * 60
    cur_time = int(sub["time"].iloc[-1])
    current_bucket_start = cur_time - (cur_time % htf_seconds)

    bucket = (sub["time"] // htf_seconds) * htf_seconds
    closed = sub[bucket < current_bucket_start]
    if closed.empty:
        return None

    closed_bucket = (closed["time"] // htf_seconds) * htf_seconds
    grouped_close = closed.groupby(closed_bucket)["close"].last()
    if len(grouped_close) < ema_period:
        return None

    ema = grouped_close.ewm(span=ema_period, adjust=False).mean()
    last_close = float(grouped_close.iloc[-1])
    last_ema = float(ema.iloc[-1])
    if last_close > last_ema:
        return "bullish"
    if last_close < last_ema:
        return "bearish"
    return "neutral"


def passes_filters(df: pd.DataFrame, as_of_index: int, params, direction: Optional[str] = None) -> Tuple[bool, str]:
    """All enabled filters must pass. Returns (True, "") if so, else
    (False, "<why it failed>"). `direction` ("buy"/"sell") is the candidate
    signal's direction — needed by direction-aware filters like the HTF
    trend filter, so pass it whenever you have it."""
    cur = df.iloc[as_of_index]

    if params.enable_ema_filter:
        ema = ema_at(df, as_of_index, params.ema_period)
        if ema is None:
            return False, "EMA filter: not enough history yet"
        if cur.close < ema:
            return False, f"EMA{params.ema_period} filter: close {cur.close:.5f} below EMA {ema:.5f}"

    if params.enable_rsi_filter:
        rsi = rsi_at(df, as_of_index, params.rsi_period)
        if rsi is None:
            return False, "RSI filter: not enough history yet"
        if rsi > params.rsi_oversold_max:
            return False, f"RSI filter: {rsi:.1f} above max {params.rsi_oversold_max}"

    if params.enable_volume_filter:
        avg_vol = avg_volume_at(df, as_of_index, params.volume_avg_period)
        if avg_vol is None or avg_vol <= 0:
            return False, "Volume filter: not enough history yet"
        if cur.volume < avg_vol * params.volume_multiplier:
            return False, f"Volume filter: {cur.volume:.0f} below {params.volume_multiplier}x avg ({avg_vol:.0f})"

    if params.enable_htf_trend_filter and direction in ("buy", "sell"):
        trend = htf_trend_at(df, as_of_index, params.htf_ema_period, params.htf_minutes)
        if trend is None:
            return False, "HTF trend filter: not enough higher-timeframe history yet"
        wanted = "bullish" if direction == "buy" else "bearish"
        if trend != wanted:
            return False, (f"HTF trend filter: {params.htf_minutes}-min trend is {trend}, "
                            f"trade wants {wanted}")

    return True, ""
