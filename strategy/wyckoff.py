"""
Wyckoff Method bank-level identification.

The Wyckoff method reads markets through the lens of the "Composite Man" (CM) —
institutional operators who accumulate positions in low-volatility trading ranges
before marking prices up, or distribute positions before marking prices down.

Bank Levels (three sources, all returned as BankLevel objects):
───────────────────────────────────────────────────────────────
1. Volume-Weighted Swing Points
   Fractal swing highs/lows confirmed by N bars each side.  When the swing bar
   itself carries above-average volume the level is given extra weight — a
   high-volume turning point is more likely to have involved institutional orders.

2. High Volume Nodes (HVN)
   The lookback window is divided into `hvn_bins` equal price buckets.  Any
   bucket whose cumulative volume exceeds `hvn_volume_threshold × avg_bucket_vol`
   is a HVN.  HVNs act as future support (if price is below) or resistance
   (if price is above) because they mark where institutions transacted at scale.

3. Previous Day / Week High / Low
   Classical institutional reference levels, unchanged from the original bot.

4. Wyckoff Trading Range (Creek / Ice)
   When price has been oscillating in a defined band for `range_lookback` bars
   and the band width falls between range_min_atr_width and range_max_atr_width
   ATRs, the upper bound (Creek) is resistance and the lower bound (Ice) is
   support.  Springs occur below Ice; Upthrusts above Creek.

Everything is look-ahead safe: only bars up to and including `as_of_index` are
ever examined.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Literal, Optional, Tuple
from datetime import datetime, timezone
import pandas as pd


# ─────────────────────────────────────────────────────────────── data types ──

@dataclass
class BankLevel:
    price: float
    kind: Literal[
        "swing_high", "swing_low",            # volume-weighted fractal pivots
        "hvn_resistance", "hvn_support",       # High Volume Nodes
        "creek", "ice",                        # Wyckoff trading-range boundaries
        "prev_day_high", "prev_day_low",
        "prev_week_high", "prev_week_low",
    ]
    formed_at: int        # unix timestamp of the bar that confirmed this level
    volume_weight: float = 1.0   # relative importance (higher = more reliable)

    @property
    def is_support(self) -> bool:
        return self.kind in ("swing_low", "hvn_support", "ice",
                             "prev_day_low", "prev_week_low")

    @property
    def is_resistance(self) -> bool:
        return self.kind in ("swing_high", "hvn_resistance", "creek",
                             "prev_day_high", "prev_week_high")


@dataclass
class TradingRange:
    """A detected Wyckoff trading range."""
    ice: float         # lower bound (support / Ice line)
    creek: float       # upper bound (resistance / Creek line)
    midpoint: float
    bar_count: int     # how many bars the range spans
    phase_hint: Literal["accumulation", "distribution", "unknown"] = "unknown"


# ──────────────────────────────────────────────────────────── helpers ──

def _to_utc_date(unix_seconds: int):
    return datetime.fromtimestamp(unix_seconds, tz=timezone.utc).date()


def _avg_volume(df: pd.DataFrame, as_of_index: int, period: int) -> float:
    start = max(0, as_of_index - period + 1)
    vols = df["volume"].iloc[start: as_of_index + 1]
    return float(vols.mean()) if len(vols) > 0 else 1.0


# ──────────────────────────────────────────────────── volume-weighted swings ──

def find_volume_weighted_swings(df: pd.DataFrame, lookback: int,
                                 avg_vol: float) -> List[BankLevel]:
    """Fractal swing highs/lows with a volume weight.

    A swing bar with above-average volume counts as 1.5×; ultra-high (>2×avg)
    counts as 2×.  These are the most reliable bank levels — the CM left a
    heavy footprint at that turning point.
    """
    levels: List[BankLevel] = []
    n = len(df)
    highs = df["high"].values
    lows = df["low"].values
    vols = df["volume"].values
    times = df["time"].values

    last_confirmable = n - 1 - lookback
    for i in range(lookback, max(lookback + 1, last_confirmable + 1)):
        window_hi = highs[i - lookback: i + lookback + 1]
        window_lo = lows[i - lookback: i + lookback + 1]

        vol_ratio = vols[i] / avg_vol if avg_vol > 0 else 1.0
        weight = 2.0 if vol_ratio > 2.0 else (1.5 if vol_ratio > 1.0 else 1.0)

        if highs[i] == window_hi.max() and (window_hi == highs[i]).sum() == 1:
            levels.append(BankLevel(price=float(highs[i]), kind="swing_high",
                                     formed_at=int(times[i]), volume_weight=weight))
        if lows[i] == window_lo.min() and (window_lo == lows[i]).sum() == 1:
            levels.append(BankLevel(price=float(lows[i]), kind="swing_low",
                                     formed_at=int(times[i]), volume_weight=weight))
    return levels


# ──────────────────────────────────────────────── High Volume Nodes ──

def find_hvn_levels(df: pd.DataFrame, current_price: float,
                     bins: int, threshold: float) -> List[BankLevel]:
    """Volume-at-price profile.  Bins with above-average cumulative volume
    become HVN bank levels.  A HVN above current price is resistance; below is
    support."""
    if len(df) < 2:
        return []

    price_min = float(df["low"].min())
    price_max = float(df["high"].max())
    if price_max <= price_min:
        return []

    bin_size = (price_max - price_min) / bins
    bucket_vol = [0.0] * bins

    for _, row in df.iterrows():
        h, l, vol = float(row.high), float(row.low), float(row.volume)
        # distribute volume evenly across the bins the bar's range touches
        lo_bin = max(0, int((l - price_min) / bin_size))
        hi_bin = min(bins - 1, int((h - price_min) / bin_size))
        if hi_bin >= lo_bin:
            share = vol / max(1, hi_bin - lo_bin + 1)
            for b in range(lo_bin, hi_bin + 1):
                bucket_vol[b] += share

    avg_bvol = sum(bucket_vol) / max(1, bins)
    if avg_bvol <= 0:
        return []

    levels: List[BankLevel] = []
    last_ts = int(df["time"].iloc[-1])
    for b, bvol in enumerate(bucket_vol):
        if bvol < threshold * avg_bvol:
            continue
        center = price_min + (b + 0.5) * bin_size
        weight = min(3.0, bvol / avg_bvol)
        kind: Literal["hvn_resistance", "hvn_support"] = (
            "hvn_resistance" if center > current_price else "hvn_support"
        )
        levels.append(BankLevel(price=round(center, 8), kind=kind,
                                 formed_at=last_ts, volume_weight=weight))
    return levels


# ──────────────────────────────────────────────── prev period levels ──

def prev_period_levels(df: pd.DataFrame, as_of_index: int,
                        period: Literal["day", "week"]) -> List[BankLevel]:
    if as_of_index <= 0:
        return []
    sub = df.iloc[: as_of_index + 1]
    dt_series = sub["time"].apply(_to_utc_date)
    current_date = dt_series.iloc[-1]

    if period == "day":
        key = dt_series
        current_key = current_date
    else:
        key = sub["time"].apply(
            lambda t: datetime.fromtimestamp(t, tz=timezone.utc).isocalendar()[:2])
        current_key = key.iloc[-1]

    prior_mask = key != current_key
    if not prior_mask.any():
        return []
    prior = sub[prior_mask]
    last_prior_key = key[prior_mask].iloc[-1]
    last_prior = prior[key[prior_mask] == last_prior_key]
    if last_prior.empty:
        return []

    hi = float(last_prior["high"].max())
    lo = float(last_prior["low"].min())
    formed_at = int(last_prior["time"].iloc[-1])

    if period == "day":
        return [BankLevel(price=hi, kind="prev_day_high", formed_at=formed_at),
                BankLevel(price=lo, kind="prev_day_low", formed_at=formed_at)]
    else:
        return [BankLevel(price=hi, kind="prev_week_high", formed_at=formed_at),
                BankLevel(price=lo, kind="prev_week_low", formed_at=formed_at)]


# ──────────────────────────────────────────── trading range (Creek / Ice) ──

def detect_trading_range(df: pd.DataFrame, atr: float,
                           min_atr_width: float, max_atr_width: float) -> Optional[TradingRange]:
    """Simple Wyckoff trading-range detector.

    A range is present when the high-low span of the window falls between
    `min_atr_width` and `max_atr_width` ATRs AND price oscillates (i.e.
    the number of crossings of the midline is above a minimum — a one-way
    trend does not qualify).
    """
    if len(df) < 10 or atr <= 0:
        return None

    range_high = float(df["high"].max())
    range_low = float(df["low"].min())
    span = range_high - range_low

    if span < min_atr_width * atr or span > max_atr_width * atr:
        return None

    midpoint = (range_high + range_low) / 2.0
    closes = df["close"].values
    crossings = sum(
        1 for j in range(1, len(closes))
        if (closes[j - 1] < midpoint) != (closes[j] < midpoint)
    )

    # require at least 3 midline crossings to call it an oscillating range
    if crossings < 3:
        return None

    # Simple phase hint based on close distribution
    avg_close = float(df["close"].mean())
    if avg_close < midpoint:
        phase: Literal["accumulation", "distribution", "unknown"] = "accumulation"
    elif avg_close > midpoint:
        phase = "distribution"
    else:
        phase = "unknown"

    return TradingRange(
        ice=range_low, creek=range_high,
        midpoint=midpoint, bar_count=len(df), phase_hint=phase
    )


# ──────────────────────────────────────────────────────── collector ──

def collect_bank_levels(df: pd.DataFrame, as_of_index: int,
                         params, atr: float) -> Tuple[List[BankLevel], Optional[TradingRange]]:
    """All bank levels visible as of bar `as_of_index` (look-ahead safe).

    Returns (levels, trading_range_or_None).
    """
    window_start = max(0, as_of_index - params.level_lookback_bars)
    sub = df.iloc[window_start: as_of_index + 1].reset_index(drop=True)

    avg_vol = _avg_volume(sub, len(sub) - 1, params.volume_avg_period)
    current_price = float(sub["close"].iloc[-1])

    levels: List[BankLevel] = []

    # 1. Volume-weighted swing highs/lows
    levels += find_volume_weighted_swings(sub, params.swing_lookback, avg_vol)

    # 2. High Volume Nodes
    if params.use_hvn_levels:
        levels += find_hvn_levels(sub, current_price,
                                    params.hvn_bins, params.hvn_volume_threshold)

    # 3. Previous day/week levels
    if params.use_prev_day_levels:
        levels += prev_period_levels(df, as_of_index, "day")
    if params.use_prev_week_levels:
        levels += prev_period_levels(df, as_of_index, "week")

    # 4. Trading range (Creek / Ice)
    trading_range: Optional[TradingRange] = None
    if params.use_trading_range:
        range_bars = min(params.range_lookback, len(sub))
        range_sub = sub.iloc[-range_bars:]
        trading_range = detect_trading_range(
            range_sub, atr,
            params.range_min_atr_width, params.range_max_atr_width
        )
        if trading_range is not None:
            levels.append(BankLevel(price=trading_range.creek, kind="creek",
                                     formed_at=int(sub["time"].iloc[-1]), volume_weight=2.0))
            levels.append(BankLevel(price=trading_range.ice, kind="ice",
                                     formed_at=int(sub["time"].iloc[-1]), volume_weight=2.0))

    return levels, trading_range


# ────────────────────────────────────────────────── zone clustering ──

from dataclasses import dataclass as _dc

@_dc
class BankZone:
    price: float
    low: float
    high: float
    kind: Literal["support", "resistance"]
    touch_count: int
    total_weight: float
    last_touch_time: int
    sources: List[str] = field(default_factory=list)


def cluster_into_zones(levels: List[BankLevel], cluster_distance: float,
                        max_zone_width: float) -> List[BankZone]:
    """Group raw bank-level touches into support/resistance zones."""
    def _cluster_side(lv_list: List[BankLevel],
                       side: Literal["support", "resistance"]) -> List[BankZone]:
        tagged = [(lv, lv.volume_weight) for lv in lv_list
                  if (side == "support" and lv.is_support) or
                     (side == "resistance" and lv.is_resistance)]
        if not tagged:
            return []
        tagged.sort(key=lambda x: x[0].price)

        zones: List[BankZone] = []
        cluster: List[tuple] = [tagged[0]]
        c_min = c_max = tagged[0][0].price

        def flush(group):
            prices = [g[0].price for g in group]
            total_w = sum(g[1] for g in group)
            zones.append(BankZone(
                price=sum(p * g[1] for p, g in zip(prices, group)) / max(total_w, 1e-9),
                low=min(prices), high=max(prices),
                kind=side, touch_count=len(group), total_weight=total_w,
                last_touch_time=max(g[0].formed_at for g in group),
                sources=sorted({g[0].kind for g in group}),
            ))

        for item in tagged[1:]:
            gap = item[0].price - cluster[-1][0].price
            span = item[0].price - c_min
            if gap <= cluster_distance and span <= max_zone_width:
                cluster.append(item)
                c_max = item[0].price
            else:
                flush(cluster)
                cluster = [item]
                c_min = c_max = item[0].price
        flush(cluster)
        return zones

    supports = _cluster_side(levels, "support")
    resistances = _cluster_side(levels, "resistance")
    return supports + resistances


def nearest_zone(zones: List[BankZone], price: float,
                  kind: Literal["support", "resistance"],
                  tolerance: float, min_touches: int = 1) -> Optional[BankZone]:
    best = None
    best_dist = None
    for z in zones:
        if z.kind != kind or z.touch_count < min_touches:
            continue
        if z.low - tolerance <= price <= z.high + tolerance:
            dist = abs(price - z.price)
            if best is None or dist < best_dist:
                best = z
                best_dist = dist
    return best


def nearest_level(levels: List[BankLevel], price: float,
                   tolerance: float,
                   kind: Literal["support", "resistance"]) -> Optional[BankLevel]:
    best = None
    best_dist = None
    for lv in levels:
        if kind == "support" and not lv.is_support:
            continue
        if kind == "resistance" and not lv.is_resistance:
            continue
        dist = abs(lv.price - price)
        if dist <= tolerance and (best_dist is None or dist < best_dist):
            best = lv
            best_dist = dist
    return best
