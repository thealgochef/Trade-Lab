from collections.abc import AsyncIterator, Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trade_lab.api.dto import feed_status_to_dto, warning_to_dto
from trade_lab.api.serialization import dumps_bytes
from trade_lab.domain.data_quality import DataQualityCode, DataQualitySeverity, DataQualityWarning
from trade_lab.domain.events import MarketEvent, TradeEvent
from trade_lab.domain.feed import FeedConnectionState, FeedStatus
from trade_lab.ports.market_data import HistoricalMarketDataSource, MarketDataFeed


class FakeMarketDataFeed:
    def __init__(self) -> None:
        self.started = False

    async def events(self) -> AsyncIterator[MarketEvent | DataQualityWarning | FeedStatus]:
        yield FeedStatus(FeedConnectionState.CONNECTED, "synthetic", "NQ.c.0")

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False


class FakeHistoricalSource:
    def scan(
        self,
        paths: Iterable[Path],
        *,
        requested_symbol: str,
        schema: str,
        start_ts_utc: datetime | None = None,
        end_ts_utc: datetime | None = None,
    ) -> Iterator[MarketEvent | DataQualityWarning]:
        _ = (paths, schema, start_ts_utc, end_ts_utc)
        yield TradeEvent(
            datetime(2026, 1, 1, tzinfo=UTC),
            None,
            1,
            requested_symbol,
            "NQM6",
            68_000,
            1,
        )


def test_market_data_protocol_contracts_are_structurally_usable() -> None:
    feed: MarketDataFeed = FakeMarketDataFeed()
    source: HistoricalMarketDataSource = FakeHistoricalSource()

    events = list(source.scan([], requested_symbol="NQ.c.0", schema="trades"))

    assert feed is not None
    assert isinstance(events[0], TradeEvent)
    assert events[0].requested_symbol == "NQ.c.0"


def test_warning_dto_contains_severity_code_source_message_and_is_safe() -> None:
    warning = DataQualityWarning(
        code=DataQualityCode.INVALID_PRICE,
        message="bad synthetic price",
        severity=DataQualitySeverity.WARNING,
        source="synthetic-fixture",
        event_ts_utc=datetime(2026, 1, 1, tzinfo=UTC),
        metadata={"column": "price"},
    )

    dto = warning_to_dto(warning).model_dump(mode="json")

    assert dto == {
        "code": "invalid_price",
        "message": "bad synthetic price",
        "severity": "warning",
        "source": "synthetic-fixture",
        "event_ts_utc": "2026-01-01T00:00:00Z",
        "metadata": {"column": "price"},
    }


def test_feed_status_serializes_safely_without_secret_like_fields() -> None:
    status = FeedStatus(
        state=FeedConnectionState.DEGRADED,
        mode="historical",
        requested_symbol="NQ.c.0",
        raw_symbol="NQM6",
        dataset="GLBX.MDP3",
        schema="trades",
        last_event_ts_utc=datetime(2026, 1, 1, tzinfo=UTC),
        last_message="synthetic warning",
        metadata={"safe": True},
    )

    payload = feed_status_to_dto(status).model_dump(mode="json", by_alias=True)
    serialized = dumps_bytes(payload).decode().lower()

    assert payload["state"] == "degraded"
    assert payload["schema"] == "trades"
    assert "api_key" not in serialized
    assert "secret" not in serialized
    assert "token" not in serialized


def test_warning_and_feed_status_reject_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="warning timestamp"):
        DataQualityWarning(
            code=DataQualityCode.INVALID_TIMESTAMP,
            message="bad",
            event_ts_utc=datetime(2026, 1, 1),
        )

    with pytest.raises(ValueError, match="feed status timestamp"):
        FeedStatus(
            state=FeedConnectionState.CONNECTED,
            mode="live",
            requested_symbol="NQ.c.0",
            last_event_ts_utc=datetime(2026, 1, 1),
        )
