"""W2 P1b/c/d: live warm-start (replay-start + Historical fallback), prior-day
PDH/PDL seeding, warm/live status marking, and disconnect auto-reconnect."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from trade_lab.adapters.databento import DatabentoMarketDataFeed, _DatabentoSdkFacade
from trade_lab.adapters.databento_historical import DatabentoHistoricalSource
from trade_lab.domain.events import TopOfBookEvent, TradeEvent
from trade_lab.services.live import LiveConfig, LiveMarketDataService, LiveState
from trade_lab.services.runtime import ApplicationRuntime
from trade_lab.services.strategy_core_service import StrategyCoreService

_NOW = datetime(2026, 6, 11, 15, 0, tzinfo=UTC)  # Thursday 11:00 ET
_SESSION_OPEN = datetime(2026, 6, 10, 22, 0, tzinfo=UTC)  # Wednesday 18:00 ET


def _runtime() -> ApplicationRuntime:
    return ApplicationRuntime(
        requested_symbol="NQ.c.0",
        tick_timeframes=(2,),
        observation_duration_seconds=300,
    )


def _live_config(*, key: bool = True, enabled: bool = True) -> LiveConfig:
    return LiveConfig(
        requested_symbol="NQ.c.0",
        dataset="GLBX.MDP3",
        trade_schema="trades",
        quote_schema="mbp-1",
        context_schemas=("definition",),
        api_key_configured=key,
        enabled=enabled,
    )


class _RecordingClient:
    def __init__(self, *, reject_replay_start: bool = False) -> None:
        self.reject_replay_start = reject_replay_start
        self.subscriptions: list[dict[str, object]] = []
        self.started = False
        self.stopped = False

    def add_callback(self, callback: object) -> None:
        self.callback = callback

    def subscribe(self, **kwargs: object) -> None:
        if self.reject_replay_start and kwargs.get("start") is not None:
            raise ValueError("gateway rejected the replay start parameter")
        self.subscriptions.append(kwargs)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class _FakeSdk:
    def __init__(self, *, reject_replay_start: bool = False) -> None:
        self.reject_replay_start = reject_replay_start
        self.clients: list[_RecordingClient] = []
        sdk = self

        class Live(_RecordingClient):
            def __init__(self, key: str) -> None:
                super().__init__(reject_replay_start=sdk.reject_replay_start)
                self.key = key
                sdk.clients.append(self)

        self.Live = Live


def _feed(sdk: _FakeSdk, **kwargs: object) -> DatabentoMarketDataFeed:
    return DatabentoMarketDataFeed(
        api_key="k" * 32,
        requested_symbol="NQ.c.0",
        dataset="GLBX.MDP3",
        trade_schema="trades",
        quote_schema="mbp-1",
        context_schemas=("definition",),
        sdk_facade=_DatabentoSdkFacade(sdk),
        now_provider=lambda: _NOW,
        **kwargs,
    )


def test_intraday_replay_subscribes_trade_and_quote_with_trading_day_start() -> None:
    asyncio.run(_run_replay_start_subscribe())


async def _run_replay_start_subscribe() -> None:
    sdk = _FakeSdk()
    feed = _feed(sdk, intraday_replay=True)
    await feed.start()
    client = sdk.clients[0]
    by_schema = {sub["schema"]: sub for sub in client.subscriptions}
    assert by_schema["trades"]["start"] == _SESSION_OPEN
    assert by_schema["mbp-1"]["start"] == _SESSION_OPEN
    assert by_schema["definition"].get("start") is None
    assert client.started is True
    await feed.stop()


def test_without_intraday_replay_no_start_parameter_is_sent() -> None:
    asyncio.run(_run_no_replay_start())


async def _run_no_replay_start() -> None:
    sdk = _FakeSdk()
    feed = _feed(sdk)
    await feed.start()
    assert all("start" not in sub for sub in sdk.clients[0].subscriptions)
    await feed.stop()


def _historical_records(
    fetch_calls: list[tuple[str, datetime, datetime]],
) -> DatabentoHistoricalSource:
    def record_fetcher(schema: str, start: datetime, end: datetime):
        fetch_calls.append((schema, start, end))
        if schema == "trades":
            return [
                SimpleNamespace(
                    ts_event=1_781_200_000_000_000_000, price=20_000_250_000_000, size=2, side="A"
                ),
                SimpleNamespace(
                    ts_event=1_781_200_002_000_000_000, price=20_000_500_000_000, size=1, side="B"
                ),
            ]
        return [
            SimpleNamespace(
                ts_event=1_781_200_001_000_000_000,
                levels=[
                    SimpleNamespace(
                        bid_px=20_000_000_000_000, bid_sz=3, ask_px=20_000_500_000_000, ask_sz=4
                    )
                ],
            )
        ]

    return DatabentoHistoricalSource(
        api_key=None,
        dataset="GLBX.MDP3",
        requested_symbol="NQ.c.0",
        stype_in="continuous",
        record_fetcher=record_fetcher,
    )


def test_rejected_replay_start_falls_back_to_historical_records_then_live() -> None:
    asyncio.run(_run_fallback_warm_start())


async def _run_fallback_warm_start() -> None:
    sdk = _FakeSdk(reject_replay_start=True)
    fetch_calls: list[tuple[str, datetime, datetime]] = []
    feed = _feed(sdk, intraday_replay=True, historical_source=_historical_records(fetch_calls))
    await feed.start()
    # The rejecting client was torn down; no live client exists until the
    # historical records drain inside events().
    assert len(sdk.clients) == 1
    assert sdk.clients[0].stopped is True
    assert [(schema, start) for schema, start, _end in fetch_calls] == [
        ("trades", _SESSION_OPEN),
        ("mbp-1", _SESSION_OPEN),
    ]

    events: list[object] = []

    async def consume() -> None:
        async for item in feed.events():
            events.append(item)

    task = asyncio.create_task(consume())
    for _ in range(300):
        if len(sdk.clients) == 2:
            break
        await asyncio.sleep(0.01)
    await feed.stop()
    await task

    # ts_event-merged order: trade, quote, trade — through the SAME normalize path.
    assert isinstance(events[1], TradeEvent)
    assert isinstance(events[2], TopOfBookEvent)
    assert isinstance(events[3], TradeEvent)
    assert events[1].price_ticks == 80_001  # 20000.25 / 0.25
    assert events[1].side.value == "sell"  # Databento 'A' = sell aggressor
    assert events[2].bid_price_ticks == 80_000
    # After the drain the feed subscribed live WITHOUT a start parameter.
    assert len(sdk.clients) == 2
    live_client = sdk.clients[1]
    assert all(sub.get("start") is None for sub in live_client.subscriptions)
    assert live_client.started is True


def _ohlcv_source(calls: list[tuple[datetime, datetime]]) -> DatabentoHistoricalSource:
    def ohlcv_fetcher(start: datetime, end: datetime):
        import pandas as pd

        calls.append((start, end))
        return pd.DataFrame({"high": [20_100.25, 20_150.50], "low": [19_900.75, 19_850.25]})

    return DatabentoHistoricalSource(
        api_key=None,
        dataset="GLBX.MDP3",
        requested_symbol="NQ.c.0",
        stype_in="continuous",
        ohlcv_fetcher=ohlcv_fetcher,
    )


def test_live_start_seeds_prior_day_summary_through_the_runtime_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_run_prior_day_seed(monkeypatch))


async def _run_prior_day_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded: list[tuple[object, int, int]] = []

    def record_load(self, trading_day, high_ticks, low_ticks):
        loaded.append((trading_day, high_ticks, low_ticks))

    monkeypatch.setattr(StrategyCoreService, "load_prior_day_summary", record_load)
    calls: list[tuple[datetime, datetime]] = []

    class _Feed:
        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

        async def events(self) -> AsyncIterator[object]:
            if False:  # pragma: no cover - intentionally empty feed
                yield object()

    live = LiveMarketDataService(
        _runtime(),
        _live_config(),
        lambda _config: _Feed(),
        historical_source=_ohlcv_source(calls),
        now_provider=lambda: _NOW,
    )
    await live.start()
    await live.stop()
    # Prior trading day of Thursday 2026-06-11 is Wednesday 2026-06-10:
    # [Tue 18:00 ET, Wed 18:00 ET) in UTC.
    assert calls == [
        (datetime(2026, 6, 9, 22, 0, tzinfo=UTC), datetime(2026, 6, 10, 22, 0, tzinfo=UTC))
    ]
    assert loaded == [(datetime(2026, 6, 10).date(), 80_602, 79_401)]


def test_live_start_without_historical_access_skips_prior_day_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    asyncio.run(_run_prior_day_skip(caplog))


async def _run_prior_day_skip(caplog: pytest.LogCaptureFixture) -> None:
    live = LiveMarketDataService(_runtime(), _live_config(), lambda _config: _empty_feed())
    with caplog.at_level("WARNING"):
        await live.start()
        await live.stop()
    assert any("prior-day summary skipped" in record.message for record in caplog.records)


def _empty_feed():
    class _Feed:
        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

        async def events(self) -> AsyncIterator[object]:
            if False:  # pragma: no cover - intentionally empty feed
                yield object()

    return _Feed()


def _trade(ts: datetime, price_ticks: int = 80_000) -> TradeEvent:
    return TradeEvent(ts, None, 1, "NQ.c.0", "NQM6", price_ticks, 1, source_schema="trades")


def test_warm_start_marking_counts_replayed_events_then_flips_live() -> None:
    asyncio.run(_run_warm_marking())


async def _run_warm_marking() -> None:
    class _Feed:
        def __init__(self, events: list[object]) -> None:
            self._events = events
            self.release = asyncio.Event()

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            self.release.set()

        async def events(self) -> AsyncIterator[object]:
            for event in self._events:
                yield event
            await self.release.wait()

    feed = _Feed(
        [
            _trade(datetime(2026, 6, 11, 1, 0, tzinfo=UTC)),  # replayed (before _NOW)
            _trade(datetime(2026, 6, 11, 14, 0, tzinfo=UTC), 80_002),  # replayed
            _trade(datetime(2026, 6, 11, 15, 0, 1, tzinfo=UTC), 80_004),  # real time
        ]
    )
    live = LiveMarketDataService(
        _runtime(),
        _live_config(),
        lambda _config: feed,
        now_provider=lambda: _NOW,
    )
    await live.start()
    for _ in range(200):
        if live.status().warm_start_state == "live":
            break
        await asyncio.sleep(0.01)
    status = live.status()
    assert status.warm_start_state == "live"
    assert status.warm_start_events == 2
    await live.stop()


def test_disconnect_reconnects_through_the_full_start_path() -> None:
    asyncio.run(_run_reconnect())


async def _run_reconnect() -> None:
    factory_calls = 0

    class _DroppingFeed:
        """Yields one trade then ends (mid-session disconnect)."""

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

        async def events(self) -> AsyncIterator[object]:
            yield _trade(datetime(2026, 6, 11, 14, 0, tzinfo=UTC))

    class _SteadyFeed:
        def __init__(self) -> None:
            self.release = asyncio.Event()

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            self.release.set()

        async def events(self) -> AsyncIterator[object]:
            yield _trade(datetime(2026, 6, 11, 14, 5, tzinfo=UTC), 80_002)
            await self.release.wait()

    def factory(_config: LiveConfig):
        nonlocal factory_calls
        factory_calls += 1
        return _DroppingFeed() if factory_calls == 1 else _SteadyFeed()

    runtime = _runtime()
    live = LiveMarketDataService(
        runtime,
        _live_config(),
        factory,
        reconnect_delay_seconds=0.02,
        now_provider=lambda: _NOW,
    )
    await live.start()
    for _ in range(300):
        if (
            factory_calls >= 2
            and live.status().state == LiveState.RUNNING
            and runtime.snapshot().current_bars
        ):
            break
        await asyncio.sleep(0.01)
    assert factory_calls == 2
    assert live.status().state == LiveState.RUNNING
    # The reconnect re-ran the full start path: the runtime was reset (the second
    # feed's single trade is the only one the rebuilt engine has seen).
    assert runtime.snapshot().current_bars[0].trade_count == 1
    await live.stop()
    assert live.status().state == LiveState.STOPPED
