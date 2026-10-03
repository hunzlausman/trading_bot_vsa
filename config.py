"""
Central configuration for the VSA Bank Levels + Wyckoff Method bot.

Strategy overview
-----------------
  BANK LEVELS  — identified through Volume Spread Analysis:
    • Volume-weighted swing highs/lows (high-volume turning points = institutional interest)
    • High Volume Nodes (HVN): price bins where cumulative volume is above average
    • Previous day/week high/low (classical institutional reference)
    • Trading range Creek (resistance) and Ice (support) boundaries

  SIGNALS      — Wyckoff VSA patterns confirming institutional activity:
    Bullish  →  Spring, No Supply, Selling Climax, Sign of Strength, Test after SOS
    Bearish  →  Upthrust, No Demand, Buying Climax, Sign of Weakness

  All patterns require confluence with a bank level. SL/TP are ATR-based (same
  sizing logic as the original bot). Risk management (partials, trailing, breakeven)
  is identical to the original.
"""
import os
from pydantic import BaseModel, Field
from typing import Optional, List
from dotenv import load_dotenv

load_dotenv()

# --- MT5 connection ---
MT5_LOGIN: Optional[int] = None
MT5_PASSWORD: Optional[str] = None
MT5_SERVER: Optional[str] = None
MT5_PATH: Optional[str] = None

# --- MongoDB (backtest run history) ---
# Set MONGODB_URI in a .env file next to main.py to point at a remote/authenticated server.
MONGODB_URI: str = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB: str = os.getenv("MONGODB_DB", "vsa_wyckoff_bot")

# --- Trading sessions (UTC hours, [start, end) — overlaps are intentional) ---
# A trade is attributed to every session that is open at its entry time, so a
# trade opened at 13:00 UTC counts for both London and New York.
TRADING_SESSIONS = {
    "asian":    {"label": "Asian",    "start": 0,  "end": 9},
    "london":   {"label": "London",   "start": 7,  "end": 16},
    "new_york": {"label": "New York", "start": 12, "end": 21},
}
# MT5 bar times are in the broker's server time. Hours to subtract to get UTC
# (e.g. 2 for a UTC+2 server). Exness ("m" suffix symbols) runs on UTC, so 0.
BROKER_UTC_OFFSET_HOURS: float = float(os.getenv("BROKER_UTC_OFFSET_HOURS", "0"))

TIMEFRAME_LABELS = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]
DEFAULT_TIMEFRAME = "M5"
DEFAULT_SYMBOL = "XAUUSDm"

TIMEFRAME_MINUTES = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30,
    "H1": 60, "H4": 240, "D1": 1440, "W1": 10080, "MN1": 43200,
}


class PartialLevel(BaseModel):
    r_multiple: float = Field(..., gt=0)
    percent: float = Field(..., gt=0, le=100)


class StrategyParams(BaseModel):
    """VSA Bank Levels + Wyckoff Method strategy parameters."""

    # ── Wyckoff Bank Level Detection ─────────────────────────────────────────
    swing_lookback: int = Field(5, ge=2, le=55,
        description="Bars on each side to confirm a fractal swing high/low (volume-weighted)")
    level_lookback_bars: int = Field(300, ge=55, le=5000,
        description="How many bars back to scan for raw bank-level touches")

    use_hvn_levels: bool = Field(True,
        description="Include High Volume Nodes (price bins with above-average cumulative volume) "
                    "as bank levels — these are areas where institutions transacted heavily")
    hvn_bins: int = Field(20, ge=5, le=100,
        description="Number of price bins used to build the volume-at-price profile. More bins = "
                    "finer granularity but more noise")
    hvn_volume_threshold: float = Field(1.5, gt=0,
        description="A price bin counts as a High Volume Node when its cumulative volume exceeds "
                    "hvn_volume_threshold × the average bin volume")

    use_trading_range: bool = Field(True,
        description="Detect the current Wyckoff trading range (consolidation zone) and use its "
                    "Creek (resistance) and Ice (support) as primary bank levels for Spring / "
                    "Upthrust detection")
    range_lookback: int = Field(60, ge=20, le=500,
        description="Bars to look back when detecting a trading range")
    range_min_atr_width: float = Field(2.0, gt=0,
        description="A range must span at least this many ATRs to be considered a valid "
                    "Wyckoff trading range (avoids flagging tiny consolidations as ranges)")
    range_max_atr_width: float = Field(10.0, gt=0,
        description="A range must span no more than this many ATRs (avoids treating a long "
                    "trend move as a 'range')")

    use_prev_day_levels: bool = True
    use_prev_week_levels: bool = True

    zone_cluster_atr_multiplier: float = Field(0.6, gt=0,
        description="Two bank-level touches merge into one zone if they're within this many ATRs "
                    "of each other")
    zone_max_width_atr_multiplier: float = Field(2.5, gt=0,
        description="Hard cap on a zone's total span in ATRs")
    zone_tolerance_atr_multiplier: float = Field(0.4, gt=0,
        description="Extra buffer (ATRs) around a zone when checking if price is 'at' it")
    min_zone_touches: int = Field(1, ge=1, le=10,
        description="Zone must have at least this many touches to count as a valid bank level")

    # ── VSA Spread / Volume Thresholds ───────────────────────────────────────
    vsa_wide_spread_mult: float = Field(1.5, gt=0,
        description="Bar range > vsa_wide_spread_mult × ATR = wide-spread bar (strong effort)")
    vsa_narrow_spread_mult: float = Field(0.5, gt=0,
        description="Bar range < vsa_narrow_spread_mult × ATR = narrow-spread bar (low effort)")
    vsa_high_volume_mult: float = Field(1.5, gt=0,
        description="Volume > vsa_high_volume_mult × average = high volume bar")
    vsa_ultra_high_volume_mult: float = Field(2.0, gt=0,
        description="Volume > vsa_ultra_high_volume_mult × average = ultra-high volume (climax signal)")
    vsa_low_volume_mult: float = Field(0.7, gt=0,
        description="Volume < vsa_low_volume_mult × average = low volume (no professional interest)")
    volume_avg_period: int = Field(20, ge=5, le=200,
        description="Bars used to calculate the average volume reference for VSA classification")

    # ── VSA Signal Enables ───────────────────────────────────────────────────
    enable_spring: bool = Field(True,
        description="Spring (Wyckoff): price wicks BELOW the Ice/support bank level then closes "
                    "back above it — stop-hunt reversal. The primary Wyckoff accumulation signal. "
                    "→ BUY")
    enable_upthrust: bool = Field(True,
        description="Upthrust (Wyckoff): price wicks ABOVE the Creek/resistance bank level then "
                    "closes back below it — distribution stop-hunt reversal. → SELL")
    enable_no_supply: bool = Field(True,
        description="No Supply (VSA): narrow-spread, low-volume down bar near a support bank "
                    "level — no sellers willing to push lower, accumulation is near. → BUY")
    enable_no_demand: bool = Field(True,
        description="No Demand (VSA): narrow-spread, low-volume up bar near a resistance bank "
                    "level — no buyers willing to push higher, distribution is near. → SELL")
    enable_selling_climax: bool = Field(True,
        description="Selling Climax / Stopping Volume (VSA): wide-spread down bar with ultra-high "
                    "volume and close in the upper half — buyers have absorbed all selling at or "
                    "near a support level. → BUY")
    enable_buying_climax: bool = Field(True,
        description="Buying Climax (VSA): wide-spread up bar with ultra-high volume and close in "
                    "the lower half — sellers absorbing all buying at or near a resistance level. "
                    "→ SELL")
    enable_sos: bool = Field(True,
        description="Sign of Strength (Wyckoff/VSA): wide-spread up bar with high volume, close "
                    "near the top, breaking above resistance — institutional markup beginning. → BUY")
    enable_sow: bool = Field(True,
        description="Sign of Weakness (Wyckoff/VSA): wide-spread down bar with high volume, close "
                    "near the bottom, breaking below support — institutional markdown beginning. → SELL")

    # Spring / Upthrust require the wick to penetrate a level by at least this amount
    spring_min_penetration_atr: float = Field(0.1, gt=0,
        description="Spring: the wick must pierce below the support level by at least this many "
                    "ATRs to count as a genuine stop-hunt (not just a tag)")
    upthrust_min_penetration_atr: float = Field(0.1, gt=0,
        description="Upthrust: the wick must pierce above the resistance level by at least this "
                    "many ATRs")

    long_only: bool = Field(True,
        description="Discard any SELL signal regardless of which VSA pattern produced it")
    reverse_signals: bool = Field(False,
        description="Flip every signal to the opposite direction (applied last, after all filters)")

    # ── Risk / Trade Management (identical to original bot) ─────────────────
    risk_reward: float = Field(2.0, gt=0)
    sl_buffer_pips: float = Field(3.0, ge=0)
    use_atr_stops: bool = Field(True)
    atr_period: int = Field(14, ge=2, le=100)
    atr_sl_multiplier: float = Field(2.0, gt=0,
        description="Stop-loss = pattern extreme ± this many ATRs")
    atr_tp_multiplier: float = Field(5.0, gt=0,
        description="Take-profit distance in ATRs (used as floor alongside risk_reward×risk)")
    risk_percent: float = Field(1.0, gt=0, le=100)
    fixed_volume: Optional[float] = Field(0.01)
    max_concurrent_trades: int = Field(1, ge=1, le=5)

    # ── Partial TP / Trailing (identical to original bot) ───────────────────
    enable_partial_tp: bool = True
    partial_tp_levels: List[PartialLevel] = Field(
        default_factory=lambda: [PartialLevel(r_multiple=1.0, percent=50.0)])
    enable_sl_trailing: bool = True
    breakeven_at_r: float = Field(1.0, gt=0)
    trail_start_at_r: float = Field(1.5, gt=0)
    trail_atr_multiplier: float = Field(2.5, gt=0)

    # ── Optional Filters (identical to original bot) ─────────────────────────
    enable_ema_filter: bool = False
    ema_period: int = Field(200, ge=2, le=500)
    enable_rsi_filter: bool = False
    rsi_period: int = Field(14, ge=2, le=100)
    rsi_oversold_max: float = Field(45.0, ge=0, le=100)
    enable_volume_filter: bool = False
    volume_multiplier: float = Field(1.3, gt=0)

    enable_htf_trend_filter: bool = Field(True,
        description="Require the higher-timeframe trend to agree with the trade direction")
    htf_minutes: int = Field(60, ge=5, le=1440)
    htf_ema_period: int = Field(55, ge=2, le=300)

    # ── Backtest ──────────────────────────────────────────────────────────────
    starting_balance: float = 10000.0
    contract_size: float = Field(100.0)
    playback_ms_per_bar: int = Field(120, ge=0, le=5000)
