import json
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import trade_lab.api.__main__ as api_main
from trade_lab.adapters.synthetic_replay import ReplaySourceDefinition
from trade_lab.api.app import create_app
from trade_lab.api.dto import MESSAGE_VERSION, empty_snapshot_payload, make_envelope
from trade_lab.config import Settings
from trade_lab.domain.events import TradeEvent
from trade_lab.domain.feed import FeedConnectionState, FeedStatus
from trade_lab.services.broadcaster import WebSocketBroadcaster
from trade_lab.services.replay import HistoricalReplayService, ReplayState, ReplayStatus
from trade_lab.services.runtime import ApplicationRuntime

SECRET_PATTERNS = ("super-secret", "secret-token", "databento_api_key", "token=", "password=")


def _assert_no_secret_text(payload: object) -> None:
    text = json.dumps(payload, default=str).lower()
    for pattern in SECRET_PATTERNS:
        assert pattern not in text
    assert "c:\\users" not in text
    assert "/users/" not in text


def _runtime_with_closed_bar() -> ApplicationRuntime:
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0", tick_timeframes=(2,), observation_duration_seconds=300
    )
    start = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    for i in range(3):
        runtime.process_market_event(
            TradeEvent(start + timedelta(seconds=i), None, 1, "NQ.c.0", "NQM6", 68_000 + i, 1)
        )
    return runtime


class FailingApiReplaySource:
    def scan(self, *args: object, **kwargs: object) -> Iterator[TradeEvent]:
        _ = (args, kwargs)
        raise RuntimeError("failed C:\\Users\\gonza\\secret\\raw.parquet token=abc123")
        yield


class ManualApiReplayService:
    def __init__(self, runtime: ApplicationRuntime) -> None:
        self.runtime = runtime
        self.state = ReplayState.IDLE
        self.messages: list[str] = []
        self.has_update_callback = False
        self.source_id = "synthetic:manual-test"

    def set_update_callback(self, callback: object) -> None:
        _ = callback
        self.has_update_callback = True

    def status(self) -> ReplayStatus:
        return ReplayStatus(
            state=self.state,
            events_processed=0,
            warnings_recorded=0,
            requested_symbol="NQ.c.0",
            schema="trades",
            source_id=self.source_id,
            source_label="Synthetic safe test source",
        )

    async def start(self, source: object, config: object) -> None:
        _ = (source, config)
        self.source_id = getattr(config, "source_id", self.source_id)
        self.state = ReplayState.RUNNING
        self._set_feed("runtime reset for historical replay", FeedConnectionState.DISCONNECTED)
        self._set_feed("historical replay loading", FeedConnectionState.REPLAYING)

    async def pause(self) -> None:
        self.state = ReplayState.PAUSED
        self._set_feed("historical replay paused", FeedConnectionState.REPLAYING)

    async def resume(self) -> None:
        self.state = ReplayState.RUNNING
        self._set_feed("historical replay resumed", FeedConnectionState.REPLAYING)

    async def stop(self) -> None:
        self.state = ReplayState.STOPPED
        self._set_feed("historical replay stopped", FeedConnectionState.DISCONNECTED)

    def _set_feed(self, message: str, state: FeedConnectionState) -> None:
        self.messages.append(message)
        self.runtime.set_feed_status(
            FeedStatus(
                state=state,
                mode="replay",
                requested_symbol="NQ.c.0",
                schema="trades",
                last_message=message,
            )
        )


def _api_source_definition(source_id: str) -> ReplaySourceDefinition:
    return ReplaySourceDefinition(
        source_id=source_id,
        label="Synthetic safe test source",
        requested_symbol="NQ.c.0",
        schema="trades",
    )


def test_health_and_status_do_not_expose_secrets() -> None:
    settings = Settings(_env_file=None, databento_api_key="secret", front_month_symbol="NQ.c.0")
    client = TestClient(create_app(settings))

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["service"] == "trade-lab-backend"

    status = client.get("/api/v1/status")
    assert status.status_code == 200
    payload = status.json()
    assert payload["requested_symbol"] == "NQ.c.0"
    assert payload["supported_tick_timeframes"] == [147, 987, 2000]
    assert payload["engine_ready"] is True
    _assert_no_secret_text(health.json())
    _assert_no_secret_text(payload)


def test_health_schema_is_minimal_and_stable() -> None:
    client = TestClient(create_app(Settings(_env_file=None)))

    response = client.get("/health")

    assert response.status_code == 200
    assert set(response.json()) == {"ok", "service", "version"}
    assert response.json()["ok"] is True
    assert response.json()["service"] == "trade-lab-backend"


def test_status_includes_runtime_fields_and_supported_timeframes_without_secrets() -> None:
    settings = Settings(
        _env_file=None,
        databento_api_key="super-secret-key",
        front_month_symbol="NQ.c.0",
        instrument_root="NQ",
        tick_timeframes=(147, 987, 2000),
    )
    client = TestClient(create_app(settings))

    response = client.get("/api/v1/status")

    payload = response.json()

    assert response.status_code == 200
    assert payload == {
        "service": "trade-lab-backend",
        "version": payload["version"],
        "runtime_mode": "idle",
        "requested_symbol": "NQ.c.0",
        "instrument_root": "NQ",
        "supported_tick_timeframes": [147, 987, 2000],
        "engine_ready": True,
        "feed_ready": False,
        "feed_state": "disconnected",
        "session": None,
        "trading_day": None,
        "replay": {
            "state": "idle",
            "events_processed": 0,
            "warnings_recorded": 0,
            "last_event_ts_utc": None,
            "last_error": None,
            "requested_symbol": None,
            "schema": None,
        },
        "live": {
            "state": "idle",
            "requested_symbol": "NQ.c.0",
            "dataset": "GLBX.MDP3",
            "schemas": ["trades", "mbp-1", "definition", "status", "statistics"],
            "api_key_configured": True,
            "enabled": False,
            "sdk_available": payload["live"]["sdk_available"],
            "subscription_ready": False,
            "events_processed": 0,
            "last_event_ts_utc": None,
            "last_error": None,
            "started_at_utc": None,
            "stopped_at_utc": None,
        },
    }
    _assert_no_secret_text(payload)


def test_status_and_replay_status_include_runtime_replay_shape_without_paths_or_secrets() -> None:
    settings = Settings(
        _env_file=None, databento_api_key="secret-token", front_month_symbol="NQ.c.0"
    )
    runtime = _runtime_with_closed_bar()
    replay = HistoricalReplayService(runtime)
    client = TestClient(create_app(settings, runtime=runtime, replay=replay))

    status = client.get("/api/v1/status")
    replay_status = client.get("/api/v1/replay/status")

    assert status.status_code == 200
    assert replay_status.status_code == 200
    assert set(status.json()["replay"]) == {
        "state",
        "events_processed",
        "warnings_recorded",
        "last_event_ts_utc",
        "last_error",
        "requested_symbol",
        "schema",
    }
    assert replay_status.json() == status.json()["replay"]
    _assert_no_secret_text(status.json())
    _assert_no_secret_text(replay_status.json())


def test_app_factory_mounts_expected_routes_and_stores_settings() -> None:
    settings = Settings(_env_file=None, front_month_symbol="NQ.c.0")

    app = create_app(settings)
    route_paths = {getattr(route, "path", None) for route in app.routes}

    assert app.state.settings is settings
    assert "/health" in route_paths
    assert "/api/v1/status" in route_paths
    assert "/api/v1/replay/status" in route_paths
    assert "/api/v1/replay/sources" in route_paths
    assert "/api/v1/replay/start" in route_paths
    assert "/api/v1/live/status" in route_paths
    assert "/api/v1/live/start" in route_paths
    assert "/api/v1/live/stop" in route_paths
    assert "/ws/v1" in route_paths


def test_cors_allows_configured_local_dev_origin_for_preflight_and_rest() -> None:
    client = TestClient(create_app(Settings(_env_file=None)))

    preflight = client.options(
        "/api/v1/status",
        headers={
            "Origin": "http://localhost:5174",
            "Access-Control-Request-Method": "GET",
        },
    )
    actual = client.get("/api/v1/status", headers={"Origin": "http://localhost:5174"})

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:5174"
    assert "access-control-allow-credentials" not in preflight.headers
    assert actual.status_code == 200
    assert actual.headers["access-control-allow-origin"] == "http://localhost:5174"


def test_cors_rejects_disallowed_browser_origin() -> None:
    client = TestClient(create_app(Settings(_env_file=None)))

    preflight = client.options(
        "/api/v1/status",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    actual = client.get("/api/v1/status", headers={"Origin": "https://evil.example"})

    assert preflight.status_code == 400
    assert "access-control-allow-origin" not in preflight.headers
    assert actual.status_code == 200
    assert "access-control-allow-origin" not in actual.headers


def test_replay_sources_returns_allowlisted_safe_ids_without_paths_or_secrets() -> None:
    client = TestClient(create_app(Settings(_env_file=None, databento_api_key="secret")))

    response = client.get("/api/v1/replay/sources")

    assert response.status_code == 200
    payload = response.json()
    assert payload["sources"]
    assert payload["sources"][0]["source_id"] == "synthetic:nq-demo"
    assert set(payload["sources"][0]) == {
        "source_id",
        "label",
        "requested_symbol",
        "schema",
        "kind",
        "session_label",
        "availability",
    }
    assert payload["sources"][0]["kind"] == "synthetic"
    assert payload["historical"]["available"] is False
    assert payload["historical"]["diagnostics"]["data_path_configured"] is False
    assert "parquet_files_inspected" in payload["historical"]["diagnostics"]
    _assert_no_secret_text(payload)


def test_replay_start_rejects_unknown_and_path_like_source_ids_without_leakage() -> None:
    client = TestClient(create_app(Settings(_env_file=None)))

    unknown = client.post("/api/v1/replay/start", json={"source_id": "synthetic:missing"})
    path_like = [
        "C:\\Users\\raw.parquet",
        "C:/Users/raw.parquet",
        "C:raw.parquet",
        "..\\raw",
        "../raw",
        "synthetic/../nq-demo",
        "synthetic nq-demo",
    ]
    responses = [
        client.post("/api/v1/replay/start", json={"source_id": value}) for value in path_like
    ]

    assert unknown.status_code == 404
    assert [response.status_code for response in responses] == [400] * len(path_like)
    _assert_no_secret_text(unknown.json())
    for response in responses:
        _assert_no_secret_text(response.json())


def test_replay_start_rejects_too_long_source_id_at_request_boundary() -> None:
    client = TestClient(create_app(Settings(_env_file=None)))

    response = client.post("/api/v1/replay/start", json={"source_id": "s" * 129})

    assert response.status_code == 422
    _assert_no_secret_text(response.json())


def test_replay_control_api_pause_resume_stop_transitions_emit_feed_statuses() -> None:
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0", tick_timeframes=(147,), observation_duration_seconds=300
    )
    replay = ManualApiReplayService(runtime)
    app = create_app(Settings(_env_file=None), runtime=runtime, replay=replay)
    app.state.replay_sources = {
        "synthetic:slow-test": (_api_source_definition("synthetic:slow-test"), object())
    }
    client = TestClient(app)

    start = client.post(
        "/api/v1/replay/start", json={"source_id": "synthetic:slow-test", "speed": 1}
    )
    pause = client.post("/api/v1/replay/pause")
    resume = client.post("/api/v1/replay/resume")
    stop = client.post("/api/v1/replay/stop")

    assert start.status_code == pause.status_code == resume.status_code == stop.status_code == 200
    assert start.json()["source_id"] == "synthetic:slow-test"
    assert pause.json()["state"] == "paused"
    assert resume.json()["state"] == "running"
    assert stop.json()["state"] == "stopped"
    assert {
        "runtime reset for historical replay",
        "historical replay loading",
        "historical replay paused",
        "historical replay resumed",
    } <= set(replay.messages)
    assert replay.messages[-1] == "historical replay stopped"
    _assert_no_secret_text(
        [start.json(), pause.json(), resume.json(), stop.json(), replay.messages]
    )


def test_replay_control_api_failure_status_is_sanitized() -> None:
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0", tick_timeframes=(147,), observation_duration_seconds=300
    )
    replay = HistoricalReplayService(runtime)
    app = create_app(Settings(_env_file=None), runtime=runtime, replay=replay)
    app.state.replay_sources = {
        "synthetic:failing-test": (
            _api_source_definition("synthetic:failing-test"),
            FailingApiReplaySource(),
        )
    }
    # Keep the TestClient portal alive while the background replay task raises;
    # otherwise Starlette may cancel the task between per-request portals and report
    # a cancellation instead of the sanitized failure this test is asserting.
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/replay/start", json={"source_id": "synthetic:failing-test"}
        )
        for _ in range(200):
            if replay.status().state.value == "failed":
                break
            time.sleep(0.001)
        status = client.get("/api/v1/replay/status")

    assert response.status_code == 200
    assert status.status_code == 200
    assert status.json()["state"] == "failed"
    assert status.json()["last_error"] == "RuntimeError"
    assert runtime.snapshot().feed_status.last_message == "historical replay failed"
    _assert_no_secret_text(status.json())


def test_replay_start_allowed_synthetic_source_processes_runtime_domain_deltas() -> None:
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0", tick_timeframes=(147,), observation_duration_seconds=300
    )
    replay = HistoricalReplayService(runtime)
    broadcaster = WebSocketBroadcaster(runtime)
    app = create_app(
        Settings(_env_file=None), runtime=runtime, replay=replay, broadcaster=broadcaster
    )
    # Enter the app lifespan so the background replay task keeps running while we poll;
    # a plain TestClient tears its portal down per request and cancels replay early,
    # which (now that replay coalesces broadcasts on an interval) leaves no deltas to see.
    with TestClient(app) as client:
        response = client.post("/api/v1/replay/start", json={"source_id": "synthetic:nq-demo"})
        assert response.status_code == 200

        for _ in range(500):
            if replay.status().state.value == "completed":
                break
            time.sleep(0.001)

        seen = {
            message["type"]
            for update in list(replay.updates._queue)
            for message in (json.loads(raw) for raw in broadcaster.messages_for_update(update))
        }

        assert {"feed.status", "market.bar.updated", "levels.updated"} <= seen
        assert response.json()["source_id"] == "synthetic:nq-demo"
        _assert_no_secret_text(response.json())


def test_app_factory_accepts_injected_runtime_replay_and_broadcaster() -> None:
    settings = Settings(_env_file=None, front_month_symbol="NQ.c.0")
    runtime = _runtime_with_closed_bar()
    replay = HistoricalReplayService(runtime)
    broadcaster = WebSocketBroadcaster(runtime)

    app = create_app(settings, runtime=runtime, replay=replay, broadcaster=broadcaster)

    assert app.state.runtime is runtime
    assert app.state.replay is replay
    assert app.state.broadcaster is broadcaster
    assert replay.has_update_callback


def test_api_module_entrypoint_imports_and_invokes_uvicorn_with_factory(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(app_path: str, **kwargs: object) -> None:
        calls.append({"app_path": app_path, **kwargs})

    monkeypatch.setattr(api_main.uvicorn, "run", fake_run)
    monkeypatch.setattr(
        api_main, "load_settings", lambda: Settings(_env_file=None, backend_port=8765)
    )

    api_main.main()

    assert calls == [
        {
            "app_path": "trade_lab.api.app:create_app",
            "factory": True,
            "host": "127.0.0.1",
            "port": 8765,
        }
    ]


def test_websocket_sends_versioned_snapshot_and_heartbeat() -> None:
    settings = Settings(_env_file=None, front_month_symbol="NQ.c.0")
    client = TestClient(create_app(settings))

    with client.websocket_connect("/ws/v1") as websocket:
        snapshot = json.loads(websocket.receive_bytes())
        heartbeat = json.loads(websocket.receive_bytes())

    assert snapshot["version"] == "ws.v1"
    assert snapshot["type"] == "system.snapshot"
    assert snapshot["sequence"] == 1
    assert snapshot["payload"]["feed_status"]["requested_symbol"] == "NQ.c.0"
    assert snapshot["payload"]["current_bars"] == []
    assert heartbeat["type"] == "system.heartbeat"
    assert heartbeat["sequence"] == 2


def test_websocket_accepts_configured_browser_origin() -> None:
    client = TestClient(create_app(Settings(_env_file=None)))

    with client.websocket_connect(
        "/ws/v1", headers={"origin": "http://localhost:5174"}
    ) as websocket:
        snapshot = json.loads(websocket.receive_bytes())

    assert snapshot["type"] == "system.snapshot"


def test_websocket_rejects_disallowed_browser_origin_before_accept() -> None:
    client = TestClient(create_app(Settings(_env_file=None)))

    with pytest.raises(WebSocketDisconnect) as exc_info, client.websocket_connect(
        "/ws/v1", headers={"origin": "https://evil.example"}
    ):
        pass

    assert exc_info.value.code == 1008


def test_websocket_allows_no_origin_non_browser_test_clients() -> None:
    client = TestClient(create_app(Settings(_env_file=None)))

    with client.websocket_connect("/ws/v1") as websocket:
        snapshot = json.loads(websocket.receive_bytes())

    assert snapshot["type"] == "system.snapshot"


def test_websocket_snapshot_contract_is_versioned_deterministic_and_safe() -> None:
    settings = Settings(_env_file=None, databento_api_key="secret", front_month_symbol="NQ.c.0")
    client = TestClient(create_app(settings))

    with client.websocket_connect("/ws/v1") as websocket:
        snapshot = json.loads(websocket.receive_bytes())
        heartbeat = json.loads(websocket.receive_bytes())
        websocket.close()

    assert list(snapshot) == ["payload", "sequence", "server_time_utc", "type", "version"]
    assert snapshot["version"] == MESSAGE_VERSION
    assert snapshot["type"] == "system.snapshot"
    assert snapshot["sequence"] == 1
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T.*(Z|\+00:00)", snapshot["server_time_utc"])
    assert set(snapshot["payload"]) == {
        "current_bars",
        "recent_closed_bars",
        "display_levels",
        "active_observations",
        "feed_status",
        "warnings",
        "predictions",
        "outcomes",
        "dropped",
        "model_status",
        "session",
        "trading_day",
    }
    assert snapshot["payload"]["current_bars"] == []
    assert snapshot["payload"]["recent_closed_bars"] == []
    assert snapshot["payload"]["display_levels"] == []
    assert snapshot["payload"]["active_observations"] == []
    assert snapshot["payload"]["warnings"] == []
    assert snapshot["payload"]["predictions"] == []
    assert snapshot["payload"]["outcomes"] == []
    assert snapshot["payload"]["dropped"] == []
    assert snapshot["payload"]["model_status"] == {
        "loaded": False,
        "model_id": None,
        "strategy_id": None,
        "training_mode": None,
        "instrument": None,
        "feature_names": [],
        "class_map": {},
        "validation_ok": False,
        "validation_detail": None,
    }
    assert snapshot["payload"]["session"] is None
    assert snapshot["payload"]["trading_day"] is None
    assert snapshot["payload"]["feed_status"] == {
        "state": "disconnected",
        "mode": "idle",
        "requested_symbol": "NQ.c.0",
        "raw_symbol": None,
        "dataset": None,
        "schema": None,
        "last_event_ts_utc": None,
            "last_message": "Market-data feed is not started.",
        "metadata": {},
    }
    assert heartbeat["version"] == MESSAGE_VERSION
    assert heartbeat["type"] == "system.heartbeat"
    assert heartbeat["payload"] == {"status": "ok"}
    assert heartbeat["sequence"] > snapshot["sequence"]
    _assert_no_secret_text(snapshot)
    _assert_no_secret_text(heartbeat)


def test_websocket_snapshot_includes_recent_closed_bars_runtime_fields_and_heartbeat() -> None:
    runtime = _runtime_with_closed_bar()
    client = TestClient(
        create_app(
            Settings(_env_file=None, front_month_symbol="NQ.c.0"),
            runtime=runtime,
            replay=HistoricalReplayService(runtime),
            broadcaster=WebSocketBroadcaster(runtime),
        )
    )

    with client.websocket_connect("/ws/v1") as websocket:
        snapshot = json.loads(websocket.receive_bytes())
        heartbeat = json.loads(websocket.receive_bytes())

    assert snapshot["payload"]["current_bars"]
    assert snapshot["payload"]["recent_closed_bars"]
    assert snapshot["payload"]["current_bars"][0]["bar_index"] == 1
    assert snapshot["payload"]["current_bars"][0]["bar_id"] == "2t:2026-01-05:1"
    assert snapshot["payload"]["recent_closed_bars"][0]["bar_index"] == 0
    assert snapshot["payload"]["recent_closed_bars"][0]["bar_id"] == "2t:2026-01-05:0"
    assert snapshot["payload"]["display_levels"]
    assert snapshot["payload"]["feed_status"]["state"] in {"replaying", "connected"}
    assert heartbeat["type"] == "system.heartbeat"
    assert heartbeat["sequence"] == snapshot["sequence"] + 1


def test_websocket_sequence_is_monotonic_per_app_instance() -> None:
    client = TestClient(create_app(Settings(_env_file=None)))

    sequences: list[int] = []
    for _ in range(2):
        with client.websocket_connect("/ws/v1") as websocket:
            sequences.append(json.loads(websocket.receive_bytes())["sequence"])
            sequences.append(json.loads(websocket.receive_bytes())["sequence"])

    assert sequences == [1, 2, 3, 4]


def test_websocket_handles_client_message_and_clean_disconnect() -> None:
    client = TestClient(create_app(Settings(_env_file=None)))

    with client.websocket_connect("/ws/v1") as websocket:
        websocket.receive_bytes()
        websocket.receive_bytes()
        websocket.send_text("client-ping")
        websocket.close()


def test_empty_snapshot_payload_has_required_shape() -> None:
    payload = empty_snapshot_payload(requested_symbol="NQ.c.0").model_dump(mode="json")

    assert set(payload) == {
        "current_bars",
        "recent_closed_bars",
        "display_levels",
        "active_observations",
        "feed_status",
        "warnings",
        "predictions",
        "outcomes",
        "dropped",
        "model_status",
        "session",
        "trading_day",
    }


def test_make_envelope_has_required_fields() -> None:
    envelope = make_envelope("system.heartbeat", 7, {"status": "ok"})

    assert set(envelope) == {"version", "type", "sequence", "server_time_utc", "payload"}
    assert envelope["version"] == MESSAGE_VERSION
    assert envelope["type"] == "system.heartbeat"
    assert envelope["sequence"] == 7
    assert envelope["payload"] == {"status": "ok"}
