"""Regression coverage for audit findings NN-2 and NN-6.

NN-2: live and replay share one ApplicationRuntime, and starting either one calls
``runtime.reset()`` to rebuild the engine. Starting one while the other is active would
run that reset underneath the still-writing task and corrupt bars/levels/touches, so the
start endpoints must enforce mutual exclusion (HTTP 409).

NN-6: the replay control endpoints are operator side effects on the shared runtime and
must be gated with ``_authorize_live_control`` exactly like ``/api/v1/live/start``.
"""

import asyncio
from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from trade_lab.api.app import create_app
from trade_lab.config import Settings
from trade_lab.services.live import LiveConfig, LiveMarketDataService, LiveState
from trade_lab.services.replay import ReplayState, ReplayStatus
from trade_lab.services.runtime import ApplicationRuntime

REPLAY_CONTROL_ROUTES = (
    "/api/v1/replay/start",
    "/api/v1/replay/pause",
    "/api/v1/replay/resume",
    "/api/v1/replay/stop",
)


class _BlockingFeed:
    """Feed that never completes so the live service stays RUNNING during the test.

    The runtime cancels the events iterator when ``live.stop()`` runs at teardown.
    """

    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def events(self) -> AsyncIterator[object]:
        await asyncio.Event().wait()
        if False:  # pragma: no cover - keeps this method an async generator
            yield object()


class _StubReplay:
    """Minimal replay double that reports a chosen state via the real status() accessor."""

    def __init__(self, state: ReplayState) -> None:
        self._state = state
        # Pretend a callback is already wired so create_app does not try to attach one.
        self.has_update_callback = True

    def set_update_callback(self, callback: object) -> None:  # pragma: no cover - unused
        _ = callback

    def status(self) -> ReplayStatus:
        return ReplayStatus(state=self._state, events_processed=0, warnings_recorded=0)


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
        context_schemas=("definition", "status", "statistics"),
        api_key_configured=True,
        enabled=True,
    )


def test_replay_start_rejected_with_409_while_live_feed_is_active() -> None:
    # audit #NN-2 (a): replay must not reset/rebuild the engine while live is writing.
    runtime = _runtime()
    live = LiveMarketDataService(runtime, _live_config(), lambda _config: _BlockingFeed())
    app = create_app(Settings(_env_file=None), runtime=runtime, live=live)

    with TestClient(app) as client:
        start = client.post("/api/v1/live/start")
        assert start.status_code == 200
        assert start.json()["state"] in {LiveState.CONNECTING.value, LiveState.RUNNING.value}

        replay = client.post("/api/v1/replay/start", json={"source_id": "synthetic:nq-demo"})

        # Clean up the still-running live task before the test client portal closes.
        client.post("/api/v1/live/stop")

    assert replay.status_code == 409
    assert replay.json()["detail"] == "cannot start replay while live feed is active"


def test_live_start_rejected_with_409_while_replay_is_active() -> None:
    # audit #NN-2 (symmetric): live must not reset/rebuild the engine while replay runs.
    app = create_app(Settings(_env_file=None), replay=_StubReplay(ReplayState.RUNNING))
    client = TestClient(app)

    response = client.post("/api/v1/live/start")

    assert response.status_code == 409
    assert response.json()["detail"] == "cannot start live feed while replay is active"


def test_replay_control_endpoints_reject_remote_without_operator_token() -> None:
    # audit #NN-6 (b): every replay control is gated like live_start. A non-loopback
    # caller with no operator token must be rejected before any runtime side effect.
    app = create_app(Settings(_env_file=None, operator_token="operator-secret"))
    remote = TestClient(app, client=("203.0.113.10", 12345))

    for route in REPLAY_CONTROL_ROUTES:
        response = remote.post(route, json={"source_id": "synthetic:nq-demo"})
        assert response.status_code == 403, route
        assert "operator-secret" not in str(response.json())


def test_replay_control_allows_remote_with_operator_token() -> None:
    # audit #NN-6 (b): the same gate admits a remote caller presenting the operator token.
    app = create_app(Settings(_env_file=None, operator_token="operator-secret"))
    remote = TestClient(app, client=("203.0.113.10", 12345))

    headers = {"x-trade-lab-operator-token": "operator-secret"}
    rejected = remote.post("/api/v1/replay/pause")
    allowed = remote.post("/api/v1/replay/pause", headers=headers)

    assert rejected.status_code == 403
    assert allowed.status_code == 200
    assert "operator-secret" not in str(rejected.json()) + str(allowed.json())
