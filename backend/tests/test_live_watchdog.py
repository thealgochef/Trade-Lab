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
