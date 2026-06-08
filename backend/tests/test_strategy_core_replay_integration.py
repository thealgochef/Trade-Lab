import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trade_lab.domain.events import TradeEvent
from trade_lab.ports.market_data import HistoricalMarketDataSource
from trade_lab.services.replay import HistoricalReplayService, ReplayConfig, ReplayState
from trade_lab.services.runtime import ApplicationRuntime


class FakeSource(HistoricalMarketDataSource):
    def __init__(self, items):
        self._items = items

    def scan(self, paths, *, requested_symbol, schema, start_ts_utc=None, end_ts_utc=None):
        yield from self._items


@pytest.mark.asyncio
async def test_trade_lab_replay_runtime_uses_strategy_core_service_for_bars() -> None:
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0",
        tick_timeframes=(2,),
        observation_duration_seconds=300,
    )
    service = HistoricalReplayService(runtime)
    await service.start(
        FakeSource(
            (
                TradeEvent(
                    datetime(2026, 1, 6, 14, tzinfo=UTC),
                    None,
                    1,
                    "NQ.c.0",
                    "NQM6",
                    68000,
                    1,
                ),
                TradeEvent(
                    datetime(2026, 1, 6, 14, 1, tzinfo=UTC),
                    None,
                    1,
                    "NQ.c.0",
                    "NQM6",
                    68004,
                    1,
                ),
            )
        ),
        ReplayConfig(
            paths=(Path("synthetic:safe"),),
            requested_symbol="NQ.c.0",
            schema="trades",
        ),
    )
    while service.status().state not in {ReplayState.COMPLETED, ReplayState.FAILED}:
        await asyncio.sleep(0)
    assert service.status().state == ReplayState.COMPLETED
    assert runtime.snapshot().recent_closed_bars[0].bar_id == "2t:2026-01-06:0"
    assert service.strategy_core_replay is not None
    assert runtime.strategy_core_service is not None
