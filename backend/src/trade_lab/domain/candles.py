"""Tick-bar DTO/display types. Bar AGGREGATION lives in Strategy-Core (D2).

The local candle-engine shadow implementation was deleted in D2: authoritative
live/replay bars are produced by Strategy-Core's streaming engine and mapped into
these compatibility types at the adapter seam (``strategy_core_service``). The
only TL-local bar CONSTRUCTION left in production is the seed warm-up builder
(``services/seed.py`` — display-only, documented carve-out in the acceptance guard).
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class CandleCloseReason(StrEnum):
    COMPLETE = "complete"
    END_OF_DAY = "end_of_day"


@dataclass(frozen=True, slots=True)
class Candle:
    timeframe_ticks: int
    trading_day: date
    bar_index: int
    bar_id: str
    open_ts_utc: datetime
    close_ts_utc: datetime
    open_ticks: int
    high_ticks: int
    low_ticks: int
    close_ticks: int
    volume: int
    trade_count: int
    is_complete: bool
    is_partial: bool
    close_reason: CandleCloseReason | None = None


def make_bar_id(timeframe_ticks: int, trading_day: date, bar_index: int) -> str:
    return f"{timeframe_ticks}t:{trading_day.isoformat()}:{bar_index}"
