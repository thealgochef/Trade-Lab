"""Thin Trade-Lab adapter over Strategy-Core runtime.

Trade-Lab owns API/WebSocket DTOs, source allowlists, and operator controls. This
service converts Trade-Lab canonical events into Strategy-Core neutral events and
maps Strategy-Core neutral runtime updates back to existing Trade-Lab domain DTO
compatibility types. It must not recompute session/level/touch strategy meaning.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from uuid import uuid4

import strategy_core
from strategy_core.runtime.state import FeedStatus as CoreFeedStatus
from strategy_core.runtime.state import RuntimeSnapshot as CoreSnapshot
from strategy_core.runtime.state import RuntimeUpdate as CoreUpdate
from strategy_core.runtime.state import StrategyRuntime
from strategy_core.runtime.wiring import touch_reversal_kwargs
from strategy_core.types import Bar as CoreBar
from strategy_core.types import CloseReason as CoreCloseReason
from strategy_core.types import Direction as CoreDirection
from strategy_core.types import Level as CoreLevel
from strategy_core.types import Quote as CoreQuote
from strategy_core.types import Touch as CoreTouch
from strategy_core.types import Trade as CoreTrade

from trade_lab.domain.candles import Candle, CandleCloseReason
from trade_lab.domain.data_quality import DataQualityWarning
from trade_lab.domain.events import MarketEvent, TopOfBookEvent, TradeEvent, TradeSide
from trade_lab.domain.feed import FeedConnectionState, FeedStatus
from trade_lab.domain.levels import DisplayLevel, LevelDirection, LevelKind, TouchEvent
from trade_lab.domain.prices import NQ_TICK_SIZE
from trade_lab.domain.sessions import SessionName

__all__ = [
    "StrategyCoreService",
    "StrategyCoreSnapshot",
    "StrategyCoreUpdate",
]

# audit #NN-1: Strategy-Core's Touch.direction is authoritative — it is resolved on the
# MERGED ZONE side (low -> LONG, high -> SHORT), NOT on level_kind (= zone.names[0], the
# lowest-priced constituent), so for a mixed-side merged zone re-deriving from level_kind
# inverts it. Map the Core Direction straight onto the Trade-Lab LevelDirection and carry
# it on the TouchEvent.
_CORE_DIRECTION_TO_TRADE_LAB: dict[CoreDirection, LevelDirection] = {
    CoreDirection.LONG: LevelDirection.LONG,
    CoreDirection.SHORT: LevelDirection.SHORT,
}

# audit #7: encode the trade aggressor side using the canonical databento codes
# (strategy_core.constants: 'B' = buy, 'A' = sell, 'N' = none/unknown). The previous
# event.side.value.upper()[0] slicing produced 'S' for sell and 'U' for unknown, which
# are not databento codes and would mis-signal any side-aware order-flow feature.
_TRADE_SIDE_TO_CORE: dict[TradeSide, str] = {
    TradeSide.BUY: "B",
    TradeSide.SELL: "A",
    TradeSide.UNKNOWN: "N",
}


@dataclass(frozen=True, slots=True)
class StrategyCoreUpdate:
    feed_status: FeedStatus | None = None
    warnings: tuple[DataQualityWarning, ...] = ()
    current_bars: tuple[Candle, ...] = ()
    closed_bars: tuple[Candle, ...] = ()
    display_levels: tuple[DisplayLevel, ...] = ()
    touches: tuple[TouchEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class StrategyCoreSnapshot:
    current_bars: tuple[Candle, ...]
    recent_closed_bars: tuple[Candle, ...]
    display_levels: tuple[DisplayLevel, ...]
    feed_status: FeedStatus
    warnings: tuple[DataQualityWarning, ...]
    session: str | None
    trading_day: date | None
    metadata: MappingProxyType[str, object]


class StrategyCoreService:
    """Compatibility adapter around :class:`strategy_core.runtime.StrategyRuntime`."""

    def __init__(
        self,
        *,
        requested_symbol: str | None,
        tick_timeframes: tuple[int, ...],
        recent_closed_bar_limit: int = 500,
        warning_limit: int = 100,
    ) -> None:
        self.requested_symbol = requested_symbol
        self._display_timeframes = tuple(sorted(set(tick_timeframes)))
        self._runtime = StrategyRuntime(
            requested_symbol=requested_symbol,
            timeframes=self._display_timeframes,
            # audit #5: PIN the decision bar to the smallest configured timeframe
            # explicitly instead of relying on StrategyRuntime's implicit min() fallback.
            # With the production Trade-Lab defaults this is the 147t contract bar,
            # preserving Strategy-Core bar-range touch semantics. Pinning it means a
            # future SMALLER display timeframe cannot silently shrink the decision bar
            # and degenerate the bar-range touch back to a one-print exact-touch path.
            decision_timeframe=min(self._display_timeframes),
            recent_closed_bar_limit=recent_closed_bar_limit,
            warning_limit=warning_limit,
            # B3: the touch strategy runs through the Strategy-Core plugin (the sole path;
            # the flag and the hardwired None path were removed). touch_reversal_kwargs()
            # unconditionally attaches the registered touch_reversal plugin + its section.
            **touch_reversal_kwargs(),
        )
        self._last_trade: TradeEvent | None = None
        self._last_schema: str | None = None
        self._touch_sequence: dict[tuple[date, str], int] = {}

    @property
    def platform_version(self) -> str:
        return strategy_core.PLATFORM_VERSION

    @property
    def plugin_strategy_id(self) -> str:
        """The wired plugin's registry id (E2: activation guards contracts against it)."""

        return self._runtime._plugin.strategy_id

    @property
    def plugin_strategy_version(self) -> str:
        """The wired plugin's declared strategy_version (the E2 strategy axis)."""

        return self._runtime._plugin.strategy_version

    def reset(self, *, requested_symbol: str | None = None) -> StrategyCoreUpdate:
        if requested_symbol is not None:
            self.requested_symbol = requested_symbol
        self._last_trade = None
        self._touch_sequence.clear()
        return self._map_update(self._runtime.reset(requested_symbol=requested_symbol))

    def load_prior_day_summary(self, trading_day: date, high_ticks: int, low_ticks: int) -> None:
        self._runtime.load_prior_day_summary(
            trading_day,
            high_ticks=high_ticks,
            low_ticks=low_ticks,
        )

    def trade_price_at(self, ts_utc: datetime) -> float | None:
        """D1a: the honest decision-time fill query, backed by the Strategy-Core
        runtime's bounded trade ring (most recent price>0 print at/before ``ts_utc``
        within the 30-minute lookback)."""
        return self._runtime.trade_price_at(ts_utc)

    def display_levels(self) -> tuple[DisplayLevel, ...]:
        return self.snapshot().display_levels

    def process_market_event(self, event: MarketEvent) -> StrategyCoreUpdate:
        if isinstance(event, TradeEvent):
            self._last_trade = event
            self._last_schema = event.source_schema
            return self._map_update(self._runtime.process_event(_trade_to_core(event)))
        if isinstance(event, TopOfBookEvent):
            self._last_schema = event.source_schema
            quote = _quote_to_core(event)
            if quote is None:
                return StrategyCoreUpdate()
            return self._map_update(self._runtime.process_event(quote))
        return StrategyCoreUpdate()

    def snapshot(self) -> StrategyCoreSnapshot:
        return self._map_snapshot(self._runtime.snapshot())

    def _map_update(self, update: CoreUpdate) -> StrategyCoreUpdate:
        snapshot = self._runtime.snapshot()
        return StrategyCoreUpdate(
            feed_status=None
            if update.feed_status is None
            else _feed_status_to_trade_lab(update.feed_status, schema_fallback=self._last_schema),
            current_bars=tuple(
                _bar_to_trade_lab(bar)
                for bar in update.current_bars
                if bar.timeframe_ticks in self._display_timeframes
            ),
            closed_bars=tuple(
                _bar_to_trade_lab(bar)
                for bar in update.closed_bars
                if bar.timeframe_ticks in self._display_timeframes
            ),
            display_levels=tuple(
                _level_to_trade_lab(
                    level,
                    snapshot.session,
                    snapshot.trading_day,
                    update.feed_status,
                )
                for level in update.levels
                if _level_kind(level.name) is not None and snapshot.trading_day is not None
            ),
            touches=tuple(
                self._touch_to_trade_lab(touch, snapshot.session)
                for touch in update.touches
            ),
        )

    def _map_snapshot(self, snapshot: CoreSnapshot) -> StrategyCoreSnapshot:
        return StrategyCoreSnapshot(
            current_bars=tuple(
                _bar_to_trade_lab(bar)
                for bar in snapshot.current_bars
                if bar.timeframe_ticks in self._display_timeframes
            ),
            recent_closed_bars=tuple(
                _bar_to_trade_lab(bar)
                for bar in snapshot.recent_closed_bars
                if bar.timeframe_ticks in self._display_timeframes
            ),
            display_levels=tuple(
                _level_to_trade_lab(
                    level,
                    snapshot.session,
                    snapshot.trading_day,
                    snapshot.feed_status,
                )
                for level in snapshot.levels
                if _level_kind(level.name) is not None and snapshot.trading_day is not None
            ),
            feed_status=_feed_status_to_trade_lab(
                snapshot.feed_status,
                schema_fallback=self._last_schema,
            ),
            warnings=(),
            session=snapshot.session,
            trading_day=snapshot.trading_day,
            metadata=MappingProxyType(
                {"strategy_core_platform_version": strategy_core.PLATFORM_VERSION}
            ),
        )

    def _touch_to_trade_lab(self, touch: CoreTouch, session: str | None) -> TouchEvent:
        key = (touch.trading_day, session or "none")
        sequence = self._touch_sequence.get(key, 0) + 1
        self._touch_sequence[key] = sequence
        last_trade = self._last_trade
        level_price_ticks = round(touch.representative_price / float(NQ_TICK_SIZE))
        trade_price_ticks = (
            last_trade.price_ticks if last_trade is not None else level_price_ticks
        )
        requested_symbol = self.requested_symbol or (
            last_trade.requested_symbol if last_trade is not None else ""
        )
        return TouchEvent(
            touch_id=str(uuid4()),
            event_ts_utc=touch.bar_ts_utc,
            trading_day=touch.trading_day,
            session=_session_to_trade_lab(session),
            level_kind=_level_kind(touch.level_type) or LevelKind.PDH,
            level_price_ticks=level_price_ticks,
            trade_price_ticks=trade_price_ticks,
            requested_symbol=requested_symbol,
            raw_symbol=None if last_trade is None else last_trade.raw_symbol,
            instrument_id=None if last_trade is None else last_trade.instrument_id,
            created_observation=True,
            sequence_in_session=sequence,
            # audit #NN-1: carry the authoritative Strategy-Core direction instead of
            # leaving downstream to re-derive it from level_kind (which inverts for
            # mixed-side merged zones).
            direction=_CORE_DIRECTION_TO_TRADE_LAB[touch.direction],
            # W1 P3c: carry the EXACT zone representative price (points, no tick
            # snap) — the research feature reference; ticks stay for display/wire.
            level_price=touch.representative_price,
        )


def _trade_to_core(event: TradeEvent) -> CoreTrade:
    # audit #7: map to canonical databento aggressor codes (B/A/N), not first-letter
    # slicing of the TradeSide value (which gave 'S'/'U' for sell/unknown).
    side = None if event.side is None else _TRADE_SIDE_TO_CORE[event.side]
    return CoreTrade(
        event_ts_utc=event.event_ts_utc,
        price_ticks=event.price_ticks,
        size=event.size,
        side=side,
    )


def _quote_to_core(event: TopOfBookEvent) -> CoreQuote | None:
    if event.bid_price_ticks is None or event.ask_price_ticks is None:
        return None
    return CoreQuote(
        event_ts_utc=event.event_ts_utc,
        bid_price_ticks=event.bid_price_ticks,
        ask_price_ticks=event.ask_price_ticks,
        bid_size=event.bid_size or 0,
        ask_size=event.ask_size or 0,
    )


def _bar_to_trade_lab(bar: CoreBar) -> Candle:
    return Candle(
        timeframe_ticks=bar.timeframe_ticks,
        trading_day=bar.trading_day,
        bar_index=bar.bar_index,
        bar_id=bar.bar_id,
        open_ts_utc=bar.open_ts_utc,
        close_ts_utc=bar.close_ts_utc,
        open_ticks=bar.open_ticks,
        high_ticks=bar.high_ticks,
        low_ticks=bar.low_ticks,
        close_ticks=bar.close_ticks,
        volume=bar.volume,
        trade_count=bar.trade_count,
        is_complete=bar.is_complete,
        is_partial=bar.is_partial,
        close_reason=_close_reason_to_trade_lab(bar.close_reason),
    )


def _close_reason_to_trade_lab(reason: CoreCloseReason | None) -> CandleCloseReason | None:
    if reason is None:
        return None
    return CandleCloseReason(reason.value)


def _level_to_trade_lab(
    level: CoreLevel,
    session: str | None,
    trading_day: date | None,
    status: CoreFeedStatus | None,
) -> DisplayLevel:
    kind = _level_kind(level.name)
    if kind is None or trading_day is None:
        raise ValueError("unsupported Strategy-Core level for Trade-Lab DTO mapping")
    last_ts = None if status is None else status.last_event_ts_utc
    is_eligible = level.available_from is None or (
        last_ts is not None and last_ts >= level.available_from
    )
    origin = _level_origin(kind)
    return DisplayLevel(
        kind=kind,
        price_ticks=round(level.price / float(NQ_TICK_SIZE)),
        trading_day=trading_day,
        origin_session=origin,
        is_developing=origin is not None and origin.value == session and not is_eligible,
        is_eligible=is_eligible,
    )


def _level_kind(name: str) -> LevelKind | None:
    try:
        return LevelKind(name)
    except ValueError:
        return None


def _level_origin(kind: LevelKind) -> SessionName | None:
    if kind in (LevelKind.ASIA_HIGH, LevelKind.ASIA_LOW):
        return SessionName.ASIA
    if kind in (LevelKind.LONDON_HIGH, LevelKind.LONDON_LOW):
        return SessionName.LONDON
    if kind in (LevelKind.NY_HIGH, LevelKind.NY_LOW):
        return SessionName.NY
    return None


def _session_to_trade_lab(session: str | None) -> SessionName:
    if session == "asia":
        return SessionName.ASIA
    if session == "london":
        return SessionName.LONDON
    if session == "ny":
        return SessionName.NY
    return SessionName.CLOSED


def _feed_status_to_trade_lab(
    status: CoreFeedStatus, *, schema_fallback: str | None = None
) -> FeedStatus:
    state = {
        "disconnected": FeedConnectionState.DISCONNECTED,
        "connected": FeedConnectionState.CONNECTED,
        "replaying": FeedConnectionState.REPLAYING,
        "degraded": FeedConnectionState.DEGRADED,
        "failed": FeedConnectionState.DEGRADED,
    }.get(status.state, FeedConnectionState.REPLAYING)
    return FeedStatus(
        state=state,
        mode=status.mode,
        requested_symbol=status.requested_symbol,
        schema=status.schema or schema_fallback,
        last_event_ts_utc=status.last_event_ts_utc,
        last_message=status.last_message,
        metadata={
            **dict(status.metadata),
            "strategy_core_platform_version": strategy_core.PLATFORM_VERSION,
        },
    )
