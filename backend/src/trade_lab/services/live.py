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

#: Warm-start broadcast throttle (frontier-lag based). During an intraday warm-start
#: replay the engine processes the whole trading day; streaming every replayed bar
#: floods the browser (dropped frames -> chart gaps) and burns serialization on the
#: single-threaded consumer. While the processed frontier lags wall clock by more than
#: ``_WARMING_LAG_SECONDS`` the live service suppresses per-event deltas and emits a
#: throttled full snapshot for progress instead; once the frontier is within
#: ``_LIVE_LAG_SECONDS`` of now it flushes one snapshot and resumes per-event streaming.
#: The two thresholds give hysteresis so the mode does not flap at the boundary.
_LIVE_LAG_SECONDS = 5.0
_WARMING_LAG_SECONDS = 30.0
_WARM_SNAPSHOT_MIN_INTERVAL_SECONDS = 1.0

#: WARM-FIX P2 liveness watchdog default: if ZERO provider messages arrive within
#: this many seconds after the post-drain live subscribe (the drain's last item),
#: the feed is marked DEGRADED and the single-attempt D-P-06 reconnect runs. The
#: wedge this catches (WEDGE_CAPTURE.md): an authenticated-but-silent session that
#: neither the SDK's own monitor (blind before the first record) nor the status
#: surface (CONNECTED-by-construction, "warming" forever) will ever report.
_DEFAULT_WATCHDOG_SECONDS = 120.0


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
        on_snapshot: Callable[[], Awaitable[None]] | None = None,
        historical_source: DatabentoHistoricalSource | None = None,
        reconnect_delay_seconds: float = 1.0,
        now_provider: Callable[[], datetime] | None = None,
        throttle_warm_start: bool = False,
        watchdog_seconds: float = _DEFAULT_WATCHDOG_SECONDS,
        replay_active: Callable[[], bool] | None = None,
    ) -> None:
        self.runtime = runtime
        self.config = config
        self._feed_factory = feed_factory
        self._on_update = on_update
        self._on_snapshot = on_snapshot
        # When True, the intraday warm-start replay's per-event deltas are not streamed
        # while the frontier lags wall clock (it advances the browser via throttled
        # snapshots instead). Off by default so non-warm-start callers/tests are
        # byte-for-byte unchanged.
        self._throttle_warm_start = throttle_warm_start
        self._live_streaming = False
        self._market_update_lag: float | None = None
        self._last_warm_snapshot_at: datetime | None = None
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
        # WARM-FIX P2: post-subscribe liveness watchdog. Armed on every (re)start,
        # disarmed by the warm->live flip (the first real gateway message); strikes
        # survive a WATCHDOG reconnect so a second silence turns FAILED (D-P-06
        # single retry), and reset on flip, operator stop, and any OPERATOR start
        # (verify fix: a stale strike must not deny a fresh session its retry).
        self._watchdog_seconds = max(watchdog_seconds, 0.01)
        self._watchdog_task: asyncio.Task[None] | None = None
        self._watchdog_strikes = 0
        self._last_item_wall_utc: datetime | None = None
        # Verify fix (adversarial-verify major): the internal reconnect path must
        # honor the same live/replay mutual exclusion the HTTP endpoints enforce
        # (audit #NN-2) — a reconnect firing under an active replay would reset the
        # shared runtime and arm the warm gate underneath it.
        self._replay_active = replay_active

    @property
    def has_update_callback(self) -> bool:
        return self._on_update is not None

    def set_update_callback(
        self, callback: Callable[[RuntimeUpdate], Awaitable[None]] | None
    ) -> None:
        self._on_update = callback

    def set_snapshot_callback(
        self, callback: Callable[[], Awaitable[None]] | None
    ) -> None:
        self._on_snapshot = callback

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

    async def start(self, *, _preserve_watchdog_strikes: bool = False) -> None:
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
            # Verify fix: an OPERATOR start begins a fresh watchdog episode (full
            # DEGRADED + single-retry contract); only the watchdog's own D-P-06
            # reconnect carries its strike forward so a second silence turns FAILED.
            if not _preserve_watchdog_strikes:
                self._watchdog_strikes = 0
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
            # WARM-FIX P3 (verify fix: anchor-based): observations originating from
            # touches BEFORE this instant never predict/register/journal — during
            # the drain AND when their window crosses into the live phase. The
            # anchor stays set for the whole session; live-originated touches are
            # unaffected by it.
            self.runtime.set_live_warm_inference_gate(self._warm_anchor_utc)
            # Each (re)start re-runs the warm-start replay, so re-arm the throttle.
            self._live_streaming = False
            self._last_warm_snapshot_at = None
            await self._emit_status(FeedConnectionState.CONNECTING, "live feed connecting")
            feed: MarketDataFeed | None = None
            try:
                feed = self._feed_factory(self.config)
                self._feed = feed
                core_live = CoreLiveRuntime(
                    feed,
                    process_item=self._process_live_item,
                    on_update=self._emit_market,
                    is_warning=lambda item: isinstance(item, DataQualityWarning),
                    is_event=lambda item: not isinstance(item, (DataQualityWarning, FeedStatus)),
                    event_timestamp=lambda item: getattr(item, "event_ts_utc", None),
                )
                self.strategy_core_live = core_live
                await core_live.start()
            except Exception as exc:
                self._state = LiveState.FAILED
                self._last_error = type(exc).__name__
                self.runtime.set_live_warm_inference_gate(None)
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
            # WARM-FIX P2: (re)arm the liveness watchdog for this start's live phase.
            self._last_item_wall_utc = self._now()
            self._arm_watchdog()

    async def stop(self) -> None:
        async with self._lock:
            await self._disarm_watchdog()
            self._watchdog_strikes = 0
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
            # WARM-FIX P3: a stop must not leave the shared runtime gated
            # (the next replay/live start resets anyway; this is belt-and-braces).
            self.runtime.set_live_warm_inference_gate(None)
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
        # WARM-FIX P2: any provider item advances the liveness clock (during the
        # warm drain this keeps the watchdog quiet; after the drain only real
        # gateway traffic does).
        self._last_item_wall_utc = self._now()
        if isinstance(item, FeedStatus):
            self._market_update_lag = None
            return self.runtime.set_feed_status(item)
        if isinstance(item, DataQualityWarning):
            self._market_update_lag = None
            return self.runtime.record_warning(item)
        self._mark_warm_start(item)
        # How far the just-processed market event lags wall clock. Read by
        # _emit_market to decide whether this is warm-start replay (suppress the
        # per-event broadcast) or real time (stream it). None for non-market items.
        ts = getattr(item, "event_ts_utc", None)
        self._market_update_lag = None if ts is None else (self._now() - ts).total_seconds()
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
            # WARM-FIX P2: the live phase delivered — the watchdog loop disarms on
            # this flip, and a fresh silence episode starts from zero strikes.
            # (P3's anchor deliberately stays set: warm-originated observations
            # whose windows cross the seam must still produce nothing.)
            self._watchdog_strikes = 0

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
        # Verify fix (adversarial-verify major): a replay may legitimately have
        # started during the reconnect delay (DISCONNECTED is not an active live
        # state for the endpoint 409 guard). Reconnecting now would reset the
        # shared runtime and arm the warm gate underneath the running replay —
        # honor audit #NN-2's mutual exclusion on this internal path too.
        if self._replay_active is not None and self._replay_active():
            logger.warning(
                "live auto-reconnect skipped: a replay is active on the shared "
                "runtime (audit #NN-2 mutual exclusion); restart live manually "
                "after the replay finishes"
            )
            return
        logger.info("live feed reconnecting from the trading-day start (D-P-06)")
        try:
            # D-P-06: the watchdog's single retry carries its strike into this
            # start so a second silent episode turns FAILED; operator starts reset.
            await self.start(_preserve_watchdog_strikes=True)
        except Exception as exc:
            logger.error(
                "live auto-reconnect failed: exception_type=%s message=%s",
                type(exc).__name__,
                _redact_configured_secrets(str(exc), self.config.secret_values),
            )

    def _arm_watchdog(self) -> None:
        stale = self._watchdog_task
        if stale is not None and not stale.done():
            stale.cancel()
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def _disarm_watchdog(self) -> None:
        task = self._watchdog_task
        self._watchdog_task = None
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _watchdog_loop(self) -> None:
        """WARM-FIX P2: post-subscribe liveness watchdog (WEDGE_CAPTURE.md).

        The wedge signature this exists for: state=running, warm_start_state
        stuck at "warming", event counters frozen at the warm total, feed
        "connected", zero live messages — with no error from the SDK (its own
        monitor is blind before the first record) and none from TL. The loop
        disarms on the first received live message (the warm->live flip) and is
        re-armed by every (re)start; wall-clock silence beyond the threshold
        triggers the D-P-06 single-attempt reconnect, and a second silent episode
        marks the feed FAILED for the operator.
        """

        interval = min(max(self._watchdog_seconds / 4.0, 0.01), 1.0)
        while True:
            await asyncio.sleep(interval)
            if self._state is not LiveState.RUNNING:
                return
            if self._warm_start_state == "live":
                return
            last = self._effective_liveness_stamp()
            if last is None:
                continue
            silent_for = (self._now() - last).total_seconds()
            if silent_for <= self._watchdog_seconds:
                continue
            await self._handle_watchdog_timeout(silent_for)
            return

    def _effective_liveness_stamp(self) -> datetime | None:
        """The freshest evidence the live session is alive.

        Verify fix (adversarial-verify major): processed items alone are
        heartbeat-blind — the adapter drops gateway SystemMsg heartbeats and
        symbology mappings as control messages BEFORE they reach
        ``_process_live_item``, yet those records are exactly the healthy-vs-wedge
        discriminator (WEDGE_CAPTURE.md §C.4: a started session heartbeats every
        30 s even with zero market data; the wedge had zero inbound bytes). The
        Databento feed therefore exposes ``last_provider_activity_utc``, stamped
        on EVERY provider callback; folding it in keeps a quiet-but-healthy
        market (weekend/halt start) from being torn down as a wedge, while a true
        wedge (no callbacks at all) still trips the watchdog.
        """

        stamps = [self._last_item_wall_utc]
        feed_activity = getattr(self._feed, "last_provider_activity_utc", None)
        if isinstance(feed_activity, datetime):
            stamps.append(feed_activity)
        present = [stamp for stamp in stamps if stamp is not None]
        return max(present) if present else None

    async def _handle_watchdog_timeout(self, silent_for: float) -> None:
        async with self._lock:
            if self._state is not LiveState.RUNNING or self._warm_start_state == "live":
                return
            self._watchdog_strikes += 1
            strike = self._watchdog_strikes
            logger.error(
                "live liveness watchdog: ZERO provider messages for %.0f s after the "
                "post-drain live subscribe (wedge signature: state=running, "
                "warm_start_state=warming, events frozen at the warm total — "
                "WEDGE_CAPTURE.md); %s",
                silent_for,
                "forcing the D-P-06 single-attempt reconnect"
                if strike == 1
                else "second silent episode — marking the live feed FAILED",
            )
            # Tear the wedged session down. core.stop() stops the feed it owns
            # (mirrors stop()); dropping the core handle makes status() report from
            # this service's state instead of the stopped core's.
            core = self.strategy_core_live
            self.strategy_core_live = None
            if core is not None:
                with suppress(Exception):
                    await core.stop()
            elif self._feed is not None:
                with suppress(Exception):
                    await self._feed.stop()
            self._feed = None
            if strike >= 2:
                self._state = LiveState.FAILED
                self._last_error = "live_watchdog_silent_after_reconnect"
                self.runtime.set_live_warm_inference_gate(None)
                await self._emit_status(
                    FeedConnectionState.DISCONNECTED,
                    "live feed failed: watchdog found no live messages after reconnect",
                )
                return
            self._state = LiveState.DISCONNECTED
            await self._emit_status(
                FeedConnectionState.DEGRADED,
                "live feed degraded: no live messages after the warm drain "
                "(wedge signature); reconnecting",
            )
            self._schedule_reconnect()

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

    async def _emit_market(self, update: RuntimeUpdate) -> None:
        """``on_update`` for the live source: applies the warm-start broadcast throttle.

        With throttling off this is byte-for-byte the prior behaviour (forward to the
        update callback). With it on, a market delta whose event frontier still lags
        wall clock is NOT streamed; a throttled full snapshot carries catch-up progress
        instead, and per-event streaming resumes (after one flush) once the frontier
        reaches real time. Feed-status / warning items (no event ts) always forward.

        EXEC verify fix (major): updates carrying SERVING deltas (predictions,
        outcomes, drops, resets) are NEVER suppressed. The paper-execution tracker
        observes only broadcast updates (the broadcaster choke point) and an
        outcome rides exactly one update — a suppressed resolution would leave a
        paper position open forever with no close row, and a catch-up-tail
        prediction (live-originated touch past the warm anchor, frontier still
        lagging) would never open. These deltas are a handful per session, so
        forwarding their full updates cannot flood the browser the way the
        per-bar stream does; the UI also stops silently losing those frames.
        """

        if not self._throttle_warm_start:
            await self._emit(update)
            return
        if self._on_update is None or not update.has_deltas():
            return
        lag = self._market_update_lag
        if lag is None:
            await self._on_update(update)
            return
        if self._live_streaming:
            if lag > _WARMING_LAG_SECONDS:
                self._live_streaming = False
        elif lag <= _LIVE_LAG_SECONDS:
            # Caught up: push the whole built-up day once, then stream per event.
            self._live_streaming = True
            await self._broadcast_snapshot()
        if self._live_streaming or _carries_serving_deltas(update):
            await self._on_update(update)
        else:
            await self._maybe_emit_warm_snapshot()

    async def _broadcast_snapshot(self) -> None:
        if self._on_snapshot is None:
            return
        self._last_warm_snapshot_at = self._now()
        await self._on_snapshot()

    async def _maybe_emit_warm_snapshot(self) -> None:
        last = self._last_warm_snapshot_at
        if (
            last is not None
            and (self._now() - last).total_seconds() < _WARM_SNAPSHOT_MIN_INTERVAL_SECONDS
        ):
            return
        await self._broadcast_snapshot()


def _carries_serving_deltas(update: RuntimeUpdate) -> bool:
    """True when the update carries prediction/outcome/drop/reset deltas.

    These are throttle-exempt (see ``_emit_market``): each rides exactly one
    RuntimeUpdate and downstream consumers (the paper-execution tracker, the
    prediction panes/blotter) have no snapshot-replay recovery path for them.
    """

    return bool(
        update.predictions
        or update.outcomes
        or update.dropped
        or update.model_reset_reason is not None
    )


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
