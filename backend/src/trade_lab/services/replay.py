"""Historical replay controller using the same runtime path as live feeds."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
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
        try:
            previous_event_ts: datetime | None = None
            emitted_items_processed = 0
            items = iter(source.scan(
                config.paths,
                requested_symbol=config.requested_symbol,
                schema=config.schema,
                start_ts_utc=config.start_ts_utc,
                end_ts_utc=config.end_ts_utc,
            ))
            while True:
                if self._stop_requested:
                    self._state = ReplayState.STOPPED
                    await self._emit_terminal_feed_status("historical replay stopped")
                    return
                if config.max_events is not None and emitted_items_processed >= config.max_events:
                    break
                await self._pause_event.wait()
                try:
                    item = next(items)
                except StopIteration:
                    break
                if isinstance(item, DataQualityWarning):
                    self._warnings_recorded += 1
                    emitted_items_processed += 1
                    await self._emit(self.runtime.record_warning(item))
                    continue
                if previous_event_ts is not None and config.speed > 0:
                    elapsed = max((item.event_ts_utc - previous_event_ts).total_seconds(), 0)
                    delay = elapsed / config.speed
                    await asyncio.sleep(min(delay, 0.25))
                await self._pause_event.wait()
                if self._stop_requested:
                    self._state = ReplayState.STOPPED
                    await self._emit_terminal_feed_status("historical replay stopped")
                    return
                if previous_event_ts is not None and item.event_ts_utc < previous_event_ts:
                    self._warnings_recorded += 1
                    emitted_items_processed += 1
                    await self._emit(
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
                    continue
                self._events_processed += 1
                emitted_items_processed += 1
                self._last_event_ts_utc = item.event_ts_utc
                previous_event_ts = item.event_ts_utc
                await self._emit(self.runtime.process_market_event(item))
                await asyncio.sleep(0)
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

    async def _emit_feed_status(self, message: str) -> None:
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

    async def _emit(self, update: RuntimeUpdate) -> None:
        if not update.has_deltas():
            return
        if self.updates.full():
            self.updates.get_nowait()
        self.updates.put_nowait(update)
        if self._on_update is not None:
            await self._on_update(update)


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
