"""FastAPI app factory for the Phase 2C runtime/replay contract."""

import hmac
import ipaddress
import re
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from trade_lab import __version__
from trade_lab.adapters.databento import DatabentoMarketDataFeed, is_databento_sdk_available
from trade_lab.adapters.databento_historical import DatabentoHistoricalSource
from trade_lab.adapters.replay_catalog import build_replay_catalog
from trade_lab.api.dto import (
    ReplaySourceDTO,
    model_bundle_to_dto,
    model_status_to_dto,
    replay_status_to_dto,
)
from trade_lab.config import Settings, load_settings
from trade_lab.services.broadcaster import WebSocketBroadcaster
from trade_lab.services.inference.inference_engine import InferenceEngine
from trade_lab.services.live import LiveConfig, LiveMarketDataService, LiveState
from trade_lab.services.model_registry import (
    ModelNotFoundError,
    ModelRegistry,
    ModelValidationError,
    is_safe_model_id,
)
from trade_lab.services.replay import HistoricalReplayService, ReplayConfig, ReplayState
from trade_lab.services.runtime import ApplicationRuntime
from trade_lab.services.seed import HistoricalSeedService


class ReplayStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(default="synthetic:nq-demo", min_length=1, max_length=128)
    speed: float = Field(default=0.0, ge=0.0, le=10_000.0)
    max_events: int | None = Field(default=None, ge=1, le=100_000)


class ActivateModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(min_length=1, max_length=128)


_SAFE_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_WINDOWS_DRIVE_SOURCE_ID_RE = re.compile(r"^[A-Za-z]:")
_WS_POLICY_VIOLATION = 1008


def _reject_path_like_source_id(source_id: str) -> None:
    # Source ids are allowlisted opaque names, never caller-supplied paths. This
    # prevents path traversal, absolute-path probing, and accidental real-data reads.
    if (
        not source_id
        or not _SAFE_SOURCE_ID_RE.fullmatch(source_id)
        or _WINDOWS_DRIVE_SOURCE_ID_RE.match(source_id)
        or "/" in source_id
        or "\\" in source_id
        or ".." in source_id
    ):
        raise HTTPException(status_code=400, detail="invalid replay source id")


def _is_allowed_browser_origin(origin: str | None, allowed_origins: tuple[str, ...]) -> bool:
    # Browser WebSocket clients send Origin and must match the explicit allowlist.
    # Test/non-browser clients commonly omit Origin; those are allowed because this
    # check is specifically a browser cross-origin protection.
    return origin is None or origin in allowed_origins


def _referer_origin(referer: str | None) -> str | None:
    if not referer:
        return None
    parsed = urlsplit(referer)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _authorize_browser_live_control_origin(
    request: Request, allowed_origins: tuple[str, ...]
) -> None:
    # Browsers can trigger no-cors/form POST side effects even when CORS prevents
    # reading the response. Treat Origin as authoritative when present; if Origin
    # is absent but Referer is present, validate the referer origin. Non-browser
    # CLI/operator clients commonly send neither and are handled by local/token
    # authorization below.
    origin = request.headers.get("origin")
    if origin is None:
        origin = _referer_origin(request.headers.get("referer"))
    if origin is not None and origin not in allowed_origins:
        raise HTTPException(status_code=403, detail="browser origin is not allowed")


def _is_local_client(host: str | None) -> bool:
    if host in {None, "testclient", "localhost"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _authorize_live_control(request: Request, settings: Settings) -> None:
    _authorize_browser_live_control_origin(request, settings.allowed_origin_values)
    configured_token = (
        settings.operator_token.get_secret_value() if settings.operator_token else None
    )
    supplied_token = request.headers.get("x-trade-lab-operator-token")
    if (
        configured_token
        and supplied_token
        and hmac.compare_digest(supplied_token, configured_token)
    ):
        return
    host = request.client.host if request.client else None
    if _is_local_client(host):
        return
    raise HTTPException(
        status_code=403, detail="live controls require localhost access or operator token"
    )


def _replay_status_payload(replay: HistoricalReplayService) -> dict[str, object]:
    status = replay.status()
    payload = replay_status_to_dto(status).model_dump(mode="json", by_alias=True)
    for field in (
        "source_id",
        "source_label",
        "started_at_utc",
        "completed_at_utc",
        "failed_at_utc",
    ):
        value = getattr(status, field)
        if value is not None:
            payload[field] = value.isoformat() if hasattr(value, "isoformat") else value
    return payload


def _live_status_payload(live: LiveMarketDataService) -> dict[str, object]:
    status = live.status()
    return {
        "state": status.state.value,
        "requested_symbol": status.requested_symbol,
        "dataset": status.dataset,
        "schemas": list(status.schemas),
        "api_key_configured": status.api_key_configured,
        "enabled": status.enabled,
        "sdk_available": status.sdk_available,
        "subscription_ready": status.subscription_ready,
        "events_processed": status.events_processed,
        "last_event_ts_utc": None
        if status.last_event_ts_utc is None
        else status.last_event_ts_utc.isoformat(),
        "last_error": status.last_error,
        "started_at_utc": None
        if status.started_at_utc is None
        else status.started_at_utc.isoformat(),
        "stopped_at_utc": None
        if status.stopped_at_utc is None
        else status.stopped_at_utc.isoformat(),
    }


# audit #NN-2: live and replay share a single ApplicationRuntime, and starting either
# one calls runtime.reset() to rebuild the engine. If one is started while the other is
# active, that reset runs underneath the still-writing task and corrupts bars/levels/
# touches. These predicates classify "active" using only the existing status() accessors
# so the start endpoints can enforce mutual exclusion. Terminal states (idle/stopped/
# completed/failed/disconnected/cancelled) are not active and never block a fresh start.
_LIVE_ACTIVE_STATES = frozenset({LiveState.CONNECTING, LiveState.RUNNING})
_REPLAY_ACTIVE_STATES = frozenset(
    {ReplayState.LOADING, ReplayState.READY, ReplayState.RUNNING, ReplayState.PAUSED}
)


def _live_is_active(live: LiveMarketDataService) -> bool:
    return live.status().state in _LIVE_ACTIVE_STATES


def _replay_is_active(replay: HistoricalReplayService) -> bool:
    return replay.status().state in _REPLAY_ACTIVE_STATES


def _configured_secret_values(settings: Settings) -> tuple[str, ...]:
    secrets = []
    for secret in (settings.databento_api_key, settings.operator_token):
        if secret is not None:
            value = secret.get_secret_value()
            if value:
                secrets.append(value)
    return tuple(secrets)


def create_app(
    settings: Settings | None = None,
    *,
    runtime: ApplicationRuntime | None = None,
    replay: HistoricalReplayService | None = None,
    live: LiveMarketDataService | None = None,
    broadcaster: WebSocketBroadcaster | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="Trade-Lab Backend", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origin_values),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["content-type", "x-trade-lab-operator-token"],
    )
    app.state.settings = settings
    runtime = runtime or ApplicationRuntime(
        requested_symbol=settings.front_month_symbol,
        tick_timeframes=settings.tick_timeframes,
        observation_duration_seconds=settings.observation_duration_seconds,
        seed_bar_limit_per_timeframe=settings.seed_max_bars_per_timeframe,
        market_context_retention_minutes=settings.market_context_retention_minutes,
    )
    # ModelRegistry discovers bundles from the configured models path; the runtime
    # invokes inference on completed observations only once an operator activates a
    # model (Stage 5). No model is active by default, so the runtime just serves
    # market data until then.
    model_registry = ModelRegistry(
        settings.models_path,
        # E2 check (iv): activation refuses contracts routed to any strategy other
        # than the one the runtime's wired plugin actually serves.
        serving_strategy_id=runtime.strategy_core_service.plugin_strategy_id,
    )
    inference_engine = InferenceEngine(model_registry)
    runtime.set_inference_engine(inference_engine)
    app.state.model_registry = model_registry
    app.state.inference_engine = inference_engine
    broadcaster = broadcaster or WebSocketBroadcaster(runtime)
    app.state.runtime = runtime
    app.state.broadcaster = broadcaster
    replay = replay or HistoricalReplayService(runtime)
    if not replay.has_update_callback:
        replay.set_update_callback(broadcaster.broadcast_update)
    app.state.replay = replay
    if live is None:
        live_config = LiveConfig(
            requested_symbol=settings.databento_requested_symbol,
            dataset=settings.databento_dataset,
            trade_schema=settings.databento_trade_schema,
            quote_schema=settings.databento_quote_schema,
            context_schemas=settings.databento_context_schemas,
            api_key_configured=settings.databento_api_key is not None,
            enabled=settings.databento_live_enabled,
            sdk_available=is_databento_sdk_available(),
            secret_values=_configured_secret_values(settings),
        )

        def live_feed_factory(config: LiveConfig):
            if settings.databento_api_key is None:
                raise RuntimeError("Databento API key is not configured")
            return DatabentoMarketDataFeed(
                api_key=settings.databento_api_key.get_secret_value(),
                requested_symbol=config.requested_symbol,
                dataset=config.dataset,
                stype_in=settings.databento_stype_in,
                trade_schema=config.trade_schema,
                quote_schema=config.quote_schema,
                context_schemas=config.context_schemas,
            )

        seed_service = HistoricalSeedService(
            DatabentoHistoricalSource(
                api_key=(
                    None
                    if settings.databento_api_key is None
                    else settings.databento_api_key.get_secret_value()
                ),
                dataset=settings.databento_dataset,
                requested_symbol=settings.databento_requested_symbol,
                stype_in=settings.databento_stype_in,
            ),
            tick_timeframes=settings.tick_timeframes,
            lookback_days=settings.seed_lookback_days,
            max_bars_per_timeframe=settings.seed_max_bars_per_timeframe,
            enabled=settings.seed_enabled,
        )
        live = LiveMarketDataService(
            runtime, live_config, live_feed_factory, seed_service=seed_service
        )
    if not live.has_update_callback:
        live.set_update_callback(broadcaster.broadcast_update)
    app.state.live = live
    catalog = build_replay_catalog(
        data_path=settings.data_path,
        requested_symbol=settings.front_month_symbol,
        instrument_root=settings.instrument_root,
    )
    app.state.replay_sources = catalog.sources
    app.state.replay_catalog_status = catalog

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {"ok": True, "service": "trade-lab-backend", "version": __version__}

    @app.get("/api/v1/status")
    async def status() -> dict[str, object]:
        replay_status = _replay_status_payload(app.state.replay)
        feed_status = app.state.runtime.feed_status
        session, trading_day = app.state.runtime.session_state()
        return {
            "service": "trade-lab-backend",
            "version": __version__,
            "runtime_mode": feed_status.mode,
            "requested_symbol": settings.front_month_symbol,
            "instrument_root": settings.instrument_root,
            "supported_tick_timeframes": list(settings.tick_timeframes),
            "engine_ready": True,
            "feed_ready": feed_status.state.value in {"connected", "replaying"},
            "feed_state": feed_status.state.value,
            "session": session,
            "trading_day": None if trading_day is None else trading_day.isoformat(),
            "replay": replay_status,
            "live": _live_status_payload(app.state.live),
        }

    @app.get("/api/v1/live/status")
    async def live_status() -> dict[str, object]:
        return _live_status_payload(app.state.live)

    @app.post("/api/v1/live/start")
    async def live_start(request: Request) -> dict[str, object]:
        _authorize_live_control(request, settings)
        # audit #NN-2: refuse to start live while a replay is active; both share one
        # runtime and live.start() resets/rebuilds the engine under the replay task.
        if _replay_is_active(app.state.replay):
            raise HTTPException(
                status_code=409, detail="cannot start live feed while replay is active"
            )
        try:
            await app.state.live.start()
        except RuntimeError as exc:
            detail = str(exc)
            code = 409 if "already running" in detail else 400
            raise HTTPException(status_code=code, detail=detail) from exc
        return _live_status_payload(app.state.live)

    @app.post("/api/v1/live/stop")
    async def live_stop(request: Request) -> dict[str, object]:
        _authorize_live_control(request, settings)
        await app.state.live.stop()
        return _live_status_payload(app.state.live)

    @app.get("/api/v1/models")
    async def list_models() -> dict[str, object]:
        registry: ModelRegistry = app.state.model_registry
        return {
            "models": [
                model_bundle_to_dto(bundle).model_dump(mode="json")
                for bundle in registry.discover()
            ]
        }

    @app.get("/api/v1/models/active")
    async def active_model() -> dict[str, object]:
        return model_status_to_dto(app.state.runtime.model_status()).model_dump(mode="json")

    @app.post("/api/v1/models/activate")
    async def activate_model(payload: ActivateModelRequest, request: Request) -> dict[str, object]:
        _authorize_live_control(request, settings)
        model_id = payload.model_id
        # Reject path-like ids before touching the registry so traversal/probing is
        # a 400, not a 404 that confirms which ids exist on disk.
        if not is_safe_model_id(model_id):
            raise HTTPException(status_code=400, detail="invalid model id")
        registry: ModelRegistry = app.state.model_registry
        try:
            registry.activate(model_id)
        except ModelNotFoundError as exc:
            raise HTTPException(status_code=404, detail="unknown model id") from exc
        except ModelValidationError as exc:
            # The message is already path-free/secret-free by ModelRegistry design.
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        # The active contract changed: rebind the engine so prior predictions,
        # outcomes, and the contract-specific tracker are cleared atomically.
        app.state.runtime.set_inference_engine(app.state.inference_engine)
        status = app.state.runtime.model_status()
        await app.state.broadcaster.broadcast_model_status(status)
        return model_status_to_dto(status).model_dump(mode="json")

    @app.post("/api/v1/models/deactivate")
    async def deactivate_model(request: Request) -> dict[str, object]:
        _authorize_live_control(request, settings)
        registry: ModelRegistry = app.state.model_registry
        registry.deactivate()
        # Rebind so prediction/outcome/tracker state is dropped; market data keeps
        # flowing with no model active.
        app.state.runtime.set_inference_engine(app.state.inference_engine)
        status = app.state.runtime.model_status()
        await app.state.broadcaster.broadcast_model_status(status)
        return model_status_to_dto(status).model_dump(mode="json")

    @app.get("/api/v1/replay/status")
    async def replay_status() -> dict[str, object]:
        return _replay_status_payload(app.state.replay)

    @app.get("/api/v1/replay/sources")
    async def replay_sources() -> dict[str, object]:
        return {
            "historical": {
                "available": app.state.replay_catalog_status.historical_available,
                "status": app.state.replay_catalog_status.historical_status,
                "diagnostics": app.state.replay_catalog_status.historical_diagnostics,
            },
            "sources": [
                ReplaySourceDTO(
                    source_id=definition.source_id,
                    label=definition.label,
                    requested_symbol=definition.requested_symbol,
                    schema=definition.schema,
                    kind=definition.kind,
                    session_label=definition.session_label,
                    availability=definition.availability,
                ).model_dump(mode="json", by_alias=True)
                for definition, _source in app.state.replay_sources.values()
            ]
        }

    @app.post("/api/v1/replay/start")
    async def replay_start(payload: ReplayStartRequest, request: Request) -> dict[str, object]:
        # audit #NN-6: replay controls are operator side effects on the shared runtime,
        # so gate them exactly like live_start / the model endpoints do.
        _authorize_live_control(request, settings)
        _reject_path_like_source_id(payload.source_id)
        # audit #NN-2: live and replay share one runtime; replay.start() resets/rebuilds
        # the engine, which would corrupt bars/levels/touches if the live feed is writing.
        if _live_is_active(app.state.live):
            raise HTTPException(
                status_code=409, detail="cannot start replay while live feed is active"
            )
        entry = app.state.replay_sources.get(payload.source_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="unknown replay source id")
        definition, source = entry
        try:
            await app.state.replay.start(
                source,
                ReplayConfig(
                    paths=definition.paths or (Path(payload.source_id),),
                    requested_symbol=definition.requested_symbol,
                    schema=definition.schema,
                    source_id=definition.source_id,
                    source_label=definition.label,
                    speed=payload.speed,
                    max_events=payload.max_events,
                    trading_day=definition.trading_day,
                    symbol_dir=definition.symbol_dir,
                ),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail="replay is already running") from exc
        return _replay_status_payload(app.state.replay)

    @app.post("/api/v1/replay/pause")
    async def replay_pause(request: Request) -> dict[str, object]:
        _authorize_live_control(request, settings)  # audit #NN-6
        await app.state.replay.pause()
        return _replay_status_payload(app.state.replay)

    @app.post("/api/v1/replay/resume")
    async def replay_resume(request: Request) -> dict[str, object]:
        _authorize_live_control(request, settings)  # audit #NN-6
        await app.state.replay.resume()
        return _replay_status_payload(app.state.replay)

    @app.post("/api/v1/replay/stop")
    async def replay_stop(request: Request) -> dict[str, object]:
        _authorize_live_control(request, settings)  # audit #NN-6
        await app.state.replay.stop()
        return _replay_status_payload(app.state.replay)

    @app.websocket("/ws/v1")
    async def websocket_v1(websocket: WebSocket) -> None:
        if not _is_allowed_browser_origin(
            websocket.headers.get("origin"), settings.allowed_origin_values
        ):
            await websocket.close(code=_WS_POLICY_VIOLATION)
            return
        broadcaster: WebSocketBroadcaster = app.state.broadcaster
        queue = await broadcaster.connect(websocket)
        send_task = None
        try:
            import asyncio

            send_task = asyncio.create_task(broadcaster.send_loop(websocket, queue))
            while True:
                # Receiving keeps disconnect handling clean; replay/live deltas are fanned
                # out through bounded per-client queues, not raw tick spam.
                await websocket.receive_text()
        except WebSocketDisconnect:
            return
        finally:
            broadcaster.disconnect(queue)
            if send_task is not None:
                send_task.cancel()

    return app
