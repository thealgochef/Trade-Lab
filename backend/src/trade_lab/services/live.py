"""Operator-controlled live feed lifecycle.

Live and replay deliberately share ``ApplicationRuntime.process_market_event`` so
bars, levels, touches, observations, and WebSocket deltas cannot diverge. This
controller never auto-starts; an operator API call is required because live market
data uses paid credentials and should not surprise-connect during app startup.

W2 (D-P-06): on start the runtime resets, the prior trading day's PDH/PDL seed
loads through the same ``load_prior_day_summary`` path research and cold replay
use, and the feed replays the CURRENT trading day from 18:00 ET before going real
time (the adapter's intraday replay-start, or its Historical fallback). A feed
disconnect re-runs that exact start path, so a reconnect can never resume on a
wiped, context-free runtime. The Chicago display seed is retired — display warm-up
now comes from real engine bars produced by the warm-start replay.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import cast

from strategy_core.runtime import LiveRuntime as CoreLiveRuntime
from strategy_core.runtime import LiveState as CoreLiveState

from trade_lab.adapters.databento_historical import DatabentoHistoricalSource
from trade_lab.domain.data_quality import DataQualityWarning
from trade_lab.domain.events import MarketEvent
from trade_lab.domain.feed import FeedConnectionState, FeedStatus
from trade_lab.domain.prices import price_to_ticks
from trade_lab.domain.trading_day import (
    prior_trading_day,
    trading_day_bounds_utc,
    trading_day_for,
)
from trade_lab.ports.market_data import MarketDataFeed
from trade_lab.services.runtime import ApplicationRuntime, RuntimeUpdate, _safe_text

logger = logging.getLogger(__name__)


class LiveState(StrEnum):
    IDLE = "idle"
    CONNECTING = "connecting"
    RUNNING = "running"
    STOPPED = "stopped"
    DISCONNECTED = "disconnected"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class LiveConfig:
    requested_symbol: str
    dataset: str
    trade_schema: str
    quote_schema: str
    context_schemas: tuple[str, ...]
    api_key_configured: bool
    enabled: bool = False
    sdk_available: bool | None = None
    reset_runtime_on_start: bool = True
    secret_values: tuple[str, ...] = ()

    @property
    def schemas(self) -> tuple[str, ...]:
        return (self.trade_schema, self.quote_schema, *self.context_schemas)


@dataclass(frozen=True, slots=True)
class LiveStatus:
    state: LiveState
    requested_symbol: str
    dataset: str
    schemas: tuple[str, ...]
    api_key_configured: bool
    enabled: bool
    sdk_available: bool | None
    subscription_ready: bool
    events_processed: int
    last_event_ts_utc: datetime | None = None
    last_error: str | None = None
    started_at_utc: datetime | None = None
    stopped_at_utc: datetime | None = None
    #: W2 P1b: "warming" while the engine is digesting the trading day's replayed
    #: events (event ts < the start wall clock); "live" once real-time events flow.
    warm_start_state: str | None = None
    warm_start_events: int = 0


FeedFactory = Callable[[LiveConfig], MarketDataFeed]


class LiveMarketDataService:
    """Small live-feed state machine using only canonical events."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        config: LiveConfig,
        feed_factory: FeedFactory,
        *,
        on_update: Callable[[RuntimeUpdate], Awaitable[None]] | None = None,
        historical_source: DatabentoHistoricalSource | None = None,
        reconnect_delay_seconds: float = 1.0,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.runtime = runtime
        self.config = config
        self._feed_factory = feed_factory
        self._on_update = on_update
        self._historical_source = historical_source
        self._reconnect_delay_seconds = max(reconnect_delay_seconds, 0.01)
        self._now = now_provider or (lambda: datetime.now(UTC))
        self._state = LiveState.IDLE
        self._events_processed = 0
        self._last_error: str | None = None
        self._last_event_ts_utc: datetime | None = None
        self._started_at_utc: datetime | None = None
        self._stopped_at_utc: datetime | None = None
        self._task: asyncio.Task[None] | None = None
        self.strategy_core_live: CoreLiveRuntime | None = None
        self._feed: MarketDataFeed | None = None
        self._lock = asyncio.Lock()
        # W2 P1b warm-start marking: events older than the start wall clock are the
        # replayed trading day ("warming"); the first at/after it flips to "live".
        self._warm_anchor_utc: datetime | None = None
        self._warm_start_state: str | None = None
        self._warm_start_events = 0
        # W2 P1d: a feed disconnect schedules this task to re-run the full start
        # path (reset + prior-day seed + trading-day replay), killing the old
        # wipe-without-recovery behavior.
        self._reconnect_task: asyncio.Task[None] | None = None

    @property
    def has_update_callback(self) -> bool:
        return self._on_update is not None

    def set_update_callback(
        self, callback: Callable[[RuntimeUpdate], Awaitable[None]] | None
    ) -> None:
        self._on_update = callback

    def status(self) -> LiveStatus:
        core_status = None if self.strategy_core_live is None else self.strategy_core_live.status()
        state = self._state
        events_processed = self._events_processed
        last_event_ts_utc = self._last_event_ts_utc
        last_error = self._last_error
        started_at_utc = self._started_at_utc
        stopped_at_utc = self._stopped_at_utc
        if core_status is not None and core_status.state != CoreLiveState.IDLE:
            state = _map_core_live_state(core_status.state, fallback=self._state)
            events_processed = core_status.events_processed
            last_event_ts_utc = core_status.last_event_ts_utc
            last_error = core_status.last_error
            started_at_utc = core_status.started_at_utc
            stopped_at_utc = core_status.stopped_at_utc
        return LiveStatus(
            state=state,
            requested_symbol=self.config.requested_symbol,
            dataset=self.config.dataset,
            schemas=self.config.schemas,
            api_key_configured=self.config.api_key_configured,
            enabled=self.config.enabled,
            sdk_available=self.config.sdk_available,
            subscription_ready=(
                self.config.enabled
                and self.config.api_key_configured
                and self.config.sdk_available is not False
            ),
            events_processed=events_processed,
            last_event_ts_utc=last_event_ts_utc,
            last_error=last_error,
            started_at_utc=started_at_utc,
            stopped_at_utc=stopped_at_utc,
            warm_start_state=self._warm_start_state,
            warm_start_events=self._warm_start_events,
        )

    async def start(self) -> None:
        async with self._lock:
            if self._state in {LiveState.CONNECTING, LiveState.RUNNING} or (
                self._task is not None and not self._task.done()
            ):
                raise RuntimeError("live feed is already running")
            if not self.config.enabled:
                raise RuntimeError("live Databento onboarding is disabled by configuration")
            if not self.config.api_key_configured:
                raise RuntimeError("Databento API key is not configured in backend environment")
            self._state = LiveState.CONNECTING
            self._last_error = None
            self._events_processed = 0
            self._last_event_ts_utc = None
            self._started_at_utc = datetime.now(UTC)
            self._stopped_at_utc = None
            self.strategy_core_live = None
            if self.config.reset_runtime_on_start:
                await self._emit(
                    self.runtime.reset(
                        requested_symbol=self.config.requested_symbol,
                        feed_message="runtime reset for live market data",
                        reset_reason="live_reset",
                    )
                )
            # W2 P1c: the prior trading day's PDH/PDL through the SAME seed path
            # research and cold replay use — before any event reaches the engine.
            await self._load_prior_day_summary()
            self._warm_anchor_utc = self._now()
            self._warm_start_state = "warming"
            self._warm_start_events = 0
            await self._emit_status(FeedConnectionState.CONNECTING, "live feed connecting")
            feed: MarketDataFeed | None = None
            try:
                feed = self._feed_factory(self.config)
                self._feed = feed
                core_live = CoreLiveRuntime(
                    feed,
                    process_item=self._process_live_item,
                    on_update=self._emit,
                    is_warning=lambda item: isinstance(item, DataQualityWarning),
                    is_event=lambda item: not isinstance(item, (DataQualityWarning, FeedStatus)),
                    event_timestamp=lambda item: getattr(item, "event_ts_utc", None),
                )
                self.strategy_core_live = core_live
                await core_live.start()
            except Exception as exc:
                self._state = LiveState.FAILED
                self._last_error = type(exc).__name__
                if feed is not None:
                    with suppress(Exception):
                        await feed.stop()
                self._feed = None
                await self._emit_status(
                    FeedConnectionState.DISCONNECTED, "live feed failed to start"
                )
                message = _redact_configured_secrets(str(exc), self.config.secret_values)
                if (
                    isinstance(exc, NotImplementedError)
                    or type(exc).__name__ == "DatabentoUnavailableError"
                ):
                    raise RuntimeError(message) from exc
                logger.error(
                    "live feed failed to start: exception_type=%s message=%s",
                    type(exc).__name__,
                    message,
                )
                raise RuntimeError(
                    "live feed failed to start; check Databento SDK/configuration"
                ) from exc
            self._state = LiveState.RUNNING
            await self._emit_status(FeedConnectionState.CONNECTED, "live feed running")
            self._task = asyncio.create_task(self._wait_strategy_core_live())

    async def stop(self) -> None:
        async with self._lock:
            if self._reconnect_task is not None and not self._reconnect_task.done():
                self._reconnect_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._reconnect_task
            self._reconnect_task = None
            core = self.strategy_core_live
            if core is not None:
                await core.stop()
            if self._task is not None and not self._task.done():
                with suppress(asyncio.CancelledError):
                    await self._task
            if self._feed is not None and core is None:
                await self._feed.stop()
            self._feed = None
            self._state = LiveState.STOPPED
            self._stopped_at_utc = datetime.now(UTC)
            # W2 P2a (F10): finalize open setups whose cutoff has already passed;
            # the flushed drops ride the normal drop -> DroppedPrediction surface.
            await self._emit(self.runtime.flush_resolver(datetime.now(UTC)))
            await self._emit_status(FeedConnectionState.DISCONNECTED, "live feed stopped")

    async def _load_prior_day_summary(self) -> None:
        """W2 P1c: seed PDH/PDL from the prior trading day's ohlcv-1h summary."""

        source = self._historical_source
        if source is None or not source.available:
            logger.warning(
                "prior-day summary skipped: Databento Historical access is not "
                "configured (PDH/PDL emit only after the first in-stream day roll)"
            )
            return
        try:
            day = prior_trading_day(trading_day_for(self._now()))
            start, end = trading_day_bounds_utc(day)
            frame = await asyncio.to_thread(lambda: source.ohlcv_frame(start=start, end=end))
            if frame is None or len(frame) == 0:
                logger.warning("prior-day summary fetch returned no bars for %s", day)
                return
            high_ticks = price_to_ticks(str(float(frame["high"].max())))
            low_ticks = price_to_ticks(str(float(frame["low"].min())))
            self.runtime.levels.load_prior_day_summary(day, high_ticks, low_ticks)
            logger.info("prior-day summary loaded for %s", day)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "prior-day summary fetch failed (exception_type=%s); PDH/PDL emit "
                "only after the first in-stream day roll",
                type(exc).__name__,
            )

    def _process_live_item(self, item: object) -> RuntimeUpdate:
        if isinstance(item, FeedStatus):
            return self.runtime.set_feed_status(item)
        if isinstance(item, DataQualityWarning):
            return self.runtime.record_warning(item)
        self._mark_warm_start(item)
        return self.runtime.process_market_event(cast(MarketEvent, item))

    def _mark_warm_start(self, item: object) -> None:
        anchor = self._warm_anchor_utc
        ts = getattr(item, "event_ts_utc", None)
        if anchor is None or ts is None:
            return
        if ts < anchor:
            self._warm_start_events += 1
        elif self._warm_start_state != "live":
            self._warm_start_state = "live"

    async def _wait_strategy_core_live(self) -> None:
        core = self.strategy_core_live
        if core is None:
            return
        await core.wait_finished()
        status = core.status()
        self._events_processed = status.events_processed
        self._last_event_ts_utc = status.last_event_ts_utc
        self._last_error = status.last_error
        self._started_at_utc = status.started_at_utc or self._started_at_utc
        self._stopped_at_utc = status.stopped_at_utc
        if status.state == CoreLiveState.DISCONNECTED and self._state == LiveState.RUNNING:
            self._state = LiveState.DISCONNECTED
            await self._emit_status(FeedConnectionState.DISCONNECTED, "live feed disconnected")
            # W2 P1d (D-P-06): reconnect = the same warm-start path. start() resets
            # the runtime, reloads the prior-day seed, and replays the trading day
            # from 18:00 ET before resuming real time.
            self._schedule_reconnect()
        elif status.state == CoreLiveState.FAILED:
            self._state = LiveState.FAILED
            # audit #N6: the migration dropped the old failure logger.error call, so a
            # mid-stream live failure became invisible. Restore observable, secret-safe
            # logging: status.last_message is already safe_text-redacted by Strategy-Core,
            # and we additionally strip configured secret VALUES via the still-present
            # _redact_configured_secrets helper so credentials are never leaked.
            logger.error(
                "live feed failed: exception_type=%s message=%s",
                status.last_error,
                _redact_configured_secrets(status.last_message, self.config.secret_values),
            )
            await self._emit_status(FeedConnectionState.DISCONNECTED, "live feed failed")

    def _schedule_reconnect(self) -> None:
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.create_task(self._reconnect_after_disconnect())

    async def _reconnect_after_disconnect(self) -> None:
        await asyncio.sleep(self._reconnect_delay_seconds)
        if self._state != LiveState.DISCONNECTED:
            return
        logger.info("live feed reconnecting from the trading-day start (D-P-06)")
        try:
            await self.start()
        except Exception as exc:
            logger.error(
                "live auto-reconnect failed: exception_type=%s message=%s",
                type(exc).__name__,
                _redact_configured_secrets(str(exc), self.config.secret_values),
            )

    async def _emit_status(self, state: FeedConnectionState, message: str) -> None:
        await self._emit(
            self.runtime.set_feed_status(
                FeedStatus(
                    state=state,
                    mode="live",
                    requested_symbol=self.config.requested_symbol,
                    dataset=self.config.dataset,
                    schema=self.config.trade_schema,
                    last_event_ts_utc=self._last_event_ts_utc,
                    last_message=message,
                    metadata={"schemas": list(self.config.schemas)},
                )
            )
        )

    async def _emit(self, update: RuntimeUpdate) -> None:
        if self._on_update is not None and update.has_deltas():
            await self._on_update(update)


def _map_core_live_state(state: CoreLiveState, *, fallback: LiveState) -> LiveState:
    return {
        CoreLiveState.IDLE: fallback,
        CoreLiveState.CONNECTING: LiveState.CONNECTING,
        CoreLiveState.RUNNING: LiveState.RUNNING,
        CoreLiveState.STOPPED: LiveState.STOPPED,
        CoreLiveState.DISCONNECTED: LiveState.DISCONNECTED,
        CoreLiveState.FAILED: LiveState.FAILED,
    }[state]


def _redact_configured_secrets(message: str, secrets: tuple[str, ...]) -> str:
    redacted = message
    for secret in secrets:
        if not secret:
            continue
        redacted = redacted.replace(secret, "<redacted>")
        for length in range(len(secret), 7, -1):
            redacted = redacted.replace(secret[:length], "<redacted>")
    return _safe_text(redacted)
