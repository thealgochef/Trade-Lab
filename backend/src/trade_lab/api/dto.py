"""API-facing DTOs and serializers.

Domain engines emit dataclasses. These Pydantic DTOs live only at the external API
boundary so frontend contracts can evolve without leaking UI assumptions into the
deterministic hot path.
"""

from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from trade_lab.domain.candles import Candle
from trade_lab.domain.data_quality import DataQualityWarning
from trade_lab.domain.feed import FeedConnectionState, FeedStatus
from trade_lab.domain.levels import DisplayLevel, TouchEvent
from trade_lab.domain.observations import Observation
from trade_lab.services.replay import ReplayStatus
from trade_lab.services.runtime import RuntimeSnapshot

MESSAGE_VERSION = "ws.v1"
MessageType = Literal[
    "system.heartbeat",
    "system.snapshot",
    "market.bar.updated",
    "market.bar.closed",
    "levels.updated",
    "touch.detected",
    "observation.updated",
    "data_quality.warning",
    "feed.status",
]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class BarDTO(ApiModel):
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
    close_reason: str | None


class DisplayLevelDTO(ApiModel):
    kind: str
    price_ticks: int
    trading_day: date
    origin_session: str | None
    is_developing: bool
    is_eligible: bool


class TouchDTO(ApiModel):
    touch_id: str
    event_ts_utc: datetime
    trading_day: date
    session: str
    level_kind: str
    level_price_ticks: int
    trade_price_ticks: int
    requested_symbol: str
    raw_symbol: str | None
    instrument_id: int | None
    created_observation: bool
    sequence_in_session: int


class ObservationDTO(ApiModel):
    observation_id: str
    originating_touch_id: str
    start_ts_utc: datetime
    scheduled_end_ts_utc: datetime
    status: str
    trading_day: date
    session: str
    level_kind: str
    level_price_ticks: int


class FeedStatusDTO(ApiModel):
    state: str
    mode: str
    requested_symbol: str | None
    raw_symbol: str | None = None
    dataset: str | None = None
    schema_: str | None = Field(default=None, alias="schema")
    last_event_ts_utc: datetime | None = None
    last_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DataQualityWarningDTO(ApiModel):
    code: str
    message: str
    severity: str
    source: str | None = None
    event_ts_utc: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SnapshotPayload(ApiModel):
    current_bars: list[BarDTO] = Field(default_factory=list)
    recent_closed_bars: list[BarDTO] = Field(default_factory=list)
    display_levels: list[DisplayLevelDTO] = Field(default_factory=list)
    active_observations: list[ObservationDTO] = Field(default_factory=list)
    feed_status: FeedStatusDTO
    warnings: list[DataQualityWarningDTO] = Field(default_factory=list)


class ReplayStatusDTO(ApiModel):
    state: str
    events_processed: int
    warnings_recorded: int
    last_event_ts_utc: datetime | None = None
    last_error: str | None = None
    requested_symbol: str | None = None
    schema_: str | None = Field(default=None, alias="schema")


class ReplaySourceDTO(ApiModel):
    source_id: str
    label: str
    requested_symbol: str
    schema_: str = Field(alias="schema")
    kind: str = "synthetic"
    session_label: str | None = None
    availability: str | None = None


class Envelope(ApiModel):
    version: str = MESSAGE_VERSION
    type: MessageType
    sequence: int
    server_time_utc: datetime
    payload: dict[str, Any]


def bar_to_dto(bar: Candle) -> BarDTO:
    return BarDTO(
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
        close_reason=None if bar.close_reason is None else bar.close_reason.value,
    )


def level_to_dto(level: DisplayLevel) -> DisplayLevelDTO:
    return DisplayLevelDTO(
        kind=level.kind.value,
        price_ticks=level.price_ticks,
        trading_day=level.trading_day,
        origin_session=None if level.origin_session is None else level.origin_session.value,
        is_developing=level.is_developing,
        is_eligible=level.is_eligible,
    )


def touch_to_dto(touch: TouchEvent) -> TouchDTO:
    return TouchDTO(
        touch_id=touch.touch_id,
        event_ts_utc=touch.event_ts_utc,
        trading_day=touch.trading_day,
        session=touch.session.value,
        level_kind=touch.level_kind.value,
        level_price_ticks=touch.level_price_ticks,
        trade_price_ticks=touch.trade_price_ticks,
        requested_symbol=touch.requested_symbol,
        raw_symbol=touch.raw_symbol,
        instrument_id=touch.instrument_id,
        created_observation=touch.created_observation,
        sequence_in_session=touch.sequence_in_session,
    )


def observation_to_dto(observation: Observation) -> ObservationDTO:
    return ObservationDTO(
        observation_id=observation.observation_id,
        originating_touch_id=observation.originating_touch_id,
        start_ts_utc=observation.start_ts_utc,
        scheduled_end_ts_utc=observation.scheduled_end_ts_utc,
        status=observation.status.value,
        trading_day=observation.trading_day,
        session=observation.session.value,
        level_kind=observation.level_kind.value,
        level_price_ticks=observation.level_price_ticks,
    )


def feed_status_to_dto(status: FeedStatus) -> FeedStatusDTO:
    return FeedStatusDTO(
        state=status.state.value,
        mode=status.mode,
        requested_symbol=status.requested_symbol,
        raw_symbol=status.raw_symbol,
        dataset=status.dataset,
        schema_=status.schema,
        last_event_ts_utc=status.last_event_ts_utc,
        last_message=status.last_message,
        metadata=dict(status.metadata),
    )


def warning_to_dto(warning: DataQualityWarning) -> DataQualityWarningDTO:
    return DataQualityWarningDTO(
        code=warning.code.value,
        message=warning.message,
        severity=warning.severity.value,
        source=warning.source,
        event_ts_utc=warning.event_ts_utc,
        metadata=dict(warning.metadata),
    )


def replay_status_to_dto(status: ReplayStatus) -> ReplayStatusDTO:
    return ReplayStatusDTO(
        state=status.state.value,
        events_processed=status.events_processed,
        warnings_recorded=status.warnings_recorded,
        last_event_ts_utc=status.last_event_ts_utc,
        last_error=status.last_error,
        requested_symbol=status.requested_symbol,
        schema_=status.schema,
    )


def bars_payload(bars: tuple[Candle, ...]) -> dict[str, Any]:
    return {"bars": [bar_to_dto(bar).model_dump(mode="json") for bar in bars]}


def levels_payload(levels: tuple[DisplayLevel, ...]) -> dict[str, Any]:
    return {"levels": [level_to_dto(level).model_dump(mode="json") for level in levels]}


def snapshot_payload_from_runtime(snapshot: RuntimeSnapshot) -> SnapshotPayload:
    return SnapshotPayload(
        current_bars=[bar_to_dto(bar) for bar in snapshot.current_bars],
        recent_closed_bars=[bar_to_dto(bar) for bar in snapshot.recent_closed_bars],
        display_levels=[level_to_dto(level) for level in snapshot.display_levels],
        active_observations=[observation_to_dto(obs) for obs in snapshot.active_observations],
        feed_status=feed_status_to_dto(snapshot.feed_status),
        warnings=[warning_to_dto(warning) for warning in snapshot.warnings],
    )


def empty_snapshot_payload(*, requested_symbol: str | None) -> SnapshotPayload:
    return SnapshotPayload(
        feed_status=FeedStatusDTO(
            state=FeedConnectionState.DISCONNECTED.value,
            mode="idle",
            requested_symbol=requested_symbol,
            last_message="Market-data feed is not started in Phase 2B.",
        )
    )


def make_envelope(
    message_type: MessageType,
    sequence: int,
    payload: BaseModel | dict[str, Any],
) -> dict[str, Any]:
    if isinstance(payload, BaseModel):
        payload_dict = payload.model_dump(mode="json", by_alias=True)
    else:
        payload_dict = payload
    envelope = Envelope(
        type=message_type,
        sequence=sequence,
        server_time_utc=datetime.now(UTC),
        payload=payload_dict,
    )
    return envelope.model_dump(mode="json", by_alias=True)
