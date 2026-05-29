import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from trade_lab.adapters.databento import (
    DatabentoMarketDataFeed,
    DatabentoUnavailableError,
    normalize_provider_message,
)
from trade_lab.api.app import create_app
from trade_lab.config import Settings
from trade_lab.domain.data_quality import DataQualityCode, DataQualityWarning
from trade_lab.domain.events import (
    DailyStatisticEvent,
    InstrumentDefinitionEvent,
    MarketStatus,
    MarketStatusEvent,
    TopOfBookEvent,
    TradeEvent,
)
from trade_lab.domain.feed import FeedConnectionState, FeedStatus
from trade_lab.services.broadcaster import WebSocketBroadcaster
from trade_lab.services.live import LiveConfig, LiveMarketDataService, LiveState
from trade_lab.services.runtime import ApplicationRuntime


class FakeFeed:
    def __init__(self, events: list[object]) -> None:
        self.started = False
        self.stopped = False
        self._events = events

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def events(self) -> AsyncIterator[object]:
        for event in self._events:
            yield event


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
        context_schemas=("definition", "status", "statistics"),
        api_key_configured=key,
        enabled=enabled,
    )


def test_settings_mask_key_and_do_not_load_env_file_by_default() -> None:
    settings = Settings(_env_file=None, databento_api_key="db-secret")

    assert "db-secret" not in repr(settings)
    assert "databento_api_key" not in settings.safe_dict()
    assert settings.databento_dataset == "GLBX.MDP3"
    assert settings.databento_requested_symbol == "NQ.c.0"


def test_live_status_endpoint_does_not_leak_key() -> None:
    client = TestClient(
        create_app(
            Settings(_env_file=None, databento_api_key="db-secret", databento_live_enabled=True)
        )
    )

    response = client.get("/api/v1/live/status")

    payload = response.json()
    assert response.status_code == 200
    assert payload["api_key_configured"] is True
    assert "db-secret" not in str(payload)


def test_runtime_status_endpoint_includes_live_status_without_key_leakage() -> None:
    client = TestClient(
        create_app(
            Settings(_env_file=None, databento_api_key="db-secret", databento_live_enabled=True)
        )
    )

    response = client.get("/api/v1/status")

    payload = response.json()
    assert response.status_code == 200
    assert payload["live"]["api_key_configured"] is True
    assert payload["live"]["dataset"] == "GLBX.MDP3"
    assert "db-secret" not in str(payload)


def test_app_creation_does_not_auto_start_live_feed() -> None:
    settings = Settings(_env_file=None, databento_api_key="db-secret", databento_live_enabled=True)
    app = create_app(settings)

    status = app.state.live.status()

    assert status.state == LiveState.IDLE
    assert status.events_processed == 0


def test_live_start_without_key_fails_safely() -> None:
    client = TestClient(create_app(Settings(_env_file=None, databento_live_enabled=True)))

    response = client.post("/api/v1/live/start")

    assert response.status_code == 400
    assert "API key" in response.json()["detail"]
    assert "secret" not in str(response.json()).lower()


def test_real_databento_adapter_fails_truthfully_when_sdk_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingSdkFeed:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def start(self) -> None:
            raise DatabentoUnavailableError("Databento SDK is not installed. Install it.")

        async def stop(self) -> None:
            pass

    monkeypatch.setattr("trade_lab.api.app.DatabentoMarketDataFeed", MissingSdkFeed)
    client = TestClient(
        create_app(
            Settings(_env_file=None, databento_api_key="db-secret", databento_live_enabled=True)
        )
    )

    response = client.post("/api/v1/live/start")

    assert response.status_code == 400
    assert "Databento SDK is not installed" in response.json()["detail"]
    assert "db-secret" not in str(response.json())


def test_live_factory_exception_redacts_all_configured_secrets(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class ExplodingDatabentoFeed:
        def __init__(self, **_kwargs: object) -> None:
            raise RuntimeError(
                "provider rejected api_key=db-secret operator=operator-secret"
            )

    monkeypatch.setattr("trade_lab.api.app.DatabentoMarketDataFeed", ExplodingDatabentoFeed)
    caplog.set_level(logging.ERROR)
    app = create_app(
        Settings(
            _env_file=None,
            databento_api_key="db-secret",
            databento_live_enabled=True,
            operator_token="operator-secret",
        )
    )
    client = TestClient(app)

    response = client.post("/api/v1/live/start")
    status_payload = client.get("/api/v1/live/status").json()
    combined = f"{response.json()} {status_payload} {caplog.text}"

    assert response.status_code == 400
    assert status_payload["state"] == LiveState.FAILED.value
    assert status_payload["last_error"] == "RuntimeError"
    assert "db-secret" not in combined
    assert "operator-secret" not in combined
    assert "<redacted>" in caplog.text


def test_live_control_rejects_remote_without_operator_token_and_allows_with_token() -> None:
    app = create_app(
        Settings(
            _env_file=None,
            databento_api_key="db-secret",
            databento_live_enabled=True,
            operator_token="operator-secret",
        )
    )
    remote = TestClient(app, client=("203.0.113.10", 12345))

    rejected = remote.post("/api/v1/live/stop")
    allowed = remote.post(
        "/api/v1/live/stop", headers={"x-trade-lab-operator-token": "operator-secret"}
    )

    assert rejected.status_code == 403
    assert allowed.status_code == 200
    assert "operator-secret" not in str(rejected.json()) + str(allowed.json())


def test_live_control_allows_configured_browser_origin_for_local_dev() -> None:
    live = LiveMarketDataService(_runtime(), _live_config(), lambda _config: FakeFeed([]))
    client = TestClient(create_app(Settings(_env_file=None), live=live))

    response = client.post(
        "/api/v1/live/start", headers={"Origin": "http://localhost:5174"}
    )

    client.post("/api/v1/live/stop")

    assert response.status_code == 200
    assert response.json()["state"] in {LiveState.CONNECTING.value, LiveState.RUNNING.value}


def test_live_control_rejects_disallowed_browser_origin_even_from_loopback() -> None:
    live = LiveMarketDataService(_runtime(), _live_config(), lambda _config: FakeFeed([]))
    client = TestClient(create_app(Settings(_env_file=None), live=live))

    response = client.post(
        "/api/v1/live/start", headers={"Origin": "https://evil.example"}
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "browser origin is not allowed"
    assert live.status().state == LiveState.IDLE


def test_live_control_allows_no_origin_local_operator_client() -> None:
    live = LiveMarketDataService(_runtime(), _live_config(), lambda _config: FakeFeed([]))
    client = TestClient(create_app(Settings(_env_file=None), live=live))

    response = client.post("/api/v1/live/start")

    client.post("/api/v1/live/stop")

    assert response.status_code == 200
    assert response.json()["state"] in {LiveState.CONNECTING.value, LiveState.RUNNING.value}


def test_live_control_rejects_referer_only_disallowed_origin() -> None:
    live = LiveMarketDataService(_runtime(), _live_config(), lambda _config: FakeFeed([]))
    client = TestClient(create_app(Settings(_env_file=None), live=live))

    response = client.post(
        "/api/v1/live/start", headers={"Referer": "https://evil.example/form"}
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "browser origin is not allowed"
    assert live.status().state == LiveState.IDLE


def test_live_control_operator_token_allows_nonlocal_no_origin_client() -> None:
    app = create_app(
        Settings(
            _env_file=None,
            databento_api_key="db-secret",
            databento_live_enabled=True,
            operator_token="operator-secret",
        )
    )
    remote = TestClient(app, client=("203.0.113.10", 12345))

    response = remote.post(
        "/api/v1/live/stop", headers={"x-trade-lab-operator-token": "operator-secret"}
    )

    assert response.status_code == 200
    assert "operator-secret" not in str(response.json())


def test_live_control_rejects_disallowed_browser_origin_even_with_operator_token() -> None:
    app = create_app(
        Settings(_env_file=None, operator_token="operator-secret")
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/live/stop",
        headers={
            "Origin": "https://evil.example",
            "x-trade-lab-operator-token": "operator-secret",
        },
    )

    assert response.status_code == 403
    assert "operator-secret" not in str(response.json())


def test_live_fake_feed_processes_canonical_events_and_stops() -> None:
    asyncio.run(_run_live_fake_feed())


def test_concurrent_live_starts_only_start_one_feed() -> None:
    asyncio.run(_run_concurrent_live_starts())


async def _run_concurrent_live_starts() -> None:
    class SlowFeed(FakeFeed):
        starts = 0

        async def start(self) -> None:
            SlowFeed.starts += 1
            await asyncio.sleep(0.05)
            await super().start()

        async def events(self) -> AsyncIterator[object]:
            await asyncio.sleep(0.1)
            if False:
                yield object()

    live = LiveMarketDataService(_runtime(), _live_config(), lambda _config: SlowFeed([]))
    results = await asyncio.gather(live.start(), live.start(), return_exceptions=True)

    assert SlowFeed.starts == 1
    assert sum(result is None for result in results) == 1
    assert any(isinstance(result, RuntimeError) for result in results)
    await live.stop()


def test_partial_start_failure_stops_feed() -> None:
    asyncio.run(_run_partial_start_failure())


async def _run_partial_start_failure() -> None:
    class PartialStartFeed(FakeFeed):
        async def start(self) -> None:
            self.started = True
            raise RuntimeError("provider failed after opening resources")

    feed = PartialStartFeed([])
    live = LiveMarketDataService(_runtime(), _live_config(), lambda _config: feed)

    with pytest.raises(RuntimeError, match="live feed failed to start"):
        await live.start()

    assert feed.started is True
    assert feed.stopped is True


async def _run_live_fake_feed() -> None:
    runtime = _runtime()
    trade = TradeEvent(
        datetime(2026, 1, 5, 14, 30, tzinfo=UTC),
        None,
        1,
        "NQ.c.0",
        "NQM6",
        68_000,
        1,
        source_schema="trades",
    )
    quote = TopOfBookEvent(
        datetime(2026, 1, 5, 14, 30, 1, tzinfo=UTC),
        1,
        67_999,
        3,
        68_001,
        4,
        source_schema="mbp-1",
    )
    updates = []
    live = LiveMarketDataService(
        runtime,
        _live_config(),
        lambda _config: FakeFeed(
            [FeedStatus(FeedConnectionState.CONNECTED, "live", "NQ.c.0"), quote, trade]
        ),
        on_update=lambda update: _capture_update(updates, update),
    )

    await live.start()
    await live._task  # tests only: wait for deterministic fake feed completion
    status = live.status()
    assert status.state == LiveState.DISCONNECTED
    assert status.events_processed == 2
    assert runtime.snapshot().current_bars[0].trade_count == 1
    assert runtime.snapshot().feed_status.schema == "trades"
    assert len(updates) >= 3

    await live.stop()
    assert live.status().state == LiveState.STOPPED
    await live.stop()
    assert live.status().state == LiveState.STOPPED


async def _capture_update(updates: list[object], update: object) -> None:
    updates.append(update)


class FakeDatabentoClient:
    def __init__(self, key: str) -> None:
        self.key = key
        self.callbacks: list[object] = []
        self.subscriptions: list[dict[str, object]] = []
        self.started = False
        self.stopped = False

    def add_callback(self, callback: object) -> None:
        self.callbacks.append(callback)

    def subscribe(self, **kwargs: object) -> None:
        self.subscriptions.append(kwargs)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class FakeDatabentoSdk:
    clients: ClassVar[list[FakeDatabentoClient]] = []

    class Live(FakeDatabentoClient):
        def __init__(self, key: str) -> None:
            super().__init__(key)
            FakeDatabentoSdk.clients.append(self)


def test_databento_adapter_subscribes_and_registers_callbacks_with_fake_sdk() -> None:
    async def run() -> None:
        FakeDatabentoSdk.clients = []
        feed = DatabentoMarketDataFeed(
            api_key="db-secret",
            requested_symbol="NQ.c.0",
            dataset="GLBX.MDP3",
            trade_schema="trades",
            quote_schema="mbp-1",
            context_schemas=("definition", "status", "statistics"),
            sdk_module=FakeDatabentoSdk,
        )

        await feed.start()
        client = FakeDatabentoSdk.clients[0]

        assert client.key == "db-secret"
        assert len(client.callbacks) == 1
        assert client.started is True
        assert client.subscriptions == [
            {
                "dataset": "GLBX.MDP3",
                "schema": "trades",
                "symbols": ["NQ.c.0"],
                "stype_in": "continuous",
            },
            {
                "dataset": "GLBX.MDP3",
                "schema": "mbp-1",
                "symbols": ["NQ.c.0"],
                "stype_in": "continuous",
            },
            {
                "dataset": "GLBX.MDP3",
                "schema": "definition",
                "symbols": ["NQ.c.0"],
                "stype_in": "continuous",
            },
            {
                "dataset": "GLBX.MDP3",
                "schema": "status",
                "symbols": ["NQ.c.0"],
                "stype_in": "continuous",
            },
            {
                "dataset": "GLBX.MDP3",
                "schema": "statistics",
                "symbols": ["NQ.c.0"],
                "stype_in": "continuous",
            },
        ]
        await feed.stop()
        assert client.stopped is True

    asyncio.run(run())


def test_app_creation_and_status_do_not_instantiate_or_connect_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExplodingIfInstantiated:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("SDK client must not be created during app/status")

    monkeypatch.setattr("trade_lab.api.app.DatabentoMarketDataFeed", ExplodingIfInstantiated)
    monkeypatch.setattr("trade_lab.api.app.is_databento_sdk_available", lambda: True)

    client = TestClient(
        create_app(
            Settings(_env_file=None, databento_api_key="db-secret", databento_live_enabled=True)
        )
    )

    live_status = client.get("/api/v1/live/status")
    app_status = client.get("/api/v1/status")

    assert live_status.status_code == 200
    assert app_status.status_code == 200
    assert live_status.json()["state"] == LiveState.IDLE.value
    assert app_status.json()["live"]["subscription_ready"] is True
    assert "db-secret" not in str(live_status.json()) + str(app_status.json())


def test_sdk_constructor_signature_failure_is_actionable_and_redacted() -> None:
    class IncompatibleSdk:
        class Live:
            def __init__(self, *, token: str) -> None:
                raise AssertionError(token)

    feed = DatabentoMarketDataFeed(
        api_key="db-secret",
        requested_symbol="NQ.c.0",
        dataset="GLBX.MDP3",
        sdk_module=IncompatibleSdk,
    )

    async def run() -> None:
        await feed.start()

    with pytest.raises(DatabentoUnavailableError) as exc_info:
        asyncio.run(run())
    message = str(exc_info.value)
    assert "constructor signature is incompatible" in message
    assert "db-secret" not in message


def test_provider_callback_from_thread_only_queues_until_iterator_drains() -> None:
    async def run() -> None:
        FakeDatabentoSdk.clients = []
        runtime = _runtime()
        feed = DatabentoMarketDataFeed(
            api_key="db-secret",
            requested_symbol="NQ.c.0",
            dataset="GLBX.MDP3",
            sdk_module=FakeDatabentoSdk,
        )
        await feed.start()
        callback = FakeDatabentoSdk.clients[0].callbacks[0]
        before = runtime.snapshot()

        thread = threading.Thread(
            target=lambda: callback(
                {"ts_event": 1_767_000_000_000_000_000, "price": "17000.00", "size": 1}
            )
        )
        thread.start()
        thread.join(timeout=1)

        assert runtime.snapshot() == before

        events = feed.events()
        assert isinstance(await anext(events), FeedStatus)
        trade = await anext(events)
        await feed.stop()
        await events.aclose()

        assert isinstance(trade, TradeEvent)
        assert trade.price_ticks == 68_000

    asyncio.run(run())


def test_events_iterator_drains_callback_arrival_order_and_exits_after_stop() -> None:
    async def run() -> None:
        FakeDatabentoSdk.clients = []
        feed = DatabentoMarketDataFeed(
            api_key="db-secret",
            requested_symbol="NQ.c.0",
            dataset="GLBX.MDP3",
            sdk_module=FakeDatabentoSdk,
        )
        await feed.start()
        callback = FakeDatabentoSdk.clients[0].callbacks[0]
        callback({"ts_event": 1_767_000_000_000_000_000, "price": "17000.00"})
        callback({"schema": "status", "event_ts_utc": "2026-01-05T14:30:01Z", "status": "open"})
        callback({"event_ts_utc": "2026-01-05T14:30:02Z", "bid_px": "16999.75"})

        events = feed.events()
        assert isinstance(await anext(events), FeedStatus)
        drained = [await anext(events), await anext(events), await anext(events)]
        await feed.stop()

        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(events), timeout=0.5)

        assert [type(item) for item in drained] == [
            TradeEvent,
            MarketStatusEvent,
            TopOfBookEvent,
        ]
        assert [item.event_ts_utc.second for item in drained] == [0, 1, 2]

    asyncio.run(run())


def test_databento_callback_after_stop_is_ignored_without_enqueueing() -> None:
    async def run() -> None:
        FakeDatabentoSdk.clients = []
        feed = DatabentoMarketDataFeed(
            api_key="db-secret",
            requested_symbol="NQ.c.0",
            dataset="GLBX.MDP3",
            sdk_module=FakeDatabentoSdk,
        )
        await feed.start()
        callback = FakeDatabentoSdk.clients[0].callbacks[0]
        await feed.stop()

        callback({"ts_event": 1_767_000_000_000_000_000, "price": "17000.00"})
        await asyncio.sleep(0)

        events = feed.events()
        status = await anext(events)

        assert isinstance(status, FeedStatus)
        assert status.state == FeedConnectionState.DISCONNECTED
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(events), timeout=0.1)

    asyncio.run(run())


def test_idle_events_iterator_waits_without_busy_spinning_and_stops_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        FakeDatabentoSdk.clients = []
        feed = DatabentoMarketDataFeed(
            api_key="db-secret",
            requested_symbol="NQ.c.0",
            dataset="GLBX.MDP3",
            sdk_module=FakeDatabentoSdk,
        )
        await feed.start()
        calls = 0
        original_get = feed._queue.get

        async def counted_get(*args: object, **kwargs: object) -> object:
            nonlocal calls
            calls += 1
            return await original_get(*args, **kwargs)

        monkeypatch.setattr(feed._queue, "get", counted_get)
        events = feed.events()
        assert isinstance(await anext(events), FeedStatus)
        pending = asyncio.create_task(anext(events))
        await asyncio.sleep(0.06)
        await feed.stop()

        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(pending, timeout=0.5)

        assert calls <= 2

    asyncio.run(run())


def test_databento_adapter_allows_explicit_raw_symbol_stype() -> None:
    async def run() -> None:
        FakeDatabentoSdk.clients = []
        feed = DatabentoMarketDataFeed(
            api_key="db-secret",
            requested_symbol="NQZ6",
            dataset="GLBX.MDP3",
            stype_in="raw_symbol",
            sdk_module=FakeDatabentoSdk,
        )

        await feed.start()
        client = FakeDatabentoSdk.clients[0]
        await feed.stop()

        assert {subscription["stype_in"] for subscription in client.subscriptions} == {"raw_symbol"}

    asyncio.run(run())


def test_databento_callback_trade_and_bbo_flow_to_canonical_events() -> None:
    async def run() -> None:
        FakeDatabentoSdk.clients = []
        feed = DatabentoMarketDataFeed(
            api_key="db-secret",
            requested_symbol="NQ.c.0",
            dataset="GLBX.MDP3",
            sdk_module=FakeDatabentoSdk,
        )
        await feed.start()
        callback = FakeDatabentoSdk.clients[0].callbacks[0]
        assert callable(callback)
        callback({"ts_event": 1_767_000_000_000_000_000, "price": "17000.00", "size": 2})
        callback(
            {
                "event_ts_utc": "2026-01-05T14:30:01Z",
                "bid_px": "16999.75",
                "bid_sz": 3,
                "ask_px": "17000.00",
                "ask_sz": 4,
            }
        )

        events = feed.events()
        assert isinstance(await anext(events), FeedStatus)
        trade = await anext(events)
        quote = await anext(events)
        await feed.stop()
        await events.aclose()

        assert isinstance(trade, TradeEvent)
        assert trade.price_ticks == 68_000
        assert isinstance(quote, TopOfBookEvent)
        assert quote.bid_price_ticks == 67_999

    asyncio.run(run())


def test_databento_queue_overflow_is_bounded_and_warns_without_secret() -> None:
    async def run() -> None:
        FakeDatabentoSdk.clients = []
        feed = DatabentoMarketDataFeed(
            api_key="db-secret",
            requested_symbol="NQ.c.0",
            dataset="GLBX.MDP3",
            queue_maxsize=1,
            sdk_module=FakeDatabentoSdk,
        )
        await feed.start()
        callback = FakeDatabentoSdk.clients[0].callbacks[0]
        assert callable(callback)
        callback({"ts_event": 1_767_000_000_000_000_000, "price": "17000.00"})
        callback({"ts_event": 1_767_000_000_000_000_001, "price": "17000.25"})

        events = feed.events()
        assert isinstance(await anext(events), FeedStatus)
        warning = await anext(events)
        await feed.stop()
        await events.aclose()

        assert isinstance(warning, DataQualityWarning)
        assert warning.code == DataQualityCode.BACKPRESSURE_DROP
        assert warning.metadata["dropped"] == 1
        assert "db-secret" not in str(warning)

    asyncio.run(run())


def test_databento_partial_start_failure_stops_fake_client() -> None:
    class FailingClient(FakeDatabentoClient):
        def start(self) -> None:
            raise RuntimeError("boom")

    class FailingSdk:
        clients: ClassVar[list[FailingClient]] = []

        class Live(FailingClient):
            def __init__(self, key: str) -> None:
                super().__init__(key)
                FailingSdk.clients.append(self)

    async def run() -> None:
        feed = DatabentoMarketDataFeed(
            api_key="db-secret",
            requested_symbol="NQ.c.0",
            dataset="GLBX.MDP3",
            sdk_module=FailingSdk,
        )
        with pytest.raises(RuntimeError, match="boom"):
            await feed.start()
        assert FailingSdk.clients[0].stopped is True

    asyncio.run(run())


def test_databento_normalizers_convert_fake_trade_and_bbo_messages() -> None:
    trade = normalize_provider_message(
        {
            "ts_event": 1_767_000_000_000_000_000,
            "instrument_id": "123",
            "symbol": "NQM6",
            "price": "17000.00",
            "size": "2",
            "side": "B",
        },
        requested_symbol="NQ.c.0",
        schema="trades",
    )
    quote = normalize_provider_message(
        {
            "event_ts_utc": "2026-01-05T14:30:00Z",
            "instrument_id": 123,
            "bid_px": "16999.75",
            "bid_sz": 3,
            "ask_px": "17000.00",
            "ask_sz": 4,
        },
        requested_symbol="NQ.c.0",
        schema="mbp-1",
    )

    assert isinstance(trade, TradeEvent)
    assert trade.price_ticks == 68_000
    assert trade.event_ts_utc.tzinfo == UTC
    assert isinstance(quote, TopOfBookEvent)
    assert quote.bid_price_ticks == 67_999
    assert quote.ask_price_ticks == 68_000


def test_databento_normalizers_convert_fixed_precision_prices_without_rounding() -> None:
    timestamp = "2026-01-05T14:30:00Z"
    trade = normalize_provider_message(
        {"event_ts_utc": timestamp, "price": 17_000_000_000_000, "size": 1},
        requested_symbol="NQ.c.0",
        schema="trades",
    )
    quote = normalize_provider_message(
        {
            "event_ts_utc": timestamp,
            "bid_px": 16_999_750_000_000,
            "ask_px": 17_000_000_000_000,
        },
        requested_symbol="NQ.c.0",
        schema="mbp-1",
    )
    statistic = normalize_provider_message(
        {"event_ts_utc": timestamp, "stat_type": "high", "price": 17_000_250_000_000},
        requested_symbol="NQ.c.0",
        schema="statistics",
    )

    assert isinstance(trade, TradeEvent)
    assert trade.price_ticks == 68_000
    assert isinstance(quote, TopOfBookEvent)
    assert quote.bid_price_ticks == 67_999
    assert quote.ask_price_ticks == 68_000
    assert isinstance(statistic, DailyStatisticEvent)
    assert statistic.price_ticks == 68_001

    with pytest.raises(ValueError, match="not divisible"):
        normalize_provider_message(
            {"event_ts_utc": timestamp, "price": 17_000_125_000_000},
            requested_symbol="NQ.c.0",
            schema="trades",
        )


def test_databento_normalizer_prefers_pretty_price_fields() -> None:
    trade = normalize_provider_message(
        {
            "event_ts_utc": "2026-01-05T14:30:00Z",
            "price": 17_000_000_000_000,
            "pretty_px": "17000.25",
        },
        requested_symbol="NQ.c.0",
        schema="trades",
    )

    assert isinstance(trade, TradeEvent)
    assert trade.price_ticks == 68_001


def test_trade_normalizer_accepts_multiple_timestamp_and_price_shapes() -> None:
    class ObjectTradeRecord:
        timestamp = datetime(2026, 1, 5, 9, 30)
        receive_ts_utc = datetime(2026, 1, 5, 14, 30, 1, tzinfo=UTC)
        px = Decimal("17000.25")
        qty = "3"
        aggressor_side = "sell"
        raw_symbol = "NQM6"

    integer_tick_trade = normalize_provider_message(
        {
            "event_ts_utc": datetime(2026, 1, 5, 14, 30, tzinfo=UTC),
            "price_ticks": 68_002,
            "size": 1,
        },
        requested_symbol="NQ.c.0",
        schema="trade",
    )
    object_trade = normalize_provider_message(
        ObjectTradeRecord(), requested_symbol="NQ.c.0", schema="trades"
    )

    assert isinstance(integer_tick_trade, TradeEvent)
    assert integer_tick_trade.price_ticks == 68_002
    assert integer_tick_trade.event_ts_utc.tzinfo == UTC
    assert isinstance(object_trade, TradeEvent)
    assert object_trade.event_ts_utc == datetime(2026, 1, 5, 9, 30, tzinfo=UTC)
    assert object_trade.receive_ts_utc == datetime(2026, 1, 5, 14, 30, 1, tzinfo=UTC)
    assert object_trade.price_ticks == 68_001
    assert object_trade.size == 3


def test_databento_normalizers_convert_definition_status_and_statistics() -> None:
    timestamp = "2026-01-05T14:30:00Z"

    definition = normalize_provider_message(
        {"event_ts_utc": timestamp, "instrument_id": 123, "symbol": "NQM6", "tick_size": "0.25"},
        requested_symbol="NQ.c.0",
        schema="definition",
    )
    status = normalize_provider_message(
        {
            "event_ts_utc": timestamp,
            "instrument_id": 123,
            "status": "halted",
            "reason": "test halt",
        },
        requested_symbol="NQ.c.0",
        schema="status",
    )
    statistic = normalize_provider_message(
        {
            "event_ts_utc": timestamp,
            "instrument_id": 123,
            "stat_type": "settlement",
            "price": "17001.25",
        },
        requested_symbol="NQ.c.0",
        schema="statistics",
    )

    assert isinstance(definition, InstrumentDefinitionEvent)
    assert definition.raw_symbol == "NQM6"
    assert definition.tick_size == Decimal("0.25")
    assert isinstance(status, MarketStatusEvent)
    assert status.status == MarketStatus.HALTED
    assert status.reason == "test halt"
    assert isinstance(statistic, DailyStatisticEvent)
    assert statistic.price_ticks == 68_005


def test_invalid_provider_prices_timestamps_and_schemas_fail_safely() -> None:
    with pytest.raises(ValueError, match="missing event timestamp"):
        normalize_provider_message(
            {"price": "17000.00"}, requested_symbol="NQ.c.0", schema="trades"
        )
    with pytest.raises(ValueError, match="missing price"):
        normalize_provider_message(
            {"event_ts_utc": "2026-01-05T14:30:00Z"},
            requested_symbol="NQ.c.0",
            schema="trades",
        )
    with pytest.raises(ValueError, match=r"Invalid isoformat|month must be"):
        normalize_provider_message(
            {"event_ts_utc": "not-a-time", "price": "17000.00"},
            requested_symbol="NQ.c.0",
            schema="trades",
        )
    with pytest.raises(ValueError, match="unsupported Databento schema"):
        normalize_provider_message(
            {"event_ts_utc": "2026-01-05T14:30:00Z"},
            requested_symbol="NQ.c.0",
            schema="mbo",
        )


def test_invalid_callback_records_become_safe_warnings_without_crashing() -> None:
    async def run() -> None:
        FakeDatabentoSdk.clients = []
        feed = DatabentoMarketDataFeed(
            api_key="db-secret",
            requested_symbol="NQ.c.0",
            dataset="GLBX.MDP3",
            sdk_module=FakeDatabentoSdk,
        )
        await feed.start()
        callback = FakeDatabentoSdk.clients[0].callbacks[0]
        callback({"event_ts_utc": "not-a-time", "price": "17000.00"})
        callback({"ts_event": 1_767_000_000_000_000_000, "price": "not-a-price"})
        callback({"event_ts_utc": "2026-01-05T14:30:00Z", "mystery": "record"})
        callback(RuntimeError("provider failure api_key=db-secret"))

        events = feed.events()
        assert isinstance(await anext(events), FeedStatus)
        warnings = [
            await anext(events),
            await anext(events),
            await anext(events),
            await anext(events),
        ]
        await feed.stop()
        await events.aclose()

        assert [warning.code for warning in warnings] == [
            DataQualityCode.INVALID_TIMESTAMP,
            DataQualityCode.INVALID_PRICE,
            DataQualityCode.UNSUPPORTED_SCHEMA,
            DataQualityCode.UNSUPPORTED_SCHEMA,
        ]
        assert all(isinstance(warning, DataQualityWarning) for warning in warnings)
        assert "db-secret" not in str(warnings)

    asyncio.run(run())


def test_databento_control_records_do_not_emit_normalization_warnings_or_block_stream() -> None:
    class SystemMsg:
        def __init__(self, is_heartbeat: bool) -> None:
            self.is_heartbeat = is_heartbeat

    class SymbolMappingMsg:
        pass

    async def run() -> None:
        FakeDatabentoSdk.clients = []
        feed = DatabentoMarketDataFeed(
            api_key="db-secret",
            requested_symbol="NQ.c.0",
            dataset="GLBX.MDP3",
            sdk_module=FakeDatabentoSdk,
        )
        await feed.start()
        callback = FakeDatabentoSdk.clients[0].callbacks[0]
        callback(SystemMsg(is_heartbeat=True))
        callback(SystemMsg(is_heartbeat=False))
        callback(SymbolMappingMsg())
        callback({"ts_event": 1_767_000_000_000_000_000, "price": "17000.00", "size": 2})

        events = feed.events()
        assert isinstance(await anext(events), FeedStatus)
        trade = await anext(events)
        pending = asyncio.create_task(anext(events))
        await asyncio.sleep(0.05)
        await feed.stop()

        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(pending, timeout=0.5)
        await events.aclose()

        assert isinstance(trade, TradeEvent)
        assert trade.price_ticks == 68_000

    asyncio.run(run())


def test_databento_error_record_emits_specific_sanitized_warning() -> None:
    class ErrorMsg:
        code = "bad_request"
        message = "provider rejected api_key=db-secret path=C:\\secret\\file"
        schema = "18"

    async def run() -> None:
        FakeDatabentoSdk.clients = []
        feed = DatabentoMarketDataFeed(
            api_key="db-secret",
            requested_symbol="NQ.c.0",
            dataset="GLBX.MDP3",
            sdk_module=FakeDatabentoSdk,
        )
        await feed.start()
        FakeDatabentoSdk.clients[0].callbacks[0](ErrorMsg())

        events = feed.events()
        assert isinstance(await anext(events), FeedStatus)
        warning = await anext(events)
        await feed.stop()
        await events.aclose()

        assert isinstance(warning, DataQualityWarning)
        assert warning.code == DataQualityCode.PROVIDER_ERROR
        assert "code=bad_request" in warning.metadata["detail"]
        assert "<redacted>" in warning.metadata["detail"]
        assert "<path>" in warning.metadata["detail"]
        assert "schema" not in warning.metadata
        assert "db-secret" not in str(warning)
        assert "api_key" not in str(warning).lower()
        assert "C:\\secret\\file" not in str(warning)

    asyncio.run(run())


def test_databento_sdk_message_class_names_infer_supported_schemas_without_numeric_rtype() -> None:
    class TradeMsg:
        rtype = 0
        ts_event = 1_767_000_000_000_000_000
        price = "17000.00"
        size = 2

    class Level:
        bid_px = 16_999_750_000_000
        bid_sz = 3
        ask_px = 17_000_000_000_000
        ask_sz = 4

    class MBP1Msg:
        rtype = 1
        ts_event = 1_767_000_000_000_000_001
        levels: ClassVar[list[Level]] = [Level()]

    class BBOMsg:
        rtype = 18
        ts_event = 1_767_000_000_000_000_002
        bid_px = "17000.25"
        ask_px = "17000.50"

    class InstrumentDefMsg:
        rtype = 10
        ts_event = 1_767_000_000_000_000_003
        instrument_id = 123
        symbol = "NQM6"
        tick_size = "0.25"

    class StatusMsg:
        rtype = 12
        ts_event = 1_767_000_000_000_000_004
        status = "halted"

    class StatMsg:
        rtype = 13
        ts_event = 1_767_000_000_000_000_005
        stat_type = "high"
        price = "17001.00"

    async def run() -> None:
        FakeDatabentoSdk.clients = []
        feed = DatabentoMarketDataFeed(
            api_key="db-secret",
            requested_symbol="NQ.c.0",
            dataset="GLBX.MDP3",
            sdk_module=FakeDatabentoSdk,
        )
        await feed.start()
        callback = FakeDatabentoSdk.clients[0].callbacks[0]
        for record in (TradeMsg(), MBP1Msg(), BBOMsg(), InstrumentDefMsg(), StatusMsg(), StatMsg()):
            callback(record)

        events = feed.events()
        assert isinstance(await anext(events), FeedStatus)
        drained = [await anext(events) for _ in range(6)]
        await feed.stop()
        await events.aclose()

        assert [type(item) for item in drained] == [
            TradeEvent,
            TopOfBookEvent,
            TopOfBookEvent,
            InstrumentDefinitionEvent,
            MarketStatusEvent,
            DailyStatisticEvent,
        ]
        assert not any(isinstance(item, DataQualityWarning) for item in drained)
        assert "unsupported Databento schema: 18" not in str(drained)
        assert drained[1].bid_price_ticks == 67_999
        assert drained[1].bid_size == 3
        assert drained[1].ask_price_ticks == 68_000
        assert drained[1].ask_size == 4

    asyncio.run(run())


def test_databento_callback_ignores_numeric_schema_for_supported_sdk_record() -> None:
    class BBOMsg:
        rtype = 18
        ts_event = 1_767_000_000_000_000_000
        bid_px = "17000.25"
        ask_px = "17000.50"

    async def run() -> None:
        FakeDatabentoSdk.clients = []
        feed = DatabentoMarketDataFeed(
            api_key="db-secret",
            requested_symbol="NQ.c.0",
            dataset="GLBX.MDP3",
            sdk_module=FakeDatabentoSdk,
        )
        await feed.start()
        callback = FakeDatabentoSdk.clients[0].callbacks[0]
        callback(record=BBOMsg(), schema="18")

        events = feed.events()
        assert isinstance(await anext(events), FeedStatus)
        quote = await anext(events)
        await feed.stop()
        await events.aclose()

        assert isinstance(quote, TopOfBookEvent)
        assert quote.bid_price_ticks == 68_001
        assert quote.ask_price_ticks == 68_002
        assert "unsupported Databento schema: 18" not in str(quote)

    asyncio.run(run())


def test_databento_sdk_mbp1_levels_normalize_as_top_of_book() -> None:
    class Level:
        bid_px = 16_999_750_000_000
        bid_sz = 3
        ask_px = 17_000_000_000_000
        ask_sz = 4

    class MBP1Msg:
        ts_event = 1_767_000_000_000_000_000
        instrument_id = 123
        levels: ClassVar[list[Level]] = [Level()]

    quote = normalize_provider_message(MBP1Msg(), requested_symbol="NQ.c.0", schema="mbp-1")

    assert isinstance(quote, TopOfBookEvent)
    assert quote.bid_price_ticks == 67_999
    assert quote.ask_price_ticks == 68_000
    assert quote.bid_size == 3
    assert quote.ask_size == 4


def test_websocket_broadcaster_does_not_emit_raw_tick_messages() -> None:
    runtime = _runtime()
    update = runtime.process_market_event(
        TradeEvent(
            datetime(2026, 1, 5, 14, 30, tzinfo=UTC),
            None,
            1,
            "NQ.c.0",
            "NQM6",
            68_000,
            1,
            source_schema="trades",
        )
    )
    messages = [
        json.loads(message)
        for message in WebSocketBroadcaster(runtime).messages_for_update(update)
    ]

    message_types = {message["type"] for message in messages}
    payload_text = json.dumps(messages)
    assert "market.tick" not in message_types
    assert "trade" not in message_types
    assert "market.bar.updated" in message_types
    assert "source_schema" not in payload_text


def test_only_trade_events_increment_bars_in_runtime() -> None:
    runtime = _runtime()
    timestamp = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)

    for event in (
        TopOfBookEvent(timestamp, 1, 67_999, 3, 68_000, 4, source_schema="mbp-1"),
        InstrumentDefinitionEvent(timestamp, 1, "NQ.c.0", "NQM6", Decimal("0.25")),
        MarketStatusEvent(timestamp, 1, MarketStatus.OPEN, "open"),
        DailyStatisticEvent(timestamp, 1, "settlement", 68_000, source_schema="statistics"),
    ):
        runtime.process_market_event(event)

    assert runtime.snapshot().current_bars == ()

    runtime.process_market_event(TradeEvent(timestamp, None, 1, "NQ.c.0", "NQM6", 68_000, 1))

    assert runtime.snapshot().current_bars[0].trade_count == 1


def test_quote_bursts_update_internal_liveness_without_feed_status_fanout() -> None:
    runtime = _runtime()
    timestamp = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)

    updates = [
        runtime.process_market_event(
            TopOfBookEvent(timestamp, 1, 67_999, 3, 68_000, 4, source_schema="mbp-1")
        )
        for _ in range(25)
    ]

    assert updates[0].feed_status is not None
    assert all(not update.has_deltas() and update.feed_status is None for update in updates[1:])
    assert runtime.feed_status.state == FeedConnectionState.CONNECTED
    assert runtime.feed_status.schema == "mbp-1"


def test_live_errors_are_sanitized_in_status_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    class ExplodingFeed(FakeFeed):
        async def events(self) -> AsyncIterator[object]:
            raise RuntimeError("provider failed api_key=db-secret password=hunter2")
            yield  # pragma: no cover

    async def run() -> LiveMarketDataService:
        live = LiveMarketDataService(_runtime(), _live_config(), lambda _config: ExplodingFeed([]))
        await live.start()
        assert live._task is not None
        await live._task
        return live

    caplog.set_level(logging.ERROR)

    live = asyncio.run(run())

    assert live.status().state == LiveState.FAILED
    assert live.status().last_error == "RuntimeError"
    log_text = caplog.text
    assert "db-secret" not in log_text
    assert "hunter2" not in log_text


def test_sdk_missing_path_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object):
        if name == "databento":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(DatabentoUnavailableError, match="Databento SDK is not installed"):
        DatabentoMarketDataFeed(
            api_key="not-returned",
            requested_symbol="NQ.c.0",
            dataset="GLBX.MDP3",
        )
