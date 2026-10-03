"""
Volume Spread Analysis (VSA) pattern detection.

VSA reads each bar through three lenses simultaneously:
  • SPREAD  — the bar's high-low range relative to recent ATR (effort)
  • VOLUME  — relative to the recent average (professional participation)
  • CLOSE   — where the bar closed within its own range (result / verdict)

The core VSA insight: when effort and result are mismatched, institutions
are at work.  A wide down bar that closes near its high on ultra-high volume
shows that ALL that selling was absorbed — the "smart money" was buying into
the weakness.  A narrow up bar on low volume shows there are no real buyers
— the up move has no institutional backing.

Patterns detected (named per Wyckoff/VSA convention):
────────────────────────────────────────────────────────
Bullish (→ BUY when near a support bank level)
  • Spring         — wick below support, close back above (Wyckoff #1)
  • No Supply      — narrow-spread down bar, low volume, close in upper half
  • Selling Climax — wide-spread down bar, ultra-high volume, close near top
                     (buyers absorbed all selling — exhaustion low)
  • Sign of Strength (SOS) — wide-spread up bar, high volume, close at top,
                              breaks above resistance
  • Test after SOS — narrow-spread, low-volume pullback to breakout area

Bearish (→ SELL when near a resistance bank level)
  • Upthrust       — wick above resistance, close back below (Wyckoff #2)
  • No Demand      — narrow-spread up bar, low volume, close in lower half
  • Buying Climax  — wide-spread up bar, ultra-high volume, close near bottom
                     (sellers absorbed all buying — exhaustion high)
  • Sign of Weakness (SOW) — wide-spread down bar, high volume, close at bottom,
                              breaks below support

The detector is stateless: given a single bar's OHLCV and the contextual
metrics (ATR, average volume, proximity to bank levels), it returns the best
matching pattern or None.  The engine in engine.py decides whether the
pattern + bank-level combination constitutes a tradeable signal.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal, Optional


# ─────────────────────────────────────────────────── data types ──

@dataclass
class VSAPattern:
    kind: Literal[
        "spring", "upthrust",
        "no_supply", "no_demand",
        "selling_climax", "buying_climax",
        "sign_of_strength", "sign_of_weakness",
        "test_after_sos",
    ]
    direction: Literal["bullish", "bearish"]
    signal_low: float
    signal_high: float
    close: float
    spread: Literal["wide", "average", "narrow"]
    volume_cat: Literal["ultra_high", "high", "average", "low", "ultra_low"]
    close_pos: Literal["high", "mid", "low"]   # position within the bar's range
    confidence: float    # 0.0–1.0; higher = cleaner pattern


# ─────────────────────────────────────────── classification helpers ──

def _spread(bar_range: float, atr: float,
             wide_mult: float, narrow_mult: float) -> Literal["wide", "average", "narrow"]:
    if atr <= 0:
        return "average"
    r = bar_range / atr
    if r > wide_mult:
        return "wide"
    if r < narrow_mult:
        return "narrow"
    return "average"


def _volume(vol: float, avg_vol: float,
             high_mult: float, ultra_mult: float,
             low_mult: float) -> Literal["ultra_high", "high", "average", "low", "ultra_low"]:
    if avg_vol <= 0:
        return "average"
    r = vol / avg_vol
    if r > ultra_mult:
        return "ultra_high"
    if r > high_mult:
        return "high"
    if r > low_mult:
        return "average"
    if r > low_mult * 0.4:
        return "low"
    return "ultra_low"


def _close_pos(o: float, h: float, l: float, c: float) -> Literal["high", "mid", "low"]:
    rng = h - l
    if rng < 1e-10:
        return "mid"
    ratio = (c - l) / rng
    if ratio >= 0.67:
        return "high"
    if ratio >= 0.33:
        return "mid"
    return "low"


def _close_pos_mid_low(o: float, h: float, l: float, c: float) -> Literal["high", "mid", "low"]:
    return _close_pos(o, h, l, c)


# ─────────────────────────────────────────────── main detector ──

def detect_vsa_pattern(
    o: float, h: float, l: float, c: float, vol: float,
    atr: float, avg_vol: float,
    # context flags from engine.py (derived from bank levels)
    near_support: bool,
    near_resistance: bool,
    wicked_below_support: bool,    # bar low pierced below a support level
    wicked_above_resistance: bool,  # bar high pierced above a resistance level
    # VSA thresholds from params
    wide_mult: float,
    narrow_mult: float,
    high_vol_mult: float,
    ultra_vol_mult: float,
    low_vol_mult: float,
) -> Optional[VSAPattern]:
    """Return the best VSA pattern for this bar, or None."""
    bar_range = h - l
    sp = _spread(bar_range, atr, wide_mult, narrow_mult)
    vc = _volume(vol, avg_vol, high_vol_mult, ultra_vol_mult, low_vol_mult)
    cp = _close_pos(o, h, l, c)
    is_up = c > o
    is_dn = c < o

    # ── 1. SPRING (highest priority — Wyckoff primary reversal) ──────────
    # Wick pierces below a support level then closes back above:
    # stop-loss hunters below the level have been trapped.
    # Volume can be low (no supply below) or high (climactic then absorbed).
    if wicked_below_support and cp in ("high", "mid"):
        conf = 0.9 if (vc in ("low", "ultra_low") and sp == "narrow") else \
               0.95 if vc in ("ultra_high", "high") else 0.8
        return VSAPattern("spring", "bullish", l, h, c, sp, vc, cp, conf)

    # ── 2. UPTHRUST (highest priority — Wyckoff primary reversal) ────────
    # Wick pierces above a resistance level then closes back below:
    # breakout buyers above the level have been trapped.
    if wicked_above_resistance and cp in ("low", "mid"):
        conf = 0.9 if (vc in ("low", "ultra_low") and sp == "narrow") else \
               0.95 if vc in ("ultra_high", "high") else 0.8
        return VSAPattern("upthrust", "bearish", l, h, c, sp, vc, cp, conf)

    # ── 3. SELLING CLIMAX / STOPPING VOLUME ──────────────────────────────
    # Wide-spread down bar, ultra-high/high volume, close in UPPER half
    # → Buyers absorbed all the selling; exhaustion low near a support level.
    if is_dn and sp == "wide" and vc in ("ultra_high", "high") and cp in ("high", "mid") \
       and (near_support or wicked_below_support):
        conf = 0.9 if vc == "ultra_high" else 0.8
        return VSAPattern("selling_climax", "bullish", l, h, c, sp, vc, cp, conf)

    # ── 4. BUYING CLIMAX ──────────────────────────────────────────────────
    # Wide-spread up bar, ultra-high volume, close in LOWER half
    # → Sellers absorbed all the buying; exhaustion high near a resistance level.
    if is_up and sp == "wide" and vc in ("ultra_high", "high") and cp in ("low", "mid") \
       and (near_resistance or wicked_above_resistance):
        conf = 0.9 if vc == "ultra_high" else 0.8
        return VSAPattern("buying_climax", "bearish", l, h, c, sp, vc, cp, conf)

    # ── 5. SIGN OF STRENGTH (SOS) ─────────────────────────────────────────
    # Wide-spread up bar, high volume, close near TOP
    # Breaking above or emerging from a resistance zone.
    if is_up and sp == "wide" and vc in ("high", "ultra_high") and cp == "high" \
       and (near_resistance or wicked_above_resistance):
        conf = 0.85
        return VSAPattern("sign_of_strength", "bullish", l, h, c, sp, vc, cp, conf)

    # ── 6. SIGN OF WEAKNESS (SOW) ────────────────────────────────────────
    # Wide-spread down bar, high volume, close near BOTTOM
    # Breaking below or emerging from a support zone.
    if is_dn and sp == "wide" and vc in ("high", "ultra_high") and cp == "low" \
       and (near_support or wicked_below_support):
        conf = 0.85
        return VSAPattern("sign_of_weakness", "bearish", l, h, c, sp, vc, cp, conf)

    # ── 7. NO SUPPLY ──────────────────────────────────────────────────────
    # Narrow-spread down bar, low/ultra-low volume, close in upper half
    # near a support level → no more sellers are coming in; accumulation near.
    if near_support and is_dn and sp == "narrow" \
       and vc in ("low", "ultra_low") and cp in ("high", "mid"):
        conf = 0.75
        return VSAPattern("no_supply", "bullish", l, h, c, sp, vc, cp, conf)

    # ── 8. NO DEMAND ──────────────────────────────────────────────────────
    # Narrow-spread up bar, low/ultra-low volume, close in lower half
    # near a resistance level → no real buyers; distribution near.
    if near_resistance and is_up and sp == "narrow" \
       and vc in ("low", "ultra_low") and cp in ("low", "mid"):
        conf = 0.75
        return VSAPattern("no_demand", "bearish", l, h, c, sp, vc, cp, conf)

    # ── 9. TEST AFTER SOS ─────────────────────────────────────────────────
    # Narrow-spread, low-volume pullback to a breakout area (support zone)
    # after a prior Sign of Strength.  Shows the level is now support.
    if near_support and is_dn and sp in ("narrow", "average") \
       and vc in ("low", "ultra_low") and cp in ("high", "mid"):
        conf = 0.70
        return VSAPattern("test_after_sos", "bullish", l, h, c, sp, vc, cp, conf)

    return None
