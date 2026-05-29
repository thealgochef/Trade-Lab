"""Canonical market-data events shared by live and replay.

These immutable, slotted dataclasses are deliberately small. Adapters normalize
feed-specific records into these types before domain processing so live and replay
cannot drift semantically.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from trade_lab.domain.prices import ticks_to_price


class TradeSide(StrEnum):
    BUY = "buy"
    SELL = "sell"
    UNKNOWN = "unknown"


class MarketStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    HALTED = "halted"
    PAUSED = "paused"
    UNKNOWN = "unknown"


def ensure_utc(ts: datetime) -> datetime:
    """Require/normalize UTC timestamps for replay-safe comparisons."""

    if ts.tzinfo is None:
        raise ValueError("event timestamps must be timezone-aware UTC datetimes")
    return ts.astimezone(UTC)


def freeze_metadata(metadata: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(metadata or {}))


def validate_tick_field(field_name: str, value: int | None) -> None:
    """Reject non-integer tick fields while excluding bool's int subclass."""

    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer tick value")


@dataclass(frozen=True, slots=True)
class TradeEvent:
    """One canonical trade message; the only event that increments tick bars."""

    event_ts_utc: datetime
    receive_ts_utc: datetime | None
    instrument_id: int | None
    requested_symbol: str
    raw_symbol: str | None
    price_ticks: int
    size: int
    side: TradeSide = TradeSide.UNKNOWN
    source_schema: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_ts_utc", ensure_utc(self.event_ts_utc))
        if self.receive_ts_utc is not None:
            object.__setattr__(self, "receive_ts_utc", ensure_utc(self.receive_ts_utc))
        object.__setattr__(self, "metadata", freeze_metadata(self.metadata))
        validate_tick_field("price_ticks", self.price_ticks)
        if self.size <= 0:
            raise ValueError("trade size must be positive")

    @property
    def price(self) -> Decimal:
        return ticks_to_price(self.price_ticks)


@dataclass(frozen=True, slots=True)
class TopOfBookEvent:
    event_ts_utc: datetime
    instrument_id: int | None
    bid_price_ticks: int | None
    bid_size: int | None
    ask_price_ticks: int | None
    ask_size: int | None
    source_schema: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_ts_utc", ensure_utc(self.event_ts_utc))
        object.__setattr__(self, "metadata", freeze_metadata(self.metadata))
        validate_tick_field("bid_price_ticks", self.bid_price_ticks)
        validate_tick_field("ask_price_ticks", self.ask_price_ticks)


@dataclass(frozen=True, slots=True)
class InstrumentDefinitionEvent:
    event_ts_utc: datetime
    instrument_id: int
    requested_symbol: str
    raw_symbol: str
    tick_size: Decimal
    point_value: Decimal | None = None
    expiration: datetime | None = None
    roll_metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_ts_utc", ensure_utc(self.event_ts_utc))
        if self.expiration is not None:
            object.__setattr__(self, "expiration", ensure_utc(self.expiration))
        object.__setattr__(self, "roll_metadata", freeze_metadata(self.roll_metadata))


@dataclass(frozen=True, slots=True)
class MarketStatusEvent:
    event_ts_utc: datetime
    instrument_id: int | None
    status: MarketStatus
    reason: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_ts_utc", ensure_utc(self.event_ts_utc))
        object.__setattr__(self, "metadata", freeze_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class DailyStatisticEvent:
    event_ts_utc: datetime
    instrument_id: int | None
    statistic_type: str
    price_ticks: int | None = None
    value: Decimal | int | None = None
    source_schema: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_ts_utc", ensure_utc(self.event_ts_utc))
        object.__setattr__(self, "metadata", freeze_metadata(self.metadata))
        validate_tick_field("price_ticks", self.price_ticks)


MarketEvent = (
    TradeEvent
    | TopOfBookEvent
    | InstrumentDefinitionEvent
    | MarketStatusEvent
    | DailyStatisticEvent
)
