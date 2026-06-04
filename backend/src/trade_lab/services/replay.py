"""Historical replay controller using the same runtime path as live feeds."""

import asyncio
import logging
import queue
import time
from collections.abc import Awaitable, Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from trade_lab.domain.data_quality import DataQualityCode, DataQualitySeverity, DataQualityWarning
from trade_lab.domain.feed import FeedConnectionState, FeedStatus
from trade_lab.ports.market_data import HistoricalMarketDataSource
from trade_lab.services.runtime import (
    ApplicationRuntime,
    RuntimeUpdate,
    _safe_source,
    _safe_text,
)

logger = logging.getLogger(__name__)

# Coalesce per-trade replay deltas into at most one broadcast per interval so a fast
# (speed=0) replay cannot outrun the WebSocket and force the broadcaster to drop
# market.bar.closed messages (which shows up as gaps in the chart).
_REPLAY_FLUSH_INTERVAL_SECONDS = 0.05
_REPLAY_MAX_PENDING_UPDATES = 4000

# The historical scan (parquet decode + per-row normalization) is CPU-heavy and blocks in
# ~batch-sized chunks. Running it on a worker thread feeding a bounded queue keeps a batch
# boundary from freezing the event loop / WebSocket fan-out (which showed as replay
# "stop-and-go"). The buffer absorbs decode stalls so the consumer never starves.
_REPLAY_PRODUCER_QUEUE_DEPTH = 20_000
_REPLAY_CONSUMER_CHUNK = 1_000
_SCAN_DONE = object()


class ReplayState(StrEnum):
    IDLE = "idle"
    LOADING = "loading"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    paths: tuple[Path, ...]
    requested_symbol: str
    schema: str
    source_id: str | None = None
    source_label: str | None = None
    speed: float = 0.0
    max_events: int | None = None
    start_ts_utc: datetime | None = None
    end_ts_utc: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReplayStatus:
    state: ReplayState
    events_processed: int
    warnings_recorded: int
    last_event_ts_utc: datetime | None = None
    last_error: str | None = None
    requested_symbol: str | None = None
    schema: str | None = None
    source_id: str | None = None
    source_label: str | None = None
    started_at_utc: datetime | None = None
    completed_at_utc: datetime | None = None
    failed_at_utc: datetime | None = None


class HistoricalReplayService:
    """Small deterministic replay state machine for fake or parquet sources."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        *,
        update_queue_depth: int = 1000,
        on_update: Callable[[RuntimeUpdate], Awaitable[None]] | None = None,
    ) -> None:
        if update_queue_depth <= 0:
            raise ValueError("update_queue_depth must be positive")
        self.runtime = runtime
        self._state = ReplayState.IDLE
        self._events_processed = 0
        self._warnings_recorded = 0
        self._last_error: str | None = None
        self._last_event_ts_utc: datetime | None = None
        self._started_at_utc: datetime | None = None
        self._completed_at_utc: datetime | None = None
        self._failed_at_utc: datetime | None = None
        self._config: ReplayConfig | None = None
        self._task: asyncio.Task[None] | None = None
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._stop_requested = False
        self._on_update = on_update
        self._pending_updates: list[RuntimeUpdate] = []
        self._last_flush_monotonic = 0.0
        self.updates: asyncio.Queue[RuntimeUpdate] = asyncio.Queue(maxsize=update_queue_depth)

    @property
    def has_update_callback(self) -> bool:
        return self._on_update is not None

    def set_update_callback(
        self, callback: Callable[[RuntimeUpdate], Awaitable[None]] | None
    ) -> None:
        """Wire replay updates to an external broadcaster.

        App composition uses this for injected replay services so tests and custom
        deployments do not accidentally bypass WebSocket feed/runtime deltas.
        """

        self._on_update = callback

    def status(self) -> ReplayStatus:
        return ReplayStatus(
            state=self._state,
            events_processed=self._events_processed,
            warnings_recorded=self._warnings_recorded,
            last_event_ts_utc=self._last_event_ts_utc,
            last_error=self._last_error,
            requested_symbol=None if self._config is None else self._config.requested_symbol,
            schema=None if self._config is None else self._config.schema,
            source_id=None if self._config is None else self._config.source_id,
            source_label=None if self._config is None else self._config.source_label,
            started_at_utc=self._started_at_utc,
            completed_at_utc=self._completed_at_utc,
            failed_at_utc=self._failed_at_utc,
        )

    async def start(self, source: HistoricalMarketDataSource, config: ReplayConfig) -> None:
        if self._task is not None and not self._task.done():
            raise RuntimeError("replay is already running")
        self._config = config
        self._events_processed = 0
        self._warnings_recorded = 0
        self._last_error = None
        self._last_event_ts_utc = None
        self._started_at_utc = datetime.now(UTC)
        self._completed_at_utc = None
        self._failed_at_utc = None
        self._stop_requested = False
        self._pause_event.set()
        self._state = ReplayState.LOADING
        await self._emit(
            self.runtime.reset(
                requested_symbol=config.requested_symbol,
                feed_message="runtime reset for historical replay",
            )
        )
        await self._emit(
            self.runtime.set_feed_status(
                FeedStatus(
                    state=FeedConnectionState.REPLAYING,
                    mode="replay",
                    requested_symbol=config.requested_symbol,
                    schema=config.schema,
                    last_message="historical replay loading",
                    metadata={"source_id": config.source_id} if config.source_id else {},
                )
            )
        )
        self._state = ReplayState.READY
        self._task = asyncio.create_task(self._run(source, config))

    async def pause(self) -> None:
        if self._state == ReplayState.RUNNING:
            self._state = ReplayState.PAUSED
            self._pause_event.clear()
            await self._emit_feed_status("historical replay paused")

    async def resume(self) -> None:
        if self._state == ReplayState.PAUSED:
            self._state = ReplayState.RUNNING
            self._pause_event.set()
            await self._emit_feed_status("historical replay resumed")

    async def stop(self) -> None:
        self._stop_requested = True
        self._pause_event.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                self._state = ReplayState.CANCELLED
                await self._emit_terminal_feed_status("historical replay cancelled")
                return
        if self._state not in (ReplayState.COMPLETED, ReplayState.FAILED, ReplayState.CANCELLED):
            self._state = ReplayState.STOPPED
            self._completed_at_utc = datetime.now(UTC)
            await self._emit_terminal_feed_status("historical replay stopped")

    async def _run(self, source: HistoricalMarketDataSource, config: ReplayConfig) -> None:
        self._state = ReplayState.RUNNING
        self._pending_updates = []
        self._last_flush_monotonic = time.monotonic()
        raw_queue: queue.Queue = queue.Queue(maxsize=_REPLAY_PRODUCER_QUEUE_DEPTH)
        producer_error: list[BaseException] = []
        max_events = config.max_events

        def _produce() -> None:
            # Worker thread: pull canonical events from the blocking scan into the queue so
            # the event loop never waits on parquet decode. Checks max_events BEFORE fetching
            # so the source is never over-read (the max_events DoS guard), and honors stop.
            produced = 0
            try:
                scan = iter(
                    source.scan(
                        config.paths,
                        requested_symbol=config.requested_symbol,
                        schema=config.schema,
                        start_ts_utc=config.start_ts_utc,
                        end_ts_utc=config.end_ts_utc,
                    )
                )
                while not self._stop_requested:
                    if max_events is not None and produced >= max_events:
                        break
                    try:
                        produced_item = next(scan)
                    except StopIteration:
                        break
                    produced += 1
                    while not self._stop_requested:
                        try:
                            raw_queue.put(produced_item, timeout=0.1)
                            break
                        except queue.Full:
                            continue
            except Exception as exc:
                producer_error.append(exc)
            finally:
                while not self._stop_requested:
                    try:
                        raw_queue.put(_SCAN_DONE, timeout=0.1)
                        break
                    except queue.Full:
                        continue
                else:
                    with suppress(queue.Full):
                        raw_queue.put_nowait(_SCAN_DONE)

        def _drain() -> list[object]:
            chunk: list[object] = [raw_queue.get()]
            while len(chunk) < _REPLAY_CONSUMER_CHUNK:
                try:
                    chunk.append(raw_queue.get_nowait())
                except queue.Empty:
                    break
            return chunk

        producer = asyncio.create_task(asyncio.to_thread(_produce))
        buffer: list[object] = []
        buffer_index = 0
        try:
            previous_event_ts: datetime | None = None
            emitted_items_processed = 0
            while True:
                if self._stop_requested:
                    await self._flush_pending()
                    self._state = ReplayState.STOPPED
                    await self._emit_terminal_feed_status("historical replay stopped")
                    return
                if config.max_events is not None and emitted_items_processed >= config.max_events:
                    break
                await self._pause_event.wait()
                if buffer_index >= len(buffer):
                    buffer = await asyncio.to_thread(_drain)
                    buffer_index = 0
                item = buffer[buffer_index]
                buffer_index += 1
                if item is _SCAN_DONE:
                    if producer_error:
                        raise producer_error[0]
                    break
                if isinstance(item, DataQualityWarning):
                    self._warnings_recorded += 1
                    emitted_items_processed += 1
                    self._accumulate(self.runtime.record_warning(item))
                    await self._maybe_flush()
                    continue
                if previous_event_ts is not None and config.speed > 0:
                    elapsed = max((item.event_ts_utc - previous_event_ts).total_seconds(), 0)
                    delay = elapsed / config.speed
                    await asyncio.sleep(min(delay, 0.25))
                await self._pause_event.wait()
                if self._stop_requested:
                    await self._flush_pending()
                    self._state = ReplayState.STOPPED
                    await self._emit_terminal_feed_status("historical replay stopped")
                    return
                if previous_event_ts is not None and item.event_ts_utc < previous_event_ts:
                    self._warnings_recorded += 1
                    emitted_items_processed += 1
                    self._accumulate(
                        self.runtime.record_warning(
                            DataQualityWarning(
                                code=DataQualityCode.TIMESTAMP_REGRESSION,
                                message="historical replay event timestamp regressed",
                                severity=DataQualitySeverity.WARNING,
                                source="historical-replay",
                                event_ts_utc=item.event_ts_utc,
                            )
                        )
                    )
                    await self._maybe_flush()
                    continue
                self._events_processed += 1
                emitted_items_processed += 1
                self._last_event_ts_utc = item.event_ts_utc
                previous_event_ts = item.event_ts_utc
                self._accumulate(self.runtime.process_market_event(item))
                await self._maybe_flush()
                await asyncio.sleep(0)
            await self._flush_pending()
            self._state = ReplayState.COMPLETED
            self._completed_at_utc = datetime.now(UTC)
            await self._emit_terminal_feed_status("historical replay completed")
        except asyncio.CancelledError:
            self._state = ReplayState.CANCELLED
            self._completed_at_utc = datetime.now(UTC)
            await self._emit_terminal_feed_status("historical replay cancelled")
            raise
        except Exception as exc:
            self._last_error = type(exc).__name__
            self._state = ReplayState.FAILED
            self._failed_at_utc = datetime.now(UTC)
            sanitized_message = _safe_text(str(exc))
            logger.error(
                "historical replay failed: exception_type=%s message=%s "
                "schema=%s symbol=%s events=%s state=%s",
                type(exc).__name__,
                sanitized_message,
                _safe_text(config.schema),
                _safe_text(config.requested_symbol),
                self._events_processed,
                self._state.value,
                extra={
                    "exception_type": type(exc).__name__,
                    "sanitized_message": sanitized_message,
                    "schema": _safe_text(config.schema),
                    "requested_symbol": _safe_text(config.requested_symbol),
                    "source_labels": [_safe_source(str(path)) for path in config.paths],
                    "events_processed": self._events_processed,
                    "replay_state": self._state.value,
                },
            )
            await self._emit_terminal_feed_status("historical replay failed")
        finally:
            # Signal the scan thread to stop and let it unwind (it exits within ~0.1s).
            self._stop_requested = True
            with suppress(BaseException):
                await producer

    async def _emit_feed_status(self, message: str) -> None:
        await self._flush_pending()
        current = self.runtime.feed_status
        await self._emit(
            self.runtime.set_feed_status(
                FeedStatus(
                    state=FeedConnectionState.REPLAYING,
                    mode="replay",
                    requested_symbol=current.requested_symbol,
                    raw_symbol=current.raw_symbol,
                    dataset=current.dataset,
                    schema=current.schema,
                    last_event_ts_utc=self._last_event_ts_utc or current.last_event_ts_utc,
                    last_message=message,
                    metadata=dict(current.metadata),
                )
            )
        )

    async def _emit_terminal_feed_status(self, message: str) -> None:
        config = self._config
        current = self.runtime.feed_status
        await self._emit(
            self.runtime.set_feed_status(
                FeedStatus(
                    state=FeedConnectionState.DISCONNECTED,
                    mode="replay",
                    requested_symbol=current.requested_symbol
                    or (None if config is None else config.requested_symbol),
                    raw_symbol=current.raw_symbol,
                    dataset=current.dataset,
                    schema=current.schema or (None if config is None else config.schema),
                    last_event_ts_utc=self._last_event_ts_utc or current.last_event_ts_utc,
                    last_message=message,
                    metadata=dict(current.metadata),
                )
            )
        )

    def _accumulate(self, update: RuntimeUpdate) -> None:
        if update.has_deltas():
            self._pending_updates.append(update)

    async def _maybe_flush(self) -> None:
        if not self._pending_updates:
            return
        elapsed = time.monotonic() - self._last_flush_monotonic
        if (
            elapsed >= _REPLAY_FLUSH_INTERVAL_SECONDS
            or len(self._pending_updates) >= _REPLAY_MAX_PENDING_UPDATES
        ):
            await self._flush_pending()

    async def _flush_pending(self) -> None:
        if not self._pending_updates:
            return
        merged = _coalesce_replay_updates(self._pending_updates)
        self._pending_updates = []
        self._last_flush_monotonic = time.monotonic()
        await self._emit(merged)

    async def _emit(self, update: RuntimeUpdate) -> None:
        if not update.has_deltas():
            return
        if self.updates.full():
            self.updates.get_nowait()
        self.updates.put_nowait(update)
        if self._on_update is not None:
            await self._on_update(update)


def _coalesce_replay_updates(updates: list[RuntimeUpdate]) -> RuntimeUpdate:
    """Merge buffered per-trade deltas into one delta for a single broadcast.

    Snapshot-style fields (feed_status, current_bars, display_levels) keep the latest
    non-empty value; event-style fields (closed_bars, touches, observations, warnings)
    are concatenated so no completed bar is ever lost. Mirrors how the frontend
    consumes each message type.
    """

    if len(updates) == 1:
        return updates[0]
    merged = RuntimeUpdate()
    feed_status = merged.feed_status
    warnings = list(merged.warnings)
    current_bars = merged.current_bars
    closed_bars = list(merged.closed_bars)
    display_levels = merged.display_levels
    touches = list(merged.touches)
    observations = list(merged.observations)
    predictions = list(merged.predictions)
    for update in updates:
        if update.feed_status is not None:
            feed_status = update.feed_status
        if update.warnings:
            warnings.extend(update.warnings)
        if update.current_bars:
            current_bars = update.current_bars
        if update.closed_bars:
            closed_bars.extend(update.closed_bars)
        if update.display_levels:
            display_levels = update.display_levels
        if update.touches:
            touches.extend(update.touches)
        if update.observations:
            observations.extend(update.observations)
        if update.predictions:
            predictions.extend(update.predictions)
    return RuntimeUpdate(
        feed_status=feed_status,
        warnings=tuple(warnings),
        current_bars=current_bars,
        closed_bars=tuple(closed_bars),
        display_levels=display_levels,
        touches=tuple(touches),
        observations=tuple(observations),
        predictions=tuple(predictions),
    )


def resolve_replay_paths(data_root: Path, identifiers: Iterable[str]) -> tuple[Path, ...]:
    """Resolve safe relative replay identifiers under a configured data root."""

    root = data_root.resolve()
    paths: list[Path] = []
    for identifier in identifiers:
        candidate = Path(identifier)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("replay source identifiers must be safe relative paths")
        resolved = (root / candidate).resolve()
        if root != resolved and root not in resolved.parents:
            raise ValueError("replay source escapes configured data root")
        paths.append(resolved)
    return tuple(paths)
