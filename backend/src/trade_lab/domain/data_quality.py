"""Data-quality primitives shared by adapters, services, and API DTO mapping.

Warnings are domain-level dataclasses, not frontend payloads. Adapters emit them
when input data cannot be normalized safely; the API layer serializes them later.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class DataQualityCode(StrEnum):
    MISSING_REQUIRED_COLUMN = "missing_required_column"
    INVALID_TIMESTAMP = "invalid_timestamp"
    INVALID_PRICE = "invalid_price"
    INVALID_RECORD = "invalid_record"
    UNSUPPORTED_SCHEMA = "unsupported_schema"
    HISTORICAL_ONLY_FIELD_IGNORED = "historical_only_field_ignored"
    TIMESTAMP_REGRESSION = "timestamp_regression"
    BACKPRESSURE_DROP = "backpressure_drop"
    PROVIDER_ERROR = "provider_error"


class DataQualitySeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class DataQualityWarning:
    code: DataQualityCode
    message: str
    severity: DataQualitySeverity = DataQualitySeverity.WARNING
    source: str | None = None
    event_ts_utc: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.event_ts_utc is not None:
            if self.event_ts_utc.tzinfo is None:
                raise ValueError("warning timestamp must be timezone-aware UTC datetime")
            object.__setattr__(self, "event_ts_utc", self.event_ts_utc.astimezone(UTC))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
