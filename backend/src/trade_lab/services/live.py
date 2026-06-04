"""Operator-controlled live feed lifecycle.

Live and replay deliberately share ``ApplicationRuntime.process_market_event`` so
bars, levels, touches, observations, and WebSocket deltas cannot diverge. This
controller never auto-starts; an operator API call is required because live market
data uses paid credentials and should not surprise-connect during app startup.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from trade_lab.domain.data_quality import (
    DataQualityCode,
    DataQualitySeverity,
    DataQualityWarning,
)
from trade_lab.domain.feed import FeedConnectionState, FeedStatus
from trade_lab.ports.market_data import MarketDataFeed
from trade_lab.services.runtime import ApplicationRuntime, RuntimeUpdate, _safe_text
from trade_lab.services.seed import HistoricalSeedService

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
        seed_service: HistoricalSeedService | None = None,
    ) -> None:
        self.runtime = runtime
        self.config = config
        self._feed_factory = feed_factory
        self._on_update = on_update
        self._seed_service = seed_service
        self._state = LiveState.IDLE
        self._events_processed = 0
        self._last_error: str | None = None
        self._last_event_ts_utc: datetime | None = None
        self._started_at_utc: datetime | None = None
        self._stopped_at_utc: datetime | None = None
        self._task: asyncio.Task[None] | None = None
        self._seed_task: asyncio.Task[None] | None = None
        self._feed: MarketDataFeed | None = None
        self._lock = asyncio.Lock()

    @property
    def has_update_callback(self) -> bool:
        return self._on_update is not None

    def set_update_callback(
        self, callback: Callable[[RuntimeUpdate], Awaitable[None]] | None
    ) -> None:
        self._on_update = callback

    def status(self) -> LiveStatus:
        return LiveStatus(
            state=self._state,
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
            events_processed=self._events_processed,
            last_event_ts_utc=self._last_event_ts_utc,
            last_error=self._last_error,
            started_at_utc=self._started_at_utc,
            stopped_at_utc=self._stopped_at_utc,
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
            if self.config.reset_runtime_on_start:
                await self._emit(
                    self.runtime.reset(
                        requested_symbol=self.config.requested_symbol,
                        feed_message="runtime reset for live market data",
                    )
                )
            await self._emit_status(FeedConnectionState.CONNECTING, "live feed connecting")
            feed: MarketDataFeed | None = None
            try:
                feed = self._feed_factory(self.config)
                self._feed = feed
                await feed.start()
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
            # Warm-up runs off the event loop and broadcasts when ready, so the live
            # connection is never blocked by the historical fetch. It is scheduled after
            # the runtime reset above so its bars are not cleared by the reset.
            if self._seed_service is not None and self._seed_service.enabled:
                self._seed_task = asyncio.create_task(self._seed_and_broadcast())
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        async with self._lock:
            if self._seed_task is not None and not self._seed_task.done():
                self._seed_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._seed_task
            if self._task is not None and not self._task.done():
                self._task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._task
            if self._feed is not None:
                await self._feed.stop()
            self._feed = None
            self._state = LiveState.STOPPED
            self._stopped_at_utc = datetime.now(UTC)
            await self._emit_status(FeedConnectionState.DISCONNECTED, "live feed stopped")

    async def _run(self) -> None:
        assert self._feed is not None
        try:
            async for item in self._feed.events():
                if isinstance(item, FeedStatus):
                    await self._emit(self.runtime.set_feed_status(item))
                    continue
                if isinstance(item, DataQualityWarning):
                    await self._emit(self.runtime.record_warning(item))
                    continue
                self._events_processed += 1
                self._last_event_ts_utc = item.event_ts_utc
                await self._emit(self.runtime.process_market_event(item))
                await asyncio.sleep(0)
            if self._state == LiveState.RUNNING:
                self._state = LiveState.DISCONNECTED
                await self._emit_status(FeedConnectionState.DISCONNECTED, "live feed disconnected")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._state = LiveState.FAILED
            self._last_error = type(exc).__name__
            logger.error(
                "live feed failed: exception_type=%s message=%s",
                type(exc).__name__,
                _redact_configured_secrets(str(exc), self.config.secret_values),
            )
            await self._emit_status(FeedConnectionState.DISCONNECTED, "live feed failed")

    async def _seed_and_broadcast(self) -> None:
        service = self._seed_service
        if service is None:
            return
        try:
            bars = await asyncio.to_thread(service.build_seed_bars)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "historical warm-up seeding task failed: exception_type=%s", type(exc).__name__
            )
            bars = ()
        if bars:
            logger.info("historical warm-up seeded %d bars", len(bars))
            await self._emit(self.runtime.seed_closed_bars(bars))
            return
        # Surface (rather than silently swallow) so an empty chart is explained in the UI.
        logger.warning("historical warm-up produced no seed bars")
        await self._emit(
            RuntimeUpdate(
                warnings=(
                    DataQualityWarning(
                        code=DataQualityCode.PROVIDER_ERROR,
                        message=(
                            "live warm-up history unavailable; chart starts without prior "
                            "sessions and will fill as live trades arrive"
                        ),
                        severity=DataQualitySeverity.WARNING,
                        source="seed",
                    ),
                )
            )
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


def _redact_configured_secrets(message: str, secrets: tuple[str, ...]) -> str:
    redacted = message
    for secret in secrets:
        if not secret:
            continue
        redacted = redacted.replace(secret, "<redacted>")
        for length in range(len(secret), 7, -1):
            redacted = redacted.replace(secret[:length], "<redacted>")
    return _safe_text(redacted)
