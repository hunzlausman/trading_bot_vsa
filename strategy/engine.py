"""
VSA Bank Levels + Wyckoff Method strategy engine.

Signal pipeline (evaluated on every completed bar):
───────────────────────────────────────────────────
1.  Collect Wyckoff bank levels (volume-weighted swings, HVN, range Creek/Ice,
    prev day/week H/L) from bars up to and including `as_of_index`.

2.  Cluster raw touches into support / resistance zones (ATR-scaled).

3.  Classify the current bar via Volume Spread Analysis:
      • Was the bar's wick a Spring (pierced below support, closed above)?
      • Was it an Upthrust (pierced above resistance, closed below)?
      • No Supply / No Demand / Selling Climax / Buying Climax?
      • Sign of Strength / Sign of Weakness?

4.  Each VSA pattern maps to a BUY or SELL signal (gated by its `enable_*`
    flag in StrategyParams).

5.  Long-only filter: discard SELL signals when `long_only` is True.

6.  Optional indicator filters (EMA / RSI / volume / HTF trend) — same set
    as the original bot, unchanged.

7.  ATR-based stop-loss / take-profit sizing.

8.  Optional signal reversal (same as original bot).

This module is drop-in compatible with the original engine:
  • Same `evaluate_bar(df, as_of_index, params, pip_size, htf_df)` signature
  • Same `Signal` dataclass fields
  • Same `infer_pip_size(symbol_digits)` helper
The backtest runner, live loop, and websocket routes need no changes.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Literal
import pandas as pd

from strategy import wyckoff as wy
from strategy import vsa as vsa_mod
from strategy import indicators as ind_mod
from strategy import filters as filters_mod

PIP_SIZE_DEFAULT = 0.0001


# ─────────────────────────────────────────────────── Signal ──

@dataclass
class Signal:
    direction: Literal["buy", "sell"]
    entry_price: float
    stop_loss: float
    take_profit: float
    reason: str
    level_price: float
    level_kind: str


# ─────────────────────────────────────────── SL/TP sizing ──

def _sl_tp_long(entry: float, structural_low: float, atr: Optional[float],
                 params, pip_size: float):
    if params.use_atr_stops and atr:
        sl = structural_low - atr * params.atr_sl_multiplier
    else:
        sl = structural_low - params.sl_buffer_pips * pip_size
    risk = entry - sl
    if risk <= 0:
        return None
    tp_rr = entry + risk * params.risk_reward
    tp_atr = entry + atr * params.atr_tp_multiplier if (params.use_atr_stops and atr) else tp_rr
    tp = max(tp_rr, tp_atr)
    return sl, tp


def _sl_tp_short(entry: float, structural_high: float, atr: Optional[float],
                  params, pip_size: float):
    if params.use_atr_stops and atr:
        sl = structural_high + atr * params.atr_sl_multiplier
    else:
        sl = structural_high + params.sl_buffer_pips * pip_size
    risk = sl - entry
    if risk <= 0:
        return None
    tp_rr = entry - risk * params.risk_reward
    tp_atr = entry - atr * params.atr_tp_multiplier if (params.use_atr_stops and atr) else tp_rr
    tp = min(tp_rr, tp_atr)
    return sl, tp


# ───────────────────────────────────────── average volume helper ──

def _avg_volume(df: pd.DataFrame, as_of_index: int, period: int) -> float:
    start = max(0, as_of_index - period + 1)
    vols = df["volume"].iloc[start: as_of_index + 1]
    return float(vols.mean()) if len(vols) > 0 else 1.0


# ──────────────────────────────────────────────── main evaluator ──

def evaluate_bar(df: pd.DataFrame, as_of_index: int, params,
                  pip_size: float = PIP_SIZE_DEFAULT,
                  htf_df: Optional[pd.DataFrame] = None) -> Optional[Signal]:
    """Evaluate one completed bar for a VSA+Wyckoff signal.

    Look-ahead safe: only uses df.iloc[:as_of_index+1].
    `htf_df` is accepted for API compatibility (used by the HTF trend filter
    in filters.py via the existing htf_trend_at resample approach) but not
    needed for the VSA/Wyckoff zone analysis itself.
    """
    min_warmup = max(params.swing_lookback * 2 + 2, params.atr_period + 2,
                     params.volume_avg_period + 2, 10)
    if as_of_index < min_warmup:
        return None

    cur = df.iloc[as_of_index]
    o, h, l, c, vol = (float(cur.open), float(cur.high), float(cur.low),
                        float(cur.close), float(cur.volume))

    # ── ATR (required for zone clustering and SL/TP) ─────────────────
    atr = ind_mod.atr_at(df, as_of_index, params.atr_period)
    if atr is None or atr <= 0:
        return None

    # ── Average volume for VSA classification ────────────────────────
    avg_vol = _avg_volume(df, as_of_index, params.volume_avg_period)

    # ── Wyckoff bank levels + zones ──────────────────────────────────
    raw_levels, trading_range = wy.collect_bank_levels(df, as_of_index, params, atr)

    cluster_dist = atr * params.zone_cluster_atr_multiplier
    max_zone_width = atr * params.zone_max_width_atr_multiplier
    zones = wy.cluster_into_zones(raw_levels, cluster_dist, max_zone_width)

    tolerance = atr * params.zone_tolerance_atr_multiplier
    min_touches = params.min_zone_touches

    # ── Context flags for VSA detector ──────────────────────────────
    # Is the current bar's close/low/high near a support or resistance zone?
    nearest_sup = wy.nearest_zone(zones, c, "support", tolerance, min_touches)
    nearest_res = wy.nearest_zone(zones, c, "resistance", tolerance, min_touches)

    near_support = nearest_sup is not None
    near_resistance = nearest_res is not None

    # Spring: low wicked below support zone's low
    wicked_below = False
    spring_zone: Optional[wy.BankZone] = None
    if not near_support:
        # Check if any support zone's low was pierced by this bar's wick
        for z in zones:
            if z.kind != "support" or z.touch_count < min_touches:
                continue
            pen = z.low - l   # positive if wick went below zone
            reclaimed = c > z.low
            if pen >= params.spring_min_penetration_atr * atr and reclaimed:
                wicked_below = True
                spring_zone = z
                break
    else:
        # Also check for Spring when bar is near the support zone
        for z in zones:
            if z.kind != "support" or z.touch_count < min_touches:
                continue
            pen = z.low - l
            reclaimed = c > z.low
            if pen >= params.spring_min_penetration_atr * atr and reclaimed:
                wicked_below = True
                spring_zone = z
                break

    # Upthrust: high wicked above resistance zone's high
    wicked_above = False
    upthrust_zone: Optional[wy.BankZone] = None
    for z in zones:
        if z.kind != "resistance" or z.touch_count < min_touches:
            continue
        pen = h - z.high   # positive if wick went above zone
        reclosed = c < z.high
        if pen >= params.upthrust_min_penetration_atr * atr and reclosed:
            wicked_above = True
            upthrust_zone = z
            break

    # ── VSA pattern detection ────────────────────────────────────────
    pattern = vsa_mod.detect_vsa_pattern(
        o, h, l, c, vol, atr, avg_vol,
        near_support=near_support,
        near_resistance=near_resistance,
        wicked_below_support=wicked_below,
        wicked_above_resistance=wicked_above,
        wide_mult=params.vsa_wide_spread_mult,
        narrow_mult=params.vsa_narrow_spread_mult,
        high_vol_mult=params.vsa_high_volume_mult,
        ultra_vol_mult=params.vsa_ultra_high_volume_mult,
        low_vol_mult=params.vsa_low_volume_mult,
    )

    if pattern is None:
        return None

    # ── Gate on enabled signals ──────────────────────────────────────
    enable_map = {
        "spring": params.enable_spring,
        "upthrust": params.enable_upthrust,
        "no_supply": params.enable_no_supply,
        "no_demand": params.enable_no_demand,
        "selling_climax": params.enable_selling_climax,
        "buying_climax": params.enable_buying_climax,
        "sign_of_strength": params.enable_sos,
        "sign_of_weakness": params.enable_sow,
        "test_after_sos": params.enable_no_supply,  # grouped with no_supply gate
    }
    if not enable_map.get(pattern.kind, False):
        return None

    # ── Build the actual signal ──────────────────────────────────────
    signal: Optional[Signal] = None

    if pattern.direction == "bullish":
        direction = "buy"
        # Pick the best supporting bank level/zone for SL placement
        if spring_zone is not None:
            structural_low = min(l, spring_zone.low)
            lv_price, lv_kind = spring_zone.price, f"zone_{spring_zone.kind}"
            lv_desc = (f"{', '.join(spring_zone.sources)} zone "
                       f"({spring_zone.low:.5f}–{spring_zone.high:.5f}, "
                       f"{spring_zone.touch_count} touches)")
        elif nearest_sup is not None:
            structural_low = min(l, nearest_sup.low)
            lv_price, lv_kind = nearest_sup.price, f"zone_{nearest_sup.kind}"
            lv_desc = (f"{', '.join(nearest_sup.sources)} zone "
                       f"({nearest_sup.low:.5f}–{nearest_sup.high:.5f}, "
                       f"{nearest_sup.touch_count} touches)")
        else:
            # Fall back to the lowest support level nearby (raw, not clustered)
            lv = wy.nearest_level(raw_levels, l, tolerance * 3, "support")
            if lv is None:
                return None
            structural_low = min(l, lv.price)
            lv_price, lv_kind = lv.price, lv.kind
            lv_desc = f"{lv.kind} @ {lv.price:.5f}"

        sized = _sl_tp_long(c, structural_low, atr, params, pip_size)
        if sized is None:
            return None
        sl, tp = sized
        signal = Signal(
            direction=direction, entry_price=c, stop_loss=sl, take_profit=tp,
            reason=f"[{pattern.kind.upper().replace('_',' ')}] "
                   f"VSA {pattern.spread}-spread, {pattern.volume_cat.replace('_','-')} vol, "
                   f"close-{pattern.close_pos} | bank level: {lv_desc} "
                   f"(conf {pattern.confidence:.0%})",
            level_price=lv_price, level_kind=lv_kind,
        )

    else:  # bearish
        direction = "sell"
        if upthrust_zone is not None:
            structural_high = max(h, upthrust_zone.high)
            lv_price, lv_kind = upthrust_zone.price, f"zone_{upthrust_zone.kind}"
            lv_desc = (f"{', '.join(upthrust_zone.sources)} zone "
                       f"({upthrust_zone.low:.5f}–{upthrust_zone.high:.5f}, "
                       f"{upthrust_zone.touch_count} touches)")
        elif nearest_res is not None:
            structural_high = max(h, nearest_res.high)
            lv_price, lv_kind = nearest_res.price, f"zone_{nearest_res.kind}"
            lv_desc = (f"{', '.join(nearest_res.sources)} zone "
                       f"({nearest_res.low:.5f}–{nearest_res.high:.5f}, "
                       f"{nearest_res.touch_count} touches)")
        else:
            lv = wy.nearest_level(raw_levels, h, tolerance * 3, "resistance")
            if lv is None:
                return None
            structural_high = max(h, lv.price)
            lv_price, lv_kind = lv.price, lv.kind
            lv_desc = f"{lv.kind} @ {lv.price:.5f}"

        sized = _sl_tp_short(c, structural_high, atr, params, pip_size)
        if sized is None:
            return None
        sl, tp = sized
        signal = Signal(
            direction=direction, entry_price=c, stop_loss=sl, take_profit=tp,
            reason=f"[{pattern.kind.upper().replace('_',' ')}] "
                   f"VSA {pattern.spread}-spread, {pattern.volume_cat.replace('_','-')} vol, "
                   f"close-{pattern.close_pos} | bank level: {lv_desc} "
                   f"(conf {pattern.confidence:.0%})",
            level_price=lv_price, level_kind=lv_kind,
        )

    # ── Long-only filter ──────────────────────────────────────────────
    if signal is not None and params.long_only and signal.direction == "sell":
        return None

    # ── Optional indicator filters ────────────────────────────────────
    if signal is not None:
        ok, _why = filters_mod.passes_filters(df, as_of_index, params,
                                               direction=signal.direction)
        if not ok:
            return None

    # ── Optional signal reversal ──────────────────────────────────────
    if signal is not None and params.reverse_signals:
        risk_d = abs(signal.entry_price - signal.stop_loss)
        reward_d = abs(signal.take_profit - signal.entry_price)
        if signal.direction == "buy":
            new_dir = "sell"
            new_sl = signal.entry_price + risk_d
            new_tp = signal.entry_price - reward_d
        else:
            new_dir = "buy"
            new_sl = signal.entry_price - risk_d
            new_tp = signal.entry_price + reward_d
        signal = Signal(
            direction=new_dir, entry_price=signal.entry_price,
            stop_loss=new_sl, take_profit=new_tp,
            reason=f"[REVERSED] {signal.reason}",
            level_price=signal.level_price, level_kind=signal.level_kind,
        )

    return signal


def infer_pip_size(symbol_digits: int) -> float:
    """MT5 digits → pip size (same as original bot)."""
    point = 10 ** (-symbol_digits)
    return point * 10 if symbol_digits in (3, 5) else point
