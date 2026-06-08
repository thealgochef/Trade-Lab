"""Historical replay controller using the same runtime path as live feeds."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import cast

from strategy_core.runtime import ReplayConfig as CoreReplayConfig
from strategy_core.runtime import ReplayRuntime as CoreReplayRuntime
from strategy_core.runtime import ReplayState as CoreReplayState

from trade_lab.domain.data_quality import DataQualityCode, DataQualitySeverity, DataQualityWarning
from trade_lab.domain.events import MarketEvent
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

class _StrategyCoreHistoricalSourceAdapter:
    """Expose a Trade-Lab historical source as a Strategy-Core replay source."""

    def __init__(self, source: HistoricalMarketDataSource, config: "ReplayConfig") -> None:
        self._source = source
        self._config = config

    def events(self):
        yield from self._source.scan(
            self._config.paths,
            requested_symbol=self._config.requested_symbol,
            schema=self._config.schema,
            start_ts_utc=self._config.start_ts_utc,
            end_ts_utc=self._config.end_ts_utc,
        )


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
        self.strategy_core_replay: CoreReplayRuntime | None = None
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
        core_status = (
            None if self.strategy_core_replay is None else self.strategy_core_replay.status()
        )
        state = self._state
        if core_status is not None and core_status.state != CoreReplayState.IDLE:
            state = _map_core_replay_state(core_status.state, fallback=self._state)
        return ReplayStatus(
            state=state,
            events_processed=(
                self._events_processed if core_status is None else core_status.events_processed
            ),
            warnings_recorded=(
                self._warnings_recorded if core_status is None else core_status.warnings_recorded
            ),
            last_event_ts_utc=self._last_event_ts_utc
            if core_status is None
            else core_status.last_event_ts_utc,
            last_error=self._last_error if core_status is None else core_status.last_error,
            requested_symbol=None if self._config is None else self._config.requested_symbol,
            schema=None if self._config is None else self._config.schema,
            source_id=None if self._config is None else self._config.source_id,
            source_label=None if self._config is None else self._config.source_label,
            started_at_utc=self._started_at_utc
            if core_status is None
            else core_status.started_at_utc,
            completed_at_utc=self._completed_at_utc
            if core_status is None
            else core_status.completed_at_utc,
            failed_at_utc=self._failed_at_utc if core_status is None else core_status.failed_at_utc,
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
        self._pending_updates = []
        self._last_flush_monotonic = time.monotonic()
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
        adapter = _StrategyCoreHistoricalSourceAdapter(source, config)
        self.strategy_core_replay = CoreReplayRuntime(
            None,
            adapter,
            process_item=self._process_replay_item,
            on_update=self._on_strategy_core_replay_update,
            is_warning=lambda item: isinstance(item, DataQualityWarning),
            event_timestamp=lambda item: getattr(item, "event_ts_utc", None),
            on_timestamp_regression=self._timestamp_regression_update,
        )
        self._state = ReplayState.READY
        self._task = asyncio.create_task(self._run_strategy_core_replay(config))

    async def pause(self) -> None:
        core = self.strategy_core_replay
        if self._state == ReplayState.RUNNING and core is not None:
            await core.pause()
            self._state = ReplayState.PAUSED
            await self._emit_feed_status("historical replay paused")

    async def resume(self) -> None:
        core = self.strategy_core_replay
        if self._state == ReplayState.PAUSED and core is not None:
            await core.resume()
            self._state = ReplayState.RUNNING
            await self._emit_feed_status("historical replay resumed")

    async def stop(self) -> None:
        self._stop_requested = True
        self._pause_event.set()
        core = self.strategy_core_replay
        if core is not None:
            await core.stop()
        if self._task is not None and not self._task.done():
            await self._task
        if self._state not in (
            ReplayState.COMPLETED,
            ReplayState.FAILED,
            ReplayState.CANCELLED,
            ReplayState.STOPPED,
        ):
            self._state = ReplayState.STOPPED
            self._completed_at_utc = datetime.now(UTC)
            await self._emit_terminal_feed_status("historical replay stopped")

    def _process_replay_item(self, item: object) -> RuntimeUpdate:
        if isinstance(item, DataQualityWarning):
            return self.runtime.record_warning(item)
        return self.runtime.process_market_event(cast(MarketEvent, item))

    async def _on_strategy_core_replay_update(self, update: RuntimeUpdate) -> None:
        self._accumulate(update)
        await self._maybe_flush()

    def _timestamp_regression_update(
        self, item: object, _previous_ts: datetime
    ) -> RuntimeUpdate:
        event_ts = getattr(item, "event_ts_utc", None)
        return self.runtime.record_warning(
            DataQualityWarning(
                code=DataQualityCode.TIMESTAMP_REGRESSION,
                message="historical replay event timestamp regressed",
                severity=DataQualitySeverity.WARNING,
                source="historical-replay",
                event_ts_utc=event_ts if isinstance(event_ts, datetime) else None,
            )
        )

    async def _run_strategy_core_replay(self, config: ReplayConfig) -> None:
        core = self.strategy_core_replay
        if core is None:
            raise RuntimeError("Strategy-Core replay runtime is not configured")
        self._state = ReplayState.RUNNING
        await core.start(CoreReplayConfig(speed=config.speed, max_events=config.max_events))
        await self._flush_pending()
        status = core.status()
        self._events_processed = status.events_processed
        self._warnings_recorded = status.warnings_recorded
        self._last_event_ts_utc = status.last_event_ts_utc
        self._last_error = status.last_error
        self._started_at_utc = status.started_at_utc or self._started_at_utc
        self._completed_at_utc = status.completed_at_utc
        self._failed_at_utc = status.failed_at_utc
        if status.state == CoreReplayState.COMPLETED:
            self._state = ReplayState.COMPLETED
            await self._emit_terminal_feed_status("historical replay completed")
            return
        if status.state == CoreReplayState.STOPPED:
            self._state = ReplayState.STOPPED
            self._completed_at_utc = datetime.now(UTC)
            await self._emit_terminal_feed_status("historical replay stopped")
            return
        if status.state == CoreReplayState.FAILED:
            self._state = ReplayState.FAILED
            config = self._config or config
            logger.error(
                "historical replay failed: exception_type=%s message=%s "
                "schema=%s symbol=%s events=%s state=%s",
                status.last_error,
                _safe_text(status.last_message),
                _safe_text(config.schema),
                _safe_text(config.requested_symbol),
                self._events_processed,
                self._state.value,
                extra={
                    "exception_type": status.last_error,
                    "sanitized_message": _safe_text(status.last_message),
                    "schema": _safe_text(config.schema),
                    "requested_symbol": _safe_text(config.requested_symbol),
                    "source_labels": [_safe_source(str(path)) for path in config.paths],
                    "events_processed": self._events_processed,
                    "replay_state": self._state.value,
                },
            )
            await self._emit_terminal_feed_status("historical replay failed")
            return

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


def _map_core_replay_state(state: CoreReplayState, *, fallback: ReplayState) -> ReplayState:
    return {
        CoreReplayState.IDLE: fallback,
        CoreReplayState.RUNNING: ReplayState.RUNNING,
        CoreReplayState.PAUSED: ReplayState.PAUSED,
        CoreReplayState.COMPLETED: ReplayState.COMPLETED,
        CoreReplayState.FAILED: ReplayState.FAILED,
        CoreReplayState.STOPPED: ReplayState.STOPPED,
    }[state]


def _coalesce_replay_updates(updates: list[RuntimeUpdate]) -> RuntimeUpdate:
    """Merge buffered per-trade deltas into one delta for a single broadcast.

    Snapshot-style fields (feed_status, current_bars, display_levels) keep the latest
    non-empty value; event-style fields (closed_bars, touches, observations, warnings,
    predictions, outcomes) are concatenated so no completed bar or resolved outcome is
    ever lost. Mirrors how the frontend consumes each message type.
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
    # audit #N4: 'outcomes' is an event-style delta (prediction.resolved); accumulate
    # it like 'predictions' so coalesced replay never drops resolved-outcome broadcasts.
    outcomes = list(merged.outcomes)
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
        if update.outcomes:
            outcomes.extend(update.outcomes)
    return RuntimeUpdate(
        feed_status=feed_status,
        warnings=tuple(warnings),
        current_bars=current_bars,
        closed_bars=tuple(closed_bars),
        display_levels=display_levels,
        touches=tuple(touches),
        observations=tuple(observations),
        predictions=tuple(predictions),
        outcomes=tuple(outcomes),
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
