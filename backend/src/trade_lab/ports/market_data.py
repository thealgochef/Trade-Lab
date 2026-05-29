"""Market-data ports for live and historical adapters.

Both live and replay adapters emit the same canonical domain events. This keeps
engine semantics deterministic and prevents historical-only data from becoming a
runtime feature accidentally.
"""

from collections.abc import AsyncIterator, Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Protocol

from trade_lab.domain.data_quality import DataQualityWarning
from trade_lab.domain.events import MarketEvent
from trade_lab.domain.feed import FeedStatus


class MarketDataFeed(Protocol):
    """Async live/replay feed emitting canonical events and status updates."""

    async def events(self) -> AsyncIterator[MarketEvent | DataQualityWarning | FeedStatus]: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class HistoricalMarketDataSource(Protocol):
    """Read-only source for backfill/replay scans over canonical events."""

    def scan(
        self,
        paths: Iterable[Path],
        *,
        requested_symbol: str,
        schema: str,
        start_ts_utc: datetime | None = None,
        end_ts_utc: datetime | None = None,
    ) -> Iterator[MarketEvent | DataQualityWarning]: ...
