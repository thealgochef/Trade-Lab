"""Deterministic tick-bar aggregation from canonical trades only."""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from trade_lab.domain.events import MarketEvent, TradeEvent
from trade_lab.domain.sessions import SessionClassifier


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


@dataclass(slots=True)
class _MutableCandle:
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

    @classmethod
    def from_trade(
        cls, timeframe_ticks: int, trade: TradeEvent, trading_day: date, bar_index: int
    ) -> "_MutableCandle":
        return cls(
            timeframe_ticks=timeframe_ticks,
            trading_day=trading_day,
            bar_index=bar_index,
            bar_id=make_bar_id(timeframe_ticks, trading_day, bar_index),
            open_ts_utc=trade.event_ts_utc,
            close_ts_utc=trade.event_ts_utc,
            open_ticks=trade.price_ticks,
            high_ticks=trade.price_ticks,
            low_ticks=trade.price_ticks,
            close_ticks=trade.price_ticks,
            volume=trade.size,
            trade_count=1,
        )

    def add_trade(self, trade: TradeEvent) -> None:
        self.close_ts_utc = trade.event_ts_utc
        self.close_ticks = trade.price_ticks
        self.high_ticks = max(self.high_ticks, trade.price_ticks)
        self.low_ticks = min(self.low_ticks, trade.price_ticks)
        self.volume += trade.size
        self.trade_count += 1

    def freeze(self, *, complete: bool, reason: CandleCloseReason | None) -> Candle:
        return Candle(
            timeframe_ticks=self.timeframe_ticks,
            trading_day=self.trading_day,
            bar_index=self.bar_index,
            bar_id=self.bar_id,
            open_ts_utc=self.open_ts_utc,
            close_ts_utc=self.close_ts_utc,
            open_ticks=self.open_ticks,
            high_ticks=self.high_ticks,
            low_ticks=self.low_ticks,
            close_ticks=self.close_ticks,
            volume=self.volume,
            trade_count=self.trade_count,
            is_complete=complete,
            is_partial=not complete,
            close_reason=reason,
        )


@dataclass(frozen=True, slots=True)
class CandleUpdate:
    completed: tuple[Candle, ...]
    current: tuple[Candle, ...]


class CandleEngine:
    """Build all configured tick bars concurrently.

    Only `TradeEvent` is accepted for bar counting because quotes/status/statistics do
    not represent prints. This keeps live and replay bar semantics identical.
    """

    def __init__(self, timeframes: tuple[int, ...] = (147, 987, 2000)) -> None:
        if not timeframes or any(size <= 0 for size in timeframes):
            raise ValueError("tick timeframes must be positive")
        self.timeframes = tuple(sorted(set(timeframes)))
        self._current: dict[int, _MutableCandle] = {}
        self._next_index: dict[tuple[int, date], int] = {}
        self._session_classifier = SessionClassifier()

    def process_event(self, event: MarketEvent) -> CandleUpdate:
        if not isinstance(event, TradeEvent):
            return self.snapshot_update(())
        return self.process_trade(event)

    def process_trade(self, trade: TradeEvent) -> CandleUpdate:
        trading_day, _session = self._session_classifier.classify(trade.event_ts_utc)
        if trading_day is None:
            return self.snapshot_update(())

        completed: list[Candle] = []
        current = self._current
        price_ticks = trade.price_ticks
        event_ts_utc = trade.event_ts_utc
        size = trade.size
        for timeframe in self.timeframes:
            candle = current.get(timeframe)
            if candle is not None and candle.trading_day != trading_day:
                completed.append(candle.freeze(complete=False, reason=CandleCloseReason.END_OF_DAY))
                candle = None
            if candle is None:
                bar_index = self._allocate_bar_index(timeframe, trading_day)
                new_candle = _MutableCandle(
                    timeframe_ticks=timeframe,
                    trading_day=trading_day,
                    bar_index=bar_index,
                    bar_id=make_bar_id(timeframe, trading_day, bar_index),
                    open_ts_utc=event_ts_utc,
                    close_ts_utc=event_ts_utc,
                    open_ticks=price_ticks,
                    high_ticks=price_ticks,
                    low_ticks=price_ticks,
                    close_ticks=price_ticks,
                    volume=size,
                    trade_count=1,
                )
                if timeframe == 1:
                    completed.append(
                        new_candle.freeze(complete=True, reason=CandleCloseReason.COMPLETE)
                    )
                else:
                    current[timeframe] = new_candle
                continue
            candle.close_ts_utc = event_ts_utc
            candle.close_ticks = price_ticks
            if price_ticks > candle.high_ticks:
                candle.high_ticks = price_ticks
            if price_ticks < candle.low_ticks:
                candle.low_ticks = price_ticks
            candle.volume += size
            candle.trade_count += 1
            if candle.trade_count == timeframe:
                completed.append(candle.freeze(complete=True, reason=CandleCloseReason.COMPLETE))
                del current[timeframe]
        return self.snapshot_update(tuple(completed))

    def snapshot_update(self, completed: tuple[Candle, ...]) -> CandleUpdate:
        current = tuple(c.freeze(complete=False, reason=None) for c in self._current.values())
        return CandleUpdate(completed=completed, current=current)

    def finalize_trading_day(self) -> tuple[Candle, ...]:
        """Explicitly close all incomplete bars at their last trade."""

        completed = tuple(
            c.freeze(complete=False, reason=CandleCloseReason.END_OF_DAY)
            for c in self._current.values()
        )
        self._current.clear()
        return completed

    def _allocate_bar_index(self, timeframe: int, trading_day: date) -> int:
        key = (timeframe, trading_day)
        bar_index = self._next_index.get(key, 0)
        self._next_index[key] = bar_index + 1
        return bar_index


def make_bar_id(timeframe_ticks: int, trading_day: date, bar_index: int) -> str:
    return f"{timeframe_ticks}t:{trading_day.isoformat()}:{bar_index}"
