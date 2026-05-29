"""Feed status domain state kept independent from FastAPI/WebSocket DTOs."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class FeedConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    REPLAYING = "replaying"


@dataclass(frozen=True, slots=True)
class FeedStatus:
    state: FeedConnectionState
    mode: str
    requested_symbol: str | None
    raw_symbol: str | None = None
    dataset: str | None = None
    schema: str | None = None
    last_event_ts_utc: datetime | None = None
    last_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.last_event_ts_utc is not None:
            if self.last_event_ts_utc.tzinfo is None:
                raise ValueError("feed status timestamp must be timezone-aware UTC datetime")
            object.__setattr__(self, "last_event_ts_utc", self.last_event_ts_utc.astimezone(UTC))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
