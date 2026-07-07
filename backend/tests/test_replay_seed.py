"""SEED window: dashboard replays get the training-parity PDH/PDL seed.

The seed rides ``HistoricalReplayService.start`` — after the runtime reset (a seed on the
pre-reset runtime would be wiped with the rebuilt service) and before the core replay task
(so the summary is banked before the first event). Value source = the canonical store walk
(``strategy_core.data.prior_day.prior_full_day_extremes``), proven tick-exact equal to QL
training's ``prev_full_hl`` carry in SEED_PARITY_RECON.md §5.
"""

import asyncio
import logging
from datetime import UTC, date, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from trade_lab.domain.events import TradeEvent
from trade_lab.domain.levels import LevelKind
from trade_lab.ports.market_data import HistoricalMarketDataSource
from trade_lab.services.replay import HistoricalReplayService, ReplayConfig, ReplayState
from trade_lab.services.runtime import ApplicationRuntime


class FakeDaySource(HistoricalMarketDataSource):
    """Fake source that tolerates the day-mode scan kwargs (trading_day/symbol_dir)."""

    def __init__(self, items=()):
        self._items = tuple(items)

    def scan(
        self,
        paths,
        *,
        requested_symbol,
        schema,
        start_ts_utc=None,
        end_ts_utc=None,
        **_day_kwargs,
    ):
        yield from self._items


def _runtime() -> ApplicationRuntime:
    return ApplicationRuntime(
        requested_symbol="NQ",
        tick_timeframes=(2,),
        observation_duration_seconds=300,
    )


def _write_prior_day(symbol_dir: Path) -> None:
    """Fixture store: prior day 2026-03-09 with trades at 17001.00 / 17000.25."""
    day_dir = symbol_dir / "2026-03-09"
    day_dir.mkdir(parents=True)
    rows = [
        {
            "ts_event": datetime(2026, 3, 9, 14, 0, tzinfo=UTC),
            "price": 17001.00,
            "size": 1,
            "side": "B",
            "sequence": 1,
        },
        {
            "ts_event": datetime(2026, 3, 9, 14, 1, tzinfo=UTC),
            "price": 17000.25,
            "size": 1,
            "side": "A",
            "sequence": 2,
        },
    ]
    pq.write_table(pa.Table.from_pylist(rows), day_dir / "trades.parquet")


def _day_config(symbol_dir: Path) -> ReplayConfig:
    return ReplayConfig(
        paths=(Path("synthetic:seed"),),
        requested_symbol="NQ",
        schema="trades",
        trading_day=date(2026, 3, 10),
        symbol_dir=symbol_dir,
    )


def _replay_trade(minute: int, price_ticks: int, seq: int) -> TradeEvent:
    return TradeEvent(
        datetime(2026, 3, 10, 14, minute, tzinfo=UTC),
        None,
        1,
        "NQ",
        "NQM6",
        price_ticks,
        1,
    )


async def _wait_terminal(service: HistoricalReplayService) -> None:
    while service.status().state not in {ReplayState.COMPLETED, ReplayState.FAILED}:
        await asyncio.sleep(0)
    assert service.status().state == ReplayState.COMPLETED


def _level_ticks(runtime: ApplicationRuntime, kind: LevelKind) -> set[int]:
    return {
        level.price_ticks
        for level in runtime.snapshot().display_levels
        if level.kind == kind
    }


@pytest.mark.asyncio
async def test_day_replay_seeds_pdh_pdl_from_the_store_walk(tmp_path: Path) -> None:
    symbol_dir = tmp_path / "NQ"
    _write_prior_day(symbol_dir)
    runtime = _runtime()
    service = HistoricalReplayService(runtime)
    await service.start(
        FakeDaySource((_replay_trade(0, 68010, 1), _replay_trade(1, 68012, 2))),
        _day_config(symbol_dir),
    )
    # start() has seeded and set the LOADING status; the replay task has not run yet.
    assert (
        "seeded PDH/PDL from 2026-03-09"
        in (runtime.snapshot().feed_status.last_message or "")
    )
    await _wait_terminal(service)
    assert _level_ticks(runtime, LevelKind.PDH) == {68004}
    assert _level_ticks(runtime, LevelKind.PDL) == {68001}


@pytest.mark.asyncio
async def test_walk_miss_warns_and_replay_completes_unseeded(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    symbol_dir = tmp_path / "NQ"
    symbol_dir.mkdir(parents=True)  # store exists but holds no prior day at all
    runtime = _runtime()
    service = HistoricalReplayService(runtime)
    with caplog.at_level(logging.WARNING, logger="trade_lab.services.replay"):
        await service.start(
            FakeDaySource((_replay_trade(0, 68010, 1), _replay_trade(1, 68012, 2))),
            _day_config(symbol_dir),
        )
        await _wait_terminal(service)
    assert any(
        "replay unseeded: no prior store day with trades" in record.getMessage()
        for record in caplog.records
    )
    assert "seeded PDH/PDL" not in (runtime.snapshot().feed_status.last_message or "")
    assert _level_ticks(runtime, LevelKind.PDH) == set()
    assert _level_ticks(runtime, LevelKind.PDL) == set()


@pytest.mark.asyncio
async def test_seed_lands_on_the_post_reset_runtime(tmp_path: Path) -> None:
    symbol_dir = tmp_path / "NQ"
    _write_prior_day(symbol_dir)
    runtime = _runtime()
    service = HistoricalReplayService(runtime)
    service_before = runtime.strategy_core_service
    # No replay events at all: the seed must not depend on the replayed stream.
    await service.start(FakeDaySource(()), _day_config(symbol_dir))
    service_after = runtime.strategy_core_service
    assert service_after is not service_before  # reset rebuilt the SC service
    await _wait_terminal(service)
    # The banked summary lives on the POST-reset service: the first trade folded through
    # the runtime emits the fixture's PDH/PDL (a pre-reset seed would have been wiped).
    runtime.process_market_event(_replay_trade(0, 68010, 1))
    assert _level_ticks(runtime, LevelKind.PDH) == {68004}
    assert _level_ticks(runtime, LevelKind.PDL) == {68001}
