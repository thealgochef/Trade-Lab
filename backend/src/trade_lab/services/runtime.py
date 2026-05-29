"""Runtime composition for live-compatible market events.

The application runtime owns the Phase 2A engines so replay and future live feeds
cannot accidentally diverge. Adapters may know where bytes came from, but only
canonical events are allowed into this hot path and DTO mapping remains at the API
edge.
"""

import re
from dataclasses import dataclass, field
from datetime import timedelta
from types import MappingProxyType
from typing import Any

from trade_lab.domain.candles import Candle, CandleEngine
from trade_lab.domain.data_quality import DataQualityWarning
from trade_lab.domain.events import (
    DailyStatisticEvent,
    InstrumentDefinitionEvent,
    MarketEvent,
    MarketStatusEvent,
    TopOfBookEvent,
    TradeEvent,
)
from trade_lab.domain.feed import FeedConnectionState, FeedStatus
from trade_lab.domain.levels import DisplayLevel, SessionLevelEngine, TouchEvent
from trade_lab.domain.observations import Observation, ObservationEngine


@dataclass(frozen=True, slots=True)
class RuntimeUpdate:
    feed_status: FeedStatus | None = None
    warnings: tuple[DataQualityWarning, ...] = ()
    current_bars: tuple[Candle, ...] = ()
    closed_bars: tuple[Candle, ...] = ()
    display_levels: tuple[DisplayLevel, ...] = ()
    touches: tuple[TouchEvent, ...] = ()
    observations: tuple[Observation, ...] = ()

    def has_deltas(self) -> bool:
        return any(
            (
                self.feed_status is not None,
                self.warnings,
                self.current_bars,
                self.closed_bars,
                self.display_levels,
                self.touches,
                self.observations,
            )
        )


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    current_bars: tuple[Candle, ...]
    recent_closed_bars: tuple[Candle, ...]
    display_levels: tuple[DisplayLevel, ...]
    active_observations: tuple[Observation, ...]
    feed_status: FeedStatus
    warnings: tuple[DataQualityWarning, ...]
    metadata: MappingProxyType[str, Any] = field(default_factory=lambda: MappingProxyType({}))


class ApplicationRuntime:
    """Compose domain engines behind one live/replay-compatible entry point."""

    def __init__(
        self,
        *,
        requested_symbol: str | None,
        tick_timeframes: tuple[int, ...],
        observation_duration_seconds: int,
        warning_limit: int = 100,
        recent_closed_bar_limit: int = 500,
    ) -> None:
        if warning_limit <= 0:
            raise ValueError("warning_limit must be positive")
        if recent_closed_bar_limit <= 0:
            raise ValueError("recent_closed_bar_limit must be positive")
        self.requested_symbol = requested_symbol
        self.candles = CandleEngine(tick_timeframes)
        self.levels = SessionLevelEngine()
        self.observations = ObservationEngine(timedelta(seconds=observation_duration_seconds))
        self._warning_limit = warning_limit
        self._recent_closed_bar_limit = recent_closed_bar_limit
        self._tick_timeframes = tick_timeframes
        self._observation_duration_seconds = observation_duration_seconds
        self._warnings: list[DataQualityWarning] = []
        self._recent_closed_bars: list[Candle] = []
        self._metadata: dict[str, Any] = {}
        self._feed_status = FeedStatus(
            state=FeedConnectionState.DISCONNECTED,
            mode="idle",
            requested_symbol=requested_symbol,
            last_message="Market-data feed is not started.",
        )

    def reset(
        self,
        *,
        requested_symbol: str | None = None,
        preserve_warnings: bool = False,
        feed_message: str = "Runtime reset for replay.",
    ) -> RuntimeUpdate:
        """Reset all derived runtime state before a new replay session.

        Warnings are cleared by default so each replay is deterministic and does not
        carry data-quality state from a prior source. Callers may explicitly preserve
        warnings for a future operator-audit workflow.
        """

        if requested_symbol is not None:
            self.requested_symbol = requested_symbol
        self.candles = CandleEngine(self._tick_timeframes)
        self.levels = SessionLevelEngine()
        self.observations = ObservationEngine(
            timedelta(seconds=self._observation_duration_seconds)
        )
        if not preserve_warnings:
            self._warnings.clear()
        self._recent_closed_bars.clear()
        self._metadata.clear()
        self._feed_status = FeedStatus(
            state=FeedConnectionState.DISCONNECTED,
            mode="idle",
            requested_symbol=self.requested_symbol,
            last_message=feed_message,
        )
        return RuntimeUpdate(feed_status=self._feed_status)

    @property
    def feed_status(self) -> FeedStatus:
        return self._feed_status

    def set_feed_status(self, status: FeedStatus) -> RuntimeUpdate:
        self._feed_status = status
        return RuntimeUpdate(feed_status=status)

    def record_warning(self, warning: DataQualityWarning) -> RuntimeUpdate:
        warning = _safe_warning(warning)
        self._warnings.append(warning)
        if len(self._warnings) > self._warning_limit:
            del self._warnings[: len(self._warnings) - self._warning_limit]
        if self._feed_status.state != FeedConnectionState.DEGRADED:
            self._feed_status = FeedStatus(
                state=FeedConnectionState.DEGRADED,
                mode=self._feed_status.mode,
                requested_symbol=self._feed_status.requested_symbol,
                raw_symbol=self._feed_status.raw_symbol,
                dataset=self._feed_status.dataset,
                schema=self._feed_status.schema,
                last_event_ts_utc=warning.event_ts_utc or self._feed_status.last_event_ts_utc,
                last_message=warning.message,
                metadata=dict(self._feed_status.metadata),
            )
        return RuntimeUpdate(feed_status=self._feed_status, warnings=(warning,))

    def process_market_event(self, event: MarketEvent) -> RuntimeUpdate:
        """Process one canonical event; only trades advance bars/touches.

        Top-of-book, definitions, status, and daily statistics update contextual
        state only. This prevents quote traffic or historical-only records from
        incrementing candles or creating touches in replay.
        """

        if isinstance(event, TradeEvent):
            return self._process_trade(event)
        if isinstance(event, TopOfBookEvent):
            return self._update_feed_context(event.event_ts_utc, schema=event.source_schema)
        if isinstance(event, InstrumentDefinitionEvent):
            self._metadata["instrument"] = {
                "instrument_id": event.instrument_id,
                "requested_symbol": event.requested_symbol,
                "raw_symbol": event.raw_symbol,
                "tick_size": str(event.tick_size),
            }
            self._feed_status = FeedStatus(
                state=self._feed_status.state,
                mode=self._feed_status.mode,
                requested_symbol=event.requested_symbol,
                raw_symbol=event.raw_symbol,
                dataset=self._feed_status.dataset,
                schema=self._feed_status.schema,
                last_event_ts_utc=event.event_ts_utc,
                last_message="instrument definition received",
                metadata=dict(self._feed_status.metadata),
            )
            return RuntimeUpdate()
        if isinstance(event, MarketStatusEvent):
            self._metadata["market_status"] = event.status.value
            self._feed_status = FeedStatus(
                state=self._feed_status.state,
                mode=self._feed_status.mode,
                requested_symbol=self._feed_status.requested_symbol,
                raw_symbol=self._feed_status.raw_symbol,
                dataset=self._feed_status.dataset,
                schema=self._feed_status.schema,
                last_event_ts_utc=event.event_ts_utc,
                last_message=event.reason or f"market status: {event.status.value}",
                metadata={**dict(self._feed_status.metadata), "market_status": event.status.value},
            )
            return RuntimeUpdate()
        if isinstance(event, DailyStatisticEvent):
            self._metadata.setdefault("daily_statistics", {})[event.statistic_type] = {
                "price_ticks": event.price_ticks,
                "value": event.value,
            }
            return self._update_feed_context(event.event_ts_utc, schema=event.source_schema)
        raise TypeError(f"unsupported market event type: {type(event).__name__}")

    def snapshot(self) -> RuntimeSnapshot:
        candle_update = self.candles.snapshot_update(())
        return RuntimeSnapshot(
            current_bars=candle_update.current,
            recent_closed_bars=tuple(self._recent_closed_bars),
            display_levels=self.levels.display_levels(),
            active_observations=self.observations.active(),
            feed_status=self._feed_status,
            warnings=tuple(self._warnings),
            metadata=MappingProxyType(dict(self._metadata)),
        )

    def _process_trade(self, trade: TradeEvent) -> RuntimeUpdate:
        candle_update = self.candles.process_trade(trade)
        if candle_update.completed:
            self._recent_closed_bars.extend(candle_update.completed)
            if len(self._recent_closed_bars) > self._recent_closed_bar_limit:
                del self._recent_closed_bars[
                    : len(self._recent_closed_bars) - self._recent_closed_bar_limit
                ]
        level_update = self.levels.process_trade(trade)
        changed_observations = list(self.observations.refresh(trade.event_ts_utc))
        for touch in level_update.touches:
            changed_observations.append(self.observations.start_from_touch(touch))
        self._feed_status = FeedStatus(
            state=FeedConnectionState.CONNECTED
            if self._feed_status.mode == "live"
            else FeedConnectionState.REPLAYING,
            mode=self._feed_status.mode if self._feed_status.mode != "idle" else "runtime",
            requested_symbol=trade.requested_symbol,
            raw_symbol=trade.raw_symbol,
            dataset=self._feed_status.dataset,
            schema=trade.source_schema or self._feed_status.schema,
            last_event_ts_utc=trade.event_ts_utc,
            last_message="trade processed",
            metadata=dict(self._feed_status.metadata),
        )
        return RuntimeUpdate(
            feed_status=self._feed_status,
            current_bars=candle_update.current,
            closed_bars=candle_update.completed,
            display_levels=level_update.display_levels,
            touches=level_update.touches,
            observations=tuple(changed_observations),
        )

    def _update_feed_context(self, event_ts_utc, *, schema: str | None) -> RuntimeUpdate:
        was_disconnected = self._feed_status.state == FeedConnectionState.DISCONNECTED
        self._feed_status = FeedStatus(
            state=self._feed_status.state
            if self._feed_status.state != FeedConnectionState.DISCONNECTED
            else FeedConnectionState.CONNECTED,
            mode=self._feed_status.mode if self._feed_status.mode != "idle" else "runtime",
            requested_symbol=self._feed_status.requested_symbol,
            raw_symbol=self._feed_status.raw_symbol,
            dataset=self._feed_status.dataset,
            schema=schema or self._feed_status.schema,
            last_event_ts_utc=event_ts_utc,
            last_message="market context updated",
            metadata=dict(self._feed_status.metadata),
        )
        return RuntimeUpdate(feed_status=self._feed_status) if was_disconnected else RuntimeUpdate()


_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s,;]+")
_POSIX_PATH_RE = re.compile(r"/(?:[^\s,;]+/)+[^\s,;]+")
_SECRET_RE = re.compile(r"(?i)(secret|token|password|api[_-]?key)\s*[:=]\s*[^\s,;]+")
_SECRET_WORD_RE = re.compile(r"(?i)secret|token|password|api[_-]?key")


def _safe_text(value: str) -> str:
    value = _WINDOWS_PATH_RE.sub("<path>", value)
    value = _POSIX_PATH_RE.sub("<path>", value)
    value = _SECRET_RE.sub("<redacted>", value)
    return _SECRET_WORD_RE.sub("<redacted>", value)


def _safe_metadata(value: Any) -> Any:
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, dict):
        return {_safe_key(key): _safe_metadata(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_metadata(item) for item in value]
    return value


def _safe_key(key: object) -> str:
    text = _safe_text(str(key))
    return "<redacted_key>" if _SECRET_WORD_RE.search(text) else text


def _safe_source(source: str | None) -> str | None:
    if source is None:
        return None
    sanitized = _safe_text(source)
    if sanitized == source and ("/" in source or "\\" in source):
        return source.replace("\\", "/").rsplit("/", 1)[-1]
    return sanitized


def _safe_warning(warning: DataQualityWarning) -> DataQualityWarning:
    return DataQualityWarning(
        code=warning.code,
        message=_safe_text(warning.message),
        severity=warning.severity,
        source=_safe_source(warning.source),
        event_ts_utc=warning.event_ts_utc,
        metadata=_safe_metadata(dict(warning.metadata)),
    )
