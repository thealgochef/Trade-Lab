"""Allowlisted deterministic replay sources for safe UI demos and tests.

Synthetic sources are registered by opaque ids instead of filesystem paths so the
public replay API cannot be used to probe arbitrary local files or real raw data.
They still emit canonical market events and flow through the same runtime engines
as future live feeds, which keeps chart/replay semantics identical.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trade_lab.domain.events import MarketEvent, MarketStatus, MarketStatusEvent, TradeEvent


@dataclass(frozen=True, slots=True)
class ReplaySourceDefinition:
    source_id: str
    label: str
    requested_symbol: str
    schema: str
    kind: str = "synthetic"
    session_label: str | None = None
    availability: str | None = None
    paths: tuple[Path, ...] = ()


class SyntheticNqDemoSource:
    """Deterministic in-memory NQ replay with bars, levels, touches, observations."""

    def scan(
        self,
        paths: Iterable[Path],
        *,
        requested_symbol: str,
        schema: str,
        start_ts_utc: datetime | None = None,
        end_ts_utc: datetime | None = None,
    ) -> Iterator[MarketEvent]:
        _ = (paths, schema)
        for event in _nq_demo_events(requested_symbol):
            if start_ts_utc is not None and event.event_ts_utc < start_ts_utc:
                continue
            if end_ts_utc is not None and event.event_ts_utc >= end_ts_utc:
                continue
            yield event


def _nq_demo_events(requested_symbol: str) -> Iterator[MarketEvent]:
    start = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    instrument_id = 1001
    raw_symbol = "NQH6"
    yield MarketStatusEvent(start, instrument_id, MarketStatus.OPEN, "synthetic replay opened")

    # First 180 trades occur during Asia and intentionally create a stable high at
    # 68_020 ticks. London later revisits that exact level, producing an eligible
    # touch and observation through SessionLevelEngine/ObservationEngine.
    for i in range(180):
        price = 68_000 + (i % 21)
        yield TradeEvent(
            event_ts_utc=start + timedelta(seconds=i),
            receive_ts_utc=None,
            instrument_id=instrument_id,
            requested_symbol=requested_symbol,
            raw_symbol=raw_symbol,
            price_ticks=price,
            size=1 + (i % 3),
            source_schema="trades",
            metadata={"source": "synthetic:nq-demo"},
        )

    london_start = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
    for i in range(2_120):
        price = 68_020 if i == 3 else 68_010 + ((i * 7) % 8)
        yield TradeEvent(
            event_ts_utc=london_start + timedelta(seconds=i),
            receive_ts_utc=None,
            instrument_id=instrument_id,
            requested_symbol=requested_symbol,
            raw_symbol=raw_symbol,
            price_ticks=price,
            size=1 + (i % 4),
            source_schema="trades",
            metadata={"source": "synthetic:nq-demo"},
        )


SYNTHETIC_NQ_DEMO = ReplaySourceDefinition(
    source_id="synthetic:nq-demo",
    label="Synthetic NQ demo (bars, levels, touches)",
    requested_symbol="NQ.c.0",
    schema="trades",
)


def default_synthetic_sources() -> dict[str, tuple[ReplaySourceDefinition, SyntheticNqDemoSource]]:
    return {SYNTHETIC_NQ_DEMO.source_id: (SYNTHETIC_NQ_DEMO, SyntheticNqDemoSource())}
