"""WARM-FIX P2: post-drain liveness watchdog (WEDGE_CAPTURE.md wedge signature).

The watchdog exists because BOTH detection layers below TL are blind to a live
session that authenticates but never delivers its first message: the databento
SDK's own monitor only measures gaps after the first received record, and the
TL status surface asserts CONNECTED before any live byte. These tests drive the
service with real wall clocks and tiny thresholds.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from trade_lab.domain.events import TradeEvent
from trade_lab.domain.feed import FeedConnectionState
from trade_lab.services.live import LiveConfig, LiveMarketDataService, LiveState
from trade_lab.services.runtime import ApplicationRuntime, RuntimeUpdate


def _runtime() -> ApplicationRuntime:
    return ApplicationRuntime(
        requested_symbol="NQ.c.0",
        tick_timeframes=(2,),
        observation_duration_seconds=300,
    )


def _live_config() -> LiveConfig:
    return LiveConfig(
        requested_symbol="NQ.c.0",
        dataset="GLBX.MDP3",
        trade_schema="trades",
        quote_schema="mbp-1",
        context_schemas=("definition",),
        api_key_configured=True,
        enabled=True,
    )


def _trade(ts: datetime, price_ticks: int = 80_000) -> TradeEvent:
    return TradeEvent(ts, None, 1, "NQ.c.0", "NQM6", price_ticks, 1, source_schema="trades")


class _WarmThenSilentFeed:
    """Drains one warm (pre-anchor) trade, then delivers nothing — the wedge."""

    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        self.release.set()

    async def events(self) -> AsyncIterator[object]:
        yield _trade(datetime.now(UTC) - timedelta(hours=3))
        await self.release.wait()


class _LiveFlowingFeed:
    """Delivers a real-time (post-anchor) trade immediately, then idles."""

    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        self.release.set()

    async def events(self) -> AsyncIterator[object]:
        yield _trade(datetime.now(UTC) + timedelta(seconds=1), 80_002)
        await self.release.wait()


async def _wait_for(predicate, *, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached within timeout")


def test_silent_subscribe_goes_degraded_and_reconnects() -> None:
    asyncio.run(_run_silent_subscribe())


async def _run_silent_subscribe() -> None:
    factory_calls = 0
    feeds = [_WarmThenSilentFeed(), _LiveFlowingFeed()]
    updates: list[RuntimeUpdate] = []

    async def capture(update: RuntimeUpdate) -> None:
        updates.append(update)

    def factory(_config: LiveConfig):
        nonlocal factory_calls
        factory_calls += 1
        return feeds[factory_calls - 1]

    live = LiveMarketDataService(
        _runtime(),
        _live_config(),
        factory,
        on_update=capture,
        reconnect_delay_seconds=0.02,
        watchdog_seconds=0.08,
    )
    await live.start()
    # Strike 1: the silent post-drain session must go DEGRADED and reconnect;
    # the second feed flows, so the service ends RUNNING with a live flip.
    await _wait_for(
        lambda: factory_calls == 2 and live.status().warm_start_state == "live"
    )
    assert live.status().state == LiveState.RUNNING
    degraded = [
        update.feed_status.last_message
        for update in updates
        if update.feed_status is not None
        and update.feed_status.state is FeedConnectionState.DEGRADED
    ]
    assert degraded and "wedge signature" in degraded[0]
    await live.stop()


def test_flowing_feed_never_trips_the_watchdog() -> None:
    asyncio.run(_run_flowing_feed())


async def _run_flowing_feed() -> None:
    factory_calls = 0

    def factory(_config: LiveConfig):
        nonlocal factory_calls
        factory_calls += 1
        return _LiveFlowingFeed()

    live = LiveMarketDataService(
        _runtime(),
        _live_config(),
        factory,
        reconnect_delay_seconds=0.02,
        watchdog_seconds=0.08,
    )
    await live.start()
    await _wait_for(lambda: live.status().warm_start_state == "live")
    # Sit well past several watchdog periods: a flowing (flipped) feed must
    # never be torn down even while subsequently idle.
    await asyncio.sleep(0.4)
    assert factory_calls == 1
    assert live.status().state == LiveState.RUNNING
    await live.stop()


class _HeartbeatOnlyFeed(_WarmThenSilentFeed):
    """Quiet-market shape: no yielded items post-drain, but the provider callback
    keeps firing (gateway heartbeats) — exposed exactly like the Databento feed's
    ``last_provider_activity_utc`` stamp."""

    def __init__(self) -> None:
        super().__init__()
        self.last_provider_activity_utc: datetime | None = None
        self._pulse_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        async def pulse() -> None:
            while not self.release.is_set():
                self.last_provider_activity_utc = datetime.now(UTC)
                await asyncio.sleep(0.02)

        self._pulse_task = asyncio.create_task(pulse())

    async def stop(self) -> None:
        await super().stop()
        if self._pulse_task is not None:
            self._pulse_task.cancel()


def test_heartbeats_keep_a_quiet_market_alive() -> None:
    """Verify fix: gateway heartbeats are dropped as control messages before
    _process_live_item, so the watchdog folds in the feed's provider-activity
    stamp — a healthy-but-quiet market (weekend/halt start) must NOT be torn
    down as a wedge."""

    asyncio.run(_run_heartbeat_quiet_market())


async def _run_heartbeat_quiet_market() -> None:
    factory_calls = 0

    def factory(_config: LiveConfig):
        nonlocal factory_calls
        factory_calls += 1
        return _HeartbeatOnlyFeed()

    live = LiveMarketDataService(
        _runtime(),
        _live_config(),
        factory,
        reconnect_delay_seconds=0.02,
        watchdog_seconds=0.08,
    )
    await live.start()
    # Many watchdog periods with zero processed items but steady heartbeats:
    # still warming, still RUNNING, no teardown, no reconnect.
    await asyncio.sleep(0.5)
    status = live.status()
    assert factory_calls == 1
    assert status.state == LiveState.RUNNING
    assert status.warm_start_state == "warming"
    await live.stop()


def test_operator_start_after_failed_gets_a_fresh_watchdog_episode() -> None:
    """Verify fix: strikes must not leak from a FAILED session into a fresh
    operator start — the new session gets the full DEGRADED + single-retry
    contract instead of failing terminally on its first silence."""

    asyncio.run(_run_operator_restart_after_failed())


async def _run_operator_restart_after_failed() -> None:
    factory_calls = 0

    def factory(_config: LiveConfig):
        nonlocal factory_calls
        factory_calls += 1
        return _WarmThenSilentFeed()

    live = LiveMarketDataService(
        _runtime(),
        _live_config(),
        factory,
        reconnect_delay_seconds=0.02,
        watchdog_seconds=0.08,
    )
    await live.start()
    await _wait_for(lambda: live.status().state == LiveState.FAILED, timeout=8.0)
    assert factory_calls == 2  # episode 1: strike 1 + its single retry

    # Operator restarts directly from FAILED (the only UI-reachable action).
    await live.start()
    # The fresh episode must run its OWN strike-1 reconnect (a stale strike would
    # jump straight to FAILED with no third/fourth feed).
    await _wait_for(lambda: live.status().state == LiveState.FAILED, timeout=8.0)
    assert factory_calls == 4
    await live.stop()


def test_reconnect_is_skipped_while_a_replay_is_active(
    caplog,
) -> None:
    """Verify fix: the internal reconnect path honors the live/replay mutual
    exclusion (audit #NN-2) — it must not reset the shared runtime and arm the
    warm gate underneath a running replay."""

    asyncio.run(_run_reconnect_replay_guard(caplog))


async def _run_reconnect_replay_guard(caplog) -> None:
    factory_calls = 0

    class _DroppingFeed:
        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

        async def events(self) -> AsyncIterator[object]:
            yield _trade(datetime.now(UTC) - timedelta(hours=3))

    def factory(_config: LiveConfig):
        nonlocal factory_calls
        factory_calls += 1
        return _DroppingFeed()

    live = LiveMarketDataService(
        _runtime(),
        _live_config(),
        factory,
        reconnect_delay_seconds=0.02,
        watchdog_seconds=60.0,
        replay_active=lambda: True,
    )
    with caplog.at_level("WARNING"):
        await live.start()
        # The feed ends (disconnect) -> reconnect scheduled -> must be SKIPPED.
        await _wait_for(lambda: live.status().state == LiveState.DISCONNECTED)
        await asyncio.sleep(0.2)
    assert factory_calls == 1
    assert live.status().state == LiveState.DISCONNECTED
    assert any("reconnect skipped" in record.message for record in caplog.records)
    await live.stop()


def test_reconnect_then_silence_fails_for_the_operator() -> None:
    asyncio.run(_run_second_silence())


async def _run_second_silence() -> None:
    factory_calls = 0
    feeds = [_WarmThenSilentFeed(), _WarmThenSilentFeed()]

    def factory(_config: LiveConfig):
        nonlocal factory_calls
        factory_calls += 1
        return feeds[factory_calls - 1]

    live = LiveMarketDataService(
        _runtime(),
        _live_config(),
        factory,
        reconnect_delay_seconds=0.02,
        watchdog_seconds=0.08,
    )
    await live.start()
    # Strike 1 reconnects (single D-P-06 retry); the retry is silent too ->
    # strike 2 marks FAILED and never spawns a third feed.
    await _wait_for(lambda: live.status().state == LiveState.FAILED, timeout=8.0)
    assert factory_calls == 2
    status = live.status()
    assert status.last_error == "live_watchdog_silent_after_reconnect"
    assert status.warm_start_state != "live"
    await live.stop()
    assert live.status().state == LiveState.STOPPED
