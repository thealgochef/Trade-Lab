import asyncio
import json
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from trade_lab.adapters.synthetic_replay import SyntheticNqDemoSource, default_synthetic_sources
from trade_lab.domain.data_quality import DataQualityCode, DataQualityWarning
from trade_lab.domain.events import (
    DailyStatisticEvent,
    InstrumentDefinitionEvent,
    MarketStatus,
    MarketStatusEvent,
    TopOfBookEvent,
    TradeEvent,
)
from trade_lab.services.broadcaster import WebSocketBroadcaster
from trade_lab.services.replay import HistoricalReplayService, ReplayConfig, ReplayState
from trade_lab.services.runtime import ApplicationRuntime


def _runtime() -> ApplicationRuntime:
    return ApplicationRuntime(
        requested_symbol="NQ.c.0",
        tick_timeframes=(2,),
        observation_duration_seconds=300,
    )


def _trade(i: int, *, price_ticks: int = 68_000) -> TradeEvent:
    return TradeEvent(
        event_ts_utc=datetime(2026, 1, 5, 0, 0, tzinfo=UTC) + timedelta(seconds=i),
        receive_ts_utc=None,
        instrument_id=1,
        requested_symbol="NQ.c.0",
        raw_symbol="NQM6",
        price_ticks=price_ticks,
        size=1,
        source_schema="trades",
    )


def _quote(i: int) -> TopOfBookEvent:
    return TopOfBookEvent(
        event_ts_utc=datetime(2026, 1, 5, 0, 0, tzinfo=UTC) + timedelta(seconds=i),
        instrument_id=1,
        bid_price_ticks=67_999,
        bid_size=1,
        ask_price_ticks=68_001,
        ask_size=1,
        source_schema="mbp-1",
    )


def _json_messages(broadcaster: WebSocketBroadcaster, update) -> list[dict[str, object]]:
    return [json.loads(message) for message in broadcaster.messages_for_update(update)]


def _drain_json(queue: asyncio.Queue[bytes]) -> list[dict[str, object]]:
    return [json.loads(queue.get_nowait()) for _ in range(queue.qsize())]


def _safe_text(payload: object) -> str:
    return json.dumps(payload, default=str).lower()


def _assert_no_path_or_secret(payload: object) -> None:
    text = _safe_text(payload)
    assert "c:\\users" not in text
    assert "/users/" not in text
    assert ".." not in text
    assert "secret" not in text
    assert "token" not in text
    assert "password" not in text


async def _wait_terminal(service: HistoricalReplayService) -> None:
    while service.status().state in {ReplayState.READY, ReplayState.RUNNING, ReplayState.LOADING}:
        await asyncio.sleep(0)


def test_runtime_only_trades_advance_bars() -> None:
    runtime = _runtime()
    quote_update = runtime.process_market_event(
        TopOfBookEvent(
            event_ts_utc=datetime(2026, 1, 5, 0, 0, tzinfo=UTC),
            instrument_id=1,
            bid_price_ticks=67_999,
            bid_size=1,
            ask_price_ticks=68_001,
            ask_size=1,
            source_schema="mbp-1",
        )
    )

    assert quote_update.current_bars == ()
    assert runtime.snapshot().current_bars == ()

    first = runtime.process_market_event(_trade(1))
    second = runtime.process_market_event(_trade(2, price_ticks=68_001))

    assert len(first.current_bars) == 1
    assert len(second.closed_bars) == 1
    assert runtime.snapshot().recent_closed_bars == second.closed_bars


def test_trade_events_advance_levels_touches_and_observations() -> None:
    runtime = _runtime()

    runtime.process_market_event(_trade(0, price_ticks=68_000))
    runtime.process_market_event(_trade(1, price_ticks=68_004))
    touch_update = runtime.process_market_event(
        TradeEvent(
            event_ts_utc=datetime(2026, 1, 5, 8, 0, tzinfo=UTC),
            receive_ts_utc=None,
            instrument_id=1,
            requested_symbol="NQ.c.0",
            raw_symbol="NQM6",
            price_ticks=68_004,
            size=1,
            source_schema="trades",
        )
    )

    snapshot = runtime.snapshot()
    assert len(touch_update.touches) == 1
    assert touch_update.touches[0].level_kind.value == "asia_high"
    assert len(touch_update.observations) == 1
    assert snapshot.active_observations == touch_update.observations
    assert any(level.kind.value == "asia_high" for level in snapshot.display_levels)


def test_top_of_book_updates_context_without_candles_touches_or_observations() -> None:
    runtime = _runtime()

    update = runtime.process_market_event(
        TopOfBookEvent(
            event_ts_utc=datetime(2026, 1, 5, 0, 0, tzinfo=UTC),
            instrument_id=1,
            bid_price_ticks=67_999,
            bid_size=1,
            ask_price_ticks=68_001,
            ask_size=1,
            source_schema="mbp-1",
        )
    )

    assert update.feed_status is not None
    assert update.feed_status.schema == "mbp-1"
    assert update.current_bars == ()
    assert update.closed_bars == ()
    assert update.touches == ()
    assert update.observations == ()
    assert runtime.snapshot().current_bars == ()


def test_metadata_events_update_only_metadata_status_and_summary() -> None:
    runtime = _runtime()

    definition = runtime.process_market_event(
        InstrumentDefinitionEvent(
            event_ts_utc=datetime(2026, 1, 5, 0, 0, tzinfo=UTC),
            instrument_id=1,
            requested_symbol="NQ.c.0",
            raw_symbol="NQM6",
            tick_size=Decimal("0.25"),
        )
    )
    status = runtime.process_market_event(
        MarketStatusEvent(
            event_ts_utc=datetime(2026, 1, 5, 0, 1, tzinfo=UTC),
            instrument_id=1,
            status=MarketStatus.OPEN,
            reason="regular open",
        )
    )
    statistic = runtime.process_market_event(
        DailyStatisticEvent(
            event_ts_utc=datetime(2026, 1, 5, 0, 2, tzinfo=UTC),
            instrument_id=1,
            statistic_type="settlement",
            price_ticks=68_100,
            value=Decimal("17025.00"),
            source_schema="statistics",
        )
    )

    snapshot = runtime.snapshot()
    assert definition.current_bars == status.current_bars == statistic.current_bars == ()
    assert definition.touches == status.touches == statistic.touches == ()
    assert snapshot.metadata["instrument"]["raw_symbol"] == "NQM6"
    assert snapshot.metadata["market_status"] == "open"
    assert snapshot.metadata["daily_statistics"]["settlement"] == {
        "price_ticks": 68100,
        "value": Decimal("17025.00"),
    }


def test_runtime_snapshot_is_bounded_and_sanitizes_warning_paths_and_secrets() -> None:
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0",
        tick_timeframes=(1,),
        observation_duration_seconds=300,
        warning_limit=3,
        recent_closed_bar_limit=2,
    )
    for i in range(5):
        runtime.process_market_event(_trade(i, price_ticks=68_000 + i))
    runtime.record_warning(
        DataQualityWarning(
            code=DataQualityCode.INVALID_TIMESTAMP,
            message="failed C:\\Users\\gonza\\secret\\raw.parquet token=abc123",
            source="C:\\Users\\gonza\\secret\\raw.parquet",
            metadata={"path": "C:\\Users\\gonza\\secret\\raw.parquet", "api_key": "secret"},
        )
    )
    runtime.record_warning(
        DataQualityWarning(code=DataQualityCode.INVALID_PRICE, message="second", source="synthetic")
    )
    runtime.record_warning(
        DataQualityWarning(
            code=DataQualityCode.UNSUPPORTED_SCHEMA, message="third", source="synthetic"
        )
    )

    snapshot = runtime.snapshot()
    serialized = _safe_text(snapshot)
    assert len(snapshot.recent_closed_bars) == 2
    assert len(snapshot.warnings) == 3
    assert "c:\\users" not in serialized
    assert "abc123" not in serialized
    assert "api_key" not in serialized
    assert "secret" not in serialized


def test_broadcaster_maps_runtime_update_to_versioned_domain_deltas() -> None:
    runtime = _runtime()
    update = runtime.process_market_event(_trade(1))
    broadcaster = WebSocketBroadcaster(runtime)

    messages = _json_messages(broadcaster, update)

    assert [message["type"] for message in messages] == [
        "feed.status",
        "market.bar.updated",
        "levels.updated",
    ]
    assert [message["sequence"] for message in messages] == [1, 2, 3]
    assert messages[1]["payload"]["bars"][0]["trade_count"] == 1


def test_broadcaster_emits_required_domain_envelopes_and_monotonic_sequences() -> None:
    runtime = _runtime()
    runtime.process_market_event(_trade(0, price_ticks=68_000))
    closed_update = runtime.process_market_event(_trade(1, price_ticks=68_004))
    touch_update = runtime.process_market_event(
        TradeEvent(datetime(2026, 1, 5, 8, 0, tzinfo=UTC), None, 1, "NQ.c.0", "NQM6", 68_004, 1)
    )
    warning_update = runtime.record_warning(
        DataQualityWarning(code=DataQualityCode.HISTORICAL_ONLY_FIELD_IGNORED, message="ignored")
    )
    broadcaster = WebSocketBroadcaster(runtime)

    messages = (
        _json_messages(broadcaster, warning_update)
        + _json_messages(broadcaster, closed_update)
        + _json_messages(broadcaster, touch_update)
    )

    assert [message["sequence"] for message in messages] == list(range(1, len(messages) + 1))
    by_type = {message["type"]: message for message in messages}
    assert set(by_type) >= {
        "feed.status",
        "data_quality.warning",
        "market.bar.updated",
        "market.bar.closed",
        "levels.updated",
        "touch.detected",
        "observation.updated",
    }
    assert set(by_type["feed.status"]["payload"]) >= {"state", "mode", "requested_symbol"}
    assert "bars" in by_type["market.bar.updated"]["payload"]
    assert "levels" in by_type["levels.updated"]["payload"]
    assert "touch_id" in by_type["touch.detected"]["payload"]
    assert "observation_id" in by_type["observation.updated"]["payload"]


def test_trade_processing_does_not_emit_raw_tick_spam_by_default() -> None:
    runtime = _runtime()
    broadcaster = WebSocketBroadcaster(runtime)

    messages = _json_messages(broadcaster, runtime.process_market_event(_trade(1)))

    assert {message["type"] for message in messages}.isdisjoint({"tick", "trade", "raw.tick"})
    assert all(
        "price_ticks" not in message["payload"]
        for message in messages
        if message["type"] != "levels.updated"
    )


@pytest.mark.asyncio
async def test_broadcaster_backpressure_is_bounded_and_disconnect_removes_client() -> None:
    runtime = _runtime()
    broadcaster = WebSocketBroadcaster(runtime, queue_depth=2)
    queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=2)
    broadcaster._clients.add(queue)
    queue.put_nowait(b"old-1")
    queue.put_nowait(b"old-2")

    await broadcaster.broadcast_update(runtime.process_market_event(_trade(1)))

    messages = _drain_json(queue)
    broadcaster.disconnect(queue)

    assert broadcaster.dropped_messages >= 1
    assert messages[-1]["type"] == "levels.updated"
    assert any(message["type"] == "market.bar.updated" for message in messages)
    assert queue not in broadcaster._clients


@pytest.mark.asyncio
async def test_broadcaster_backpressure_retains_newest_domain_delta() -> None:
    runtime = _runtime()
    broadcaster = WebSocketBroadcaster(runtime, queue_depth=2)
    queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=2)
    broadcaster._clients.add(queue)
    queue.put_nowait(b"stale-1")
    queue.put_nowait(b"stale-2")

    await broadcaster.broadcast_update(runtime.process_market_event(_trade(1)))

    messages = _drain_json(queue)

    assert [message["type"] for message in messages] == ["market.bar.updated", "levels.updated"]
    assert broadcaster.dropped_messages >= 2


@pytest.mark.asyncio
async def test_broadcaster_backpressure_warning_is_coalesced_not_repeated() -> None:
    runtime = _runtime()
    broadcaster = WebSocketBroadcaster(runtime, queue_depth=4)
    queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=4)
    broadcaster._clients.add(queue)
    for i in range(4):
        queue.put_nowait(json.dumps({"type": f"stale-{i}"}).encode())

    await broadcaster.broadcast_update(runtime.process_market_event(_trade(1)))
    overflow_messages = _drain_json(queue)
    await broadcaster.broadcast_update(runtime.process_market_event(_quote(2)))
    first_recovery_messages = _drain_json(queue)
    await broadcaster.broadcast_update(runtime.process_market_event(_quote(3)))
    second_recovery_messages = _drain_json(queue)

    assert [message["type"] for message in overflow_messages][-3:] == [
        "feed.status",
        "market.bar.updated",
        "levels.updated",
    ]
    assert [message["type"] for message in first_recovery_messages].count(
        "data_quality.warning"
    ) == 1
    assert all(
        message["type"] != "data_quality.warning" for message in second_recovery_messages
    )


@pytest.mark.asyncio
async def test_broadcaster_backpressure_warning_is_per_overflow_client() -> None:
    runtime = _runtime()
    broadcaster = WebSocketBroadcaster(runtime, queue_depth=4)
    slow_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=4)
    healthy_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=4)
    broadcaster._clients.add(slow_queue)
    broadcaster._clients.add(healthy_queue)
    for i in range(4):
        slow_queue.put_nowait(json.dumps({"type": f"stale-{i}"}).encode())

    await broadcaster.broadcast_update(runtime.process_market_event(_trade(1)))
    _drain_json(slow_queue)
    _drain_json(healthy_queue)
    await broadcaster.broadcast_update(runtime.process_market_event(_quote(2)))
    slow_messages = _drain_json(slow_queue)
    healthy_messages = _drain_json(healthy_queue)

    slow_backpressure_warnings = [
        message for message in slow_messages if message["type"] == "data_quality.warning"
    ]
    assert len(slow_backpressure_warnings) == 1
    assert slow_backpressure_warnings[0]["payload"]["metadata"]["dropped_messages"] == 3
    assert all(message["type"] != "data_quality.warning" for message in healthy_messages)


class FakeSource:
    def __init__(self, items: tuple[object, ...]) -> None:
        self.items = items
        self.items_yielded = 0

    def scan(
        self,
        paths: Iterable[Path],
        *,
        requested_symbol: str,
        schema: str,
        start_ts_utc: datetime | None = None,
        end_ts_utc: datetime | None = None,
    ) -> Iterator[object]:
        _ = (paths, requested_symbol, schema, start_ts_utc, end_ts_utc)
        for item in self.items:
            self.items_yielded += 1
            yield item


class FailingSource:
    def scan(self, *args: object, **kwargs: object) -> Iterator[object]:
        _ = (args, kwargs)
        raise RuntimeError("boom C:\\Users\\gonza\\secret\\ticks.parquet password=hunter2")
        yield


def test_default_synthetic_source_catalog_is_deterministic_allowlisted_and_path_free() -> None:
    first = default_synthetic_sources()
    second = default_synthetic_sources()

    assert list(first) == list(second) == ["synthetic:nq-demo"]
    definition, source = first["synthetic:nq-demo"]
    assert isinstance(source, SyntheticNqDemoSource)
    assert definition.source_id == "synthetic:nq-demo"
    assert definition.requested_symbol == "NQ.c.0"
    assert definition.schema == "trades"
    assert "/" not in definition.source_id
    assert "\\" not in definition.source_id
    assert ".." not in definition.source_id
    _assert_no_path_or_secret(definition)


def test_synthetic_replay_events_are_canonical_utc_integer_tick_events() -> None:
    events = list(
        SyntheticNqDemoSource().scan(
            (Path("synthetic:nq-demo"),), requested_symbol="NQ.c.0", schema="trades"
        )
    )
    trade_events = [event for event in events if isinstance(event, TradeEvent)]

    assert len(trade_events) >= 2_000
    assert events == list(
        SyntheticNqDemoSource().scan(
            (Path("synthetic:nq-demo"),), requested_symbol="NQ.c.0", schema="trades"
        )
    )
    assert [event.event_ts_utc for event in events] == sorted(
        event.event_ts_utc for event in events
    )
    assert all(event.event_ts_utc.tzinfo is UTC for event in events)
    assert all(isinstance(event.price_ticks, int) for event in trade_events)
    assert all(event.source_schema == "trades" for event in trade_events)
    assert {event.requested_symbol for event in trade_events} == {"NQ.c.0"}
    _assert_no_path_or_secret([event.metadata for event in trade_events])


def test_synthetic_replay_end_timestamp_is_exclusive() -> None:
    boundary = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)

    events = list(
        SyntheticNqDemoSource().scan(
            (Path("synthetic:nq-demo"),),
            requested_symbol="NQ.c.0",
            schema="trades",
            start_ts_utc=boundary,
            end_ts_utc=boundary,
        )
    )

    assert events == []


@pytest.mark.parametrize("timeframe", [147, 987, 2000])
def test_synthetic_replay_has_enough_trades_to_close_supported_tick_bars(timeframe: int) -> None:
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0", tick_timeframes=(timeframe,), observation_duration_seconds=300
    )

    for event in SyntheticNqDemoSource().scan(
        (Path("synthetic:nq-demo"),), requested_symbol="NQ.c.0", schema="trades"
    ):
        runtime.process_market_event(event)

    closed = [
        bar for bar in runtime.snapshot().recent_closed_bars if bar.timeframe_ticks == timeframe
    ]
    assert closed
    assert closed[-1].is_complete is True
    assert closed[-1].trade_count == timeframe


@pytest.mark.asyncio
async def test_replay_service_feeds_canonical_events_into_runtime() -> None:
    runtime = _runtime()
    warning = DataQualityWarning(
        code=DataQualityCode.HISTORICAL_ONLY_FIELD_IGNORED,
        message="ignored depth",
        source="synthetic",
    )
    service = HistoricalReplayService(runtime)

    await service.start(
        FakeSource((warning, _trade(1), _trade(2))),
        ReplayConfig(
            paths=(Path("synthetic.parquet"),), requested_symbol="NQ.c.0", schema="trades"
        ),
    )
    await _wait_terminal(service)

    status = service.status()
    assert status.state == ReplayState.COMPLETED
    assert status.events_processed == 2
    assert status.warnings_recorded == 1
    assert len(runtime.snapshot().recent_closed_bars) == 1


@pytest.mark.asyncio
async def test_replay_max_events_bounds_warning_only_processing() -> None:
    runtime = _runtime()
    warnings = tuple(
        DataQualityWarning(
            code=DataQualityCode.INVALID_TIMESTAMP,
            message=f"invalid timestamp row {index}",
            source="synthetic",
        )
        for index in range(50)
    )
    source = FakeSource(warnings)
    service = HistoricalReplayService(runtime, update_queue_depth=100)

    await service.start(
        source,
        ReplayConfig(
            paths=(Path("synthetic.parquet"),),
            requested_symbol="NQ.c.0",
            schema="trades",
            max_events=3,
        ),
    )
    await _wait_terminal(service)

    status = service.status()
    assert status.state == ReplayState.COMPLETED
    assert status.events_processed == 0
    assert status.warnings_recorded == 3
    assert source.items_yielded == 3
    assert len(runtime.snapshot().warnings) == 3


@pytest.mark.asyncio
async def test_replay_max_events_counts_warnings_and_market_events_together() -> None:
    runtime = _runtime()
    warnings = tuple(
        DataQualityWarning(
            code=DataQualityCode.INVALID_TIMESTAMP,
            message=f"invalid timestamp row {index}",
            source="synthetic",
        )
        for index in range(2)
    )
    source = FakeSource((*warnings, _trade(1), _trade(2)))
    service = HistoricalReplayService(runtime, update_queue_depth=100)

    await service.start(
        source,
        ReplayConfig(
            paths=(Path("synthetic.parquet"),),
            requested_symbol="NQ.c.0",
            schema="trades",
            max_events=3,
        ),
    )
    await _wait_terminal(service)

    status = service.status()
    assert status.state == ReplayState.COMPLETED
    # Intentional DoS guard: max_events caps total replay items, so warnings
    # consume part of the budget and reduce market events processed under it.
    assert status.events_processed == 1
    assert status.warnings_recorded == 2
    assert source.items_yielded == 3
    assert len(runtime.snapshot().warnings) == 2


@pytest.mark.asyncio
async def test_synthetic_replay_source_drives_bars_levels_touches_and_observations() -> None:
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0", tick_timeframes=(147,), observation_duration_seconds=300
    )
    service = HistoricalReplayService(runtime, update_queue_depth=5_000)
    broadcaster = WebSocketBroadcaster(runtime)

    await service.start(
        SyntheticNqDemoSource(),
        ReplayConfig(
            paths=(Path("synthetic:nq-demo"),),
            requested_symbol="NQ.c.0",
            schema="trades",
            source_id="synthetic:nq-demo",
            source_label="Synthetic NQ demo (bars, levels, touches)",
        ),
    )
    await _wait_terminal(service)

    seen = {
        message["type"]
        for update in list(service.updates._queue)
        for message in _json_messages(broadcaster, update)
    }
    snapshot = runtime.snapshot()

    assert service.status().state == ReplayState.COMPLETED
    assert service.status().events_processed > 300
    assert {"market.bar.closed", "levels.updated", "touch.detected", "observation.updated"} <= seen
    assert snapshot.recent_closed_bars
    assert snapshot.display_levels


@pytest.mark.asyncio
async def test_replay_state_transitions_pause_resume_stop_and_complete_are_deterministic() -> None:
    runtime = _runtime()
    service = HistoricalReplayService(runtime)

    await service.start(
        FakeSource(tuple(_trade(i) for i in range(20))),
        ReplayConfig(
            paths=(Path("synthetic.parquet"),), requested_symbol="NQ.c.0", schema="trades"
        ),
    )
    assert service.status().state in {ReplayState.READY, ReplayState.RUNNING}
    while service.status().events_processed < 1:
        await asyncio.sleep(0)

    await service.pause()
    paused_count = service.status().events_processed
    await asyncio.sleep(0)
    assert service.status().state == ReplayState.PAUSED
    assert service.status().events_processed == paused_count

    await service.resume()
    assert service.status().state == ReplayState.RUNNING
    await service.stop()
    assert service.status().state in {ReplayState.CANCELLED, ReplayState.STOPPED}
    assert service.status().events_processed >= paused_count


@pytest.mark.asyncio
async def test_replay_pause_during_throttled_delay_blocks_next_event_until_resume() -> None:
    runtime = _runtime()
    source = FakeSource((_trade(0), _trade(10, price_ticks=68_001)))
    service = HistoricalReplayService(runtime)

    await service.start(
        source,
        ReplayConfig(
            paths=(Path("synthetic.parquet"),),
            requested_symbol="NQ.c.0",
            schema="trades",
            speed=1,
        ),
    )
    while service.status().events_processed < 1 or source.items_yielded < 2:
        await asyncio.sleep(0)

    await service.pause()
    paused_count = service.status().events_processed
    await asyncio.sleep(0.3)

    assert service.status().state == ReplayState.PAUSED
    assert service.status().events_processed == paused_count == 1

    await service.resume()
    await _wait_terminal(service)
    assert service.status().state == ReplayState.COMPLETED
    assert service.status().events_processed == 2


@pytest.mark.asyncio
async def test_replay_stop_during_throttled_delay_does_not_process_fetched_event() -> None:
    runtime = _runtime()
    source = FakeSource((_trade(0), _trade(10, price_ticks=68_001)))
    service = HistoricalReplayService(runtime)

    await service.start(
        source,
        ReplayConfig(
            paths=(Path("synthetic.parquet"),),
            requested_symbol="NQ.c.0",
            schema="trades",
            speed=1,
        ),
    )
    while service.status().events_processed < 1 or source.items_yielded < 2:
        await asyncio.sleep(0)

    await service.stop()
    await asyncio.sleep(0)

    assert service.status().state in {ReplayState.CANCELLED, ReplayState.STOPPED}
    assert source.items_yielded == 2
    assert service.status().events_processed == 1
    assert len(runtime.snapshot().recent_closed_bars) == 0


@pytest.mark.asyncio
async def test_replay_warns_and_skips_out_of_order_market_events() -> None:
    runtime = _runtime()
    service = HistoricalReplayService(runtime)

    await service.start(
        FakeSource((_trade(10), _trade(0), _trade(11, price_ticks=68_001))),
        ReplayConfig(
            paths=(Path("synthetic.parquet"),), requested_symbol="NQ.c.0", schema="trades"
        ),
    )
    await _wait_terminal(service)

    status = service.status()
    assert status.state == ReplayState.COMPLETED
    assert status.events_processed == 2
    assert status.warnings_recorded == 1
    assert len(runtime.snapshot().recent_closed_bars) == 1
    assert runtime.snapshot().warnings[-1].code == DataQualityCode.TIMESTAMP_REGRESSION


@pytest.mark.asyncio
async def test_replay_failed_source_records_safe_error_status() -> None:
    runtime = _runtime()
    service = HistoricalReplayService(runtime)

    await service.start(
        FailingSource(),
        ReplayConfig(paths=(Path("safe-id.parquet"),), requested_symbol="NQ.c.0", schema="trades"),
    )
    await _wait_terminal(service)

    payload = service.status()
    text = _safe_text(payload)
    assert payload.state == ReplayState.FAILED
    assert payload.last_error == "RuntimeError"
    assert "c:\\users" not in text
    assert "hunter2" not in text


@pytest.mark.asyncio
async def test_replay_completion_updates_feed_status_and_emits_delta() -> None:
    runtime = _runtime()
    service = HistoricalReplayService(runtime)

    await service.start(
        FakeSource((_trade(1), _trade(2))),
        ReplayConfig(paths=(Path("safe-id.parquet"),), requested_symbol="NQ.c.0", schema="trades"),
    )
    await _wait_terminal(service)

    status = service.status()
    snapshot = runtime.snapshot()
    updates = list(service.updates._queue)

    assert status.state == ReplayState.COMPLETED
    assert snapshot.feed_status.state.value == "disconnected"
    assert snapshot.feed_status.mode == "replay"
    assert snapshot.feed_status.last_message == "historical replay completed"
    assert updates[-1].feed_status is not None
    assert updates[-1].feed_status.last_message == "historical replay completed"


@pytest.mark.asyncio
async def test_replay_failure_emits_safe_feed_status_and_logs_safe_context(caplog) -> None:
    runtime = _runtime()
    service = HistoricalReplayService(runtime)

    with caplog.at_level("ERROR", logger="trade_lab.services.replay"):
        await service.start(
            FailingSource(),
            ReplayConfig(
                paths=(Path("safe-id.parquet"),), requested_symbol="NQ.c.0", schema="trades"
            ),
        )
        await _wait_terminal(service)

    status = service.status()
    snapshot = runtime.snapshot()
    updates = list(service.updates._queue)
    log_text = caplog.text.lower()

    assert status.state == ReplayState.FAILED
    assert status.last_error == "RuntimeError"
    assert snapshot.feed_status.state.value == "disconnected"
    assert snapshot.feed_status.last_message == "historical replay failed"
    assert updates[-1].feed_status is not None
    assert updates[-1].feed_status.last_message == "historical replay failed"
    assert "runtimeerror" in log_text
    assert "trades" in log_text
    assert "nq.c.0" in log_text
    assert "c:\\users" not in log_text
    assert "hunter2" not in log_text


@pytest.mark.asyncio
async def test_second_replay_on_same_service_starts_from_clean_runtime_state() -> None:
    runtime = _runtime()
    service = HistoricalReplayService(runtime)
    config = ReplayConfig(
        paths=(Path("safe-id.parquet"),), requested_symbol="NQ.c.0", schema="trades"
    )

    await service.start(FakeSource(tuple(_trade(i) for i in range(4))), config)
    await _wait_terminal(service)

    await service.start(FakeSource((_trade(10, price_ticks=68_100),)), config)
    await _wait_terminal(service)
    second_snapshot = runtime.snapshot()

    fresh_runtime = _runtime()
    fresh_service = HistoricalReplayService(fresh_runtime)
    await fresh_service.start(FakeSource((_trade(10, price_ticks=68_100),)), config)
    await _wait_terminal(fresh_service)
    fresh_snapshot = fresh_runtime.snapshot()

    assert second_snapshot.current_bars == fresh_snapshot.current_bars
    assert second_snapshot.recent_closed_bars == fresh_snapshot.recent_closed_bars == ()
    assert second_snapshot.display_levels == fresh_snapshot.display_levels
    assert second_snapshot.active_observations == fresh_snapshot.active_observations == ()
    assert second_snapshot.warnings == fresh_snapshot.warnings == ()
