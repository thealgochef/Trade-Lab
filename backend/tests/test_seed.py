import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pandas as pd

from trade_lab.adapters.databento_historical import DatabentoHistoricalSource
from trade_lab.domain.data_quality import DataQualityCode
from trade_lab.services.live import LiveConfig, LiveMarketDataService, LiveState
from trade_lab.services.runtime import ApplicationRuntime
from trade_lab.services.seed import HistoricalSeedService

# now() pinned to 2026-01-07 14:00 CT (NY session) -> current trading day 2026-01-07,
# so seed bars must come only from completed sessions strictly before it.
_FIXED_NOW = datetime(2026, 1, 7, 20, 0, tzinfo=UTC)

# (ts_event_iso, price_dollars, size); prices are 0.25-aligned NQ-like values.
_SEED_ROWS = [
    ("2026-01-05T18:00:00Z", 17000.00, 1),
    ("2026-01-05T18:00:01Z", 17000.25, 1),
    ("2026-01-05T18:00:02Z", 17000.50, 1),
    ("2026-01-06T18:00:00Z", 17001.00, 1),
    ("2026-01-06T18:00:01Z", 17001.25, 1),
    ("2026-01-06T18:00:02Z", 17001.50, 1),
    # 2026-01-07 is the current trading day -> excluded from the seed.
    ("2026-01-07T18:00:00Z", 17002.00, 1),
    ("2026-01-07T18:00:01Z", 17002.25, 1),
]


class _FakeFeed:
    def __init__(self, events: list[object]) -> None:
        self._events = events

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def events(self) -> AsyncIterator[object]:
        for event in self._events:
            yield event


def _frame_from_rows(rows: list[tuple[str, float, int]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_event": pd.to_datetime([row[0] for row in rows], utc=True),
            "price": [float(row[1]) for row in rows],
            "size": [int(row[2]) for row in rows],
        }
    )


def _frame_fetcher(rows: list[tuple[str, float, int]]):
    def fetcher(start: datetime, end: datetime) -> pd.DataFrame:
        return _frame_from_rows(rows)

    return fetcher


def _source(frame_fetcher=None, *, custom: bool = True) -> DatabentoHistoricalSource:
    return DatabentoHistoricalSource(
        api_key=None,
        dataset="GLBX.MDP3",
        requested_symbol="NQ.c.0",
        stype_in="continuous",
        frame_fetcher=frame_fetcher if custom else None,
    )


def _seed_service(rows=None, *, enabled: bool = True, custom: bool = True) -> HistoricalSeedService:
    return HistoricalSeedService(
        _source(_frame_fetcher(rows if rows is not None else _SEED_ROWS), custom=custom),
        tick_timeframes=(2,),
        lookback_days=2,
        max_bars_per_timeframe=100,
        enabled=enabled,
        now_provider=lambda: _FIXED_NOW,
    )


def test_build_seed_bars_keeps_only_recent_completed_sessions() -> None:
    bars = _seed_service().build_seed_bars()

    assert bars  # non-empty warm-up
    assert {bar.trading_day.isoformat() for bar in bars} == {"2026-01-05", "2026-01-06"}
    assert all(bar.timeframe_ticks == 2 for bar in bars)
    assert all(bar.trading_day.isoformat() < "2026-01-07" for bar in bars)


def test_seed_is_skipped_when_disabled_or_source_unavailable() -> None:
    assert _seed_service(enabled=False).enabled is False
    assert _seed_service(enabled=False).build_seed_bars() == ()
    # Default fetcher with no api key / no SDK access is treated as unavailable.
    unavailable = _seed_service(custom=False)
    assert unavailable.enabled is False
    assert unavailable.build_seed_bars() == ()


def test_runtime_seed_store_is_bounded_per_timeframe_and_served_in_snapshot() -> None:
    bars = _seed_service().build_seed_bars()
    assert len(bars) >= 3

    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0",
        tick_timeframes=(2,),
        observation_duration_seconds=300,
        seed_bar_limit_per_timeframe=2,
    )
    update = runtime.seed_closed_bars(bars)

    snapshot_bars = runtime.snapshot().recent_closed_bars
    assert update.closed_bars == bars
    assert len(snapshot_bars) == 2  # bounded to newest 2 for the single timeframe
    assert snapshot_bars == bars[-2:]


def test_runtime_reset_clears_seed_bars() -> None:
    bars = _seed_service().build_seed_bars()
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0",
        tick_timeframes=(2,),
        observation_duration_seconds=300,
    )
    runtime.seed_closed_bars(bars)
    assert runtime.snapshot().recent_closed_bars

    runtime.reset(requested_symbol="NQ.c.0")

    assert runtime.snapshot().recent_closed_bars == ()


def test_live_start_seeds_chart_and_broadcasts_history() -> None:
    asyncio.run(_run_live_start_seeds())


async def _run_live_start_seeds() -> None:
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0",
        tick_timeframes=(2,),
        observation_duration_seconds=300,
    )
    updates: list[object] = []

    async def capture(update: object) -> None:
        updates.append(update)

    live = LiveMarketDataService(
        runtime,
        LiveConfig(
            requested_symbol="NQ.c.0",
            dataset="GLBX.MDP3",
            trade_schema="trades",
            quote_schema="mbp-1",
            context_schemas=(),
            api_key_configured=True,
            enabled=True,
        ),
        lambda _config: _FakeFeed([]),
        on_update=capture,
        seed_service=_seed_service(),
    )

    await live.start()
    assert live._seed_task is not None
    await live._seed_task
    assert live._task is not None
    await live._task

    snapshot = runtime.snapshot()
    assert {bar.trading_day.isoformat() for bar in snapshot.recent_closed_bars} == {
        "2026-01-05",
        "2026-01-06",
    }
    assert any(getattr(update, "closed_bars", ()) for update in updates)

    await live.stop()
    assert live.status().state == LiveState.STOPPED


def test_live_start_surfaces_warning_when_seed_unavailable() -> None:
    asyncio.run(_run_live_start_seed_failure())


async def _run_live_start_seed_failure() -> None:
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0",
        tick_timeframes=(2,),
        observation_duration_seconds=300,
    )
    updates: list[object] = []

    async def capture(update: object) -> None:
        updates.append(update)

    def boom(start: datetime, end: datetime) -> pd.DataFrame:
        raise RuntimeError("provider unavailable")

    live = LiveMarketDataService(
        runtime,
        LiveConfig(
            requested_symbol="NQ.c.0",
            dataset="GLBX.MDP3",
            trade_schema="trades",
            quote_schema="mbp-1",
            context_schemas=(),
            api_key_configured=True,
            enabled=True,
        ),
        lambda _config: _FakeFeed([]),
        on_update=capture,
        seed_service=HistoricalSeedService(
            _source(boom),
            tick_timeframes=(2,),
            now_provider=lambda: _FIXED_NOW,
        ),
    )

    await live.start()
    assert live._seed_task is not None
    await live._seed_task
    await live._task

    warnings = [w for update in updates for w in getattr(update, "warnings", ())]
    assert any(w.code == DataQualityCode.PROVIDER_ERROR and w.source == "seed" for w in warnings)
    assert runtime.snapshot().recent_closed_bars == ()

    await live.stop()
