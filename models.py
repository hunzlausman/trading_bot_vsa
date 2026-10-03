from pydantic import BaseModel
from typing import Optional, List, Literal
from config import StrategyParams


class BacktestRequest(BaseModel):
    symbol: str
    timeframe: str = "M15"
    date_from: str  # "YYYY-MM-DD"
    date_to: str    # "YYYY-MM-DD"
    strategy: StrategyParams = StrategyParams()


class Bar(BaseModel):
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class BankLevelModel(BaseModel):
    price: float
    kind: Literal[
        "swing_high", "swing_low",
        "hvn_resistance", "hvn_support",
        "creek", "ice",
        "prev_day_high", "prev_day_low",
        "prev_week_high", "prev_week_low",
    ]
    formed_at: int


class TradeSignal(BaseModel):
    direction: Literal["buy", "sell"]
    entry_price: float
    stop_loss: float
    take_profit: float
    reason: str
    level_kind: str


class Trade(BaseModel):
    id: int
    direction: Literal["buy", "sell"]
    entry_time: int
    entry_price: float
    stop_loss: float
    take_profit: float
    volume: float
    exit_time: Optional[int] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[Literal["take_profit", "stop_loss", "end_of_data", "manual"]] = None
    pnl: Optional[float] = None
    reason: str = ""


class LiveStartRequest(BaseModel):
    symbol: str
    timeframe: str = "M15"
    volume: float = 0.10
    strategy: StrategyParams = StrategyParams()
    dry_run: bool = True


class ReportFilters(BaseModel):
    date_from: Optional[str] = None   # "YYYY-MM-DD", UTC entry date, inclusive
    date_to: Optional[str] = None
    time_from: Optional[str] = None   # "HH:MM", UTC entry time, inclusive
    time_to: Optional[str] = None
    sessions: Optional[List[str]] = None   # keys of config.TRADING_SESSIONS / "off_hours"
    weekdays: Optional[List[str]] = None   # "Mon" … "Sun"


class ReportRequest(BaseModel):
    run_id: Optional[str] = None
    trades: Optional[List[dict]] = None
    filters: ReportFilters = ReportFilters()
