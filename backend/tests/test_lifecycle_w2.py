"""W2 P2: lifecycle + visibility — atomic activation (F12), flush wiring (F10),
typed model.reset frame, named-feature failure visibility, journal tagging."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import numpy as np
import pytest
from catboost import CatBoostClassifier, Pool
from fastapi.testclient import TestClient
from strategy_core import StreamDrop

from trade_lab.api.app import create_app
from trade_lab.config import Settings
from trade_lab.domain.events import TradeEvent
from trade_lab.domain.feed import FeedConnectionState, FeedStatus
from trade_lab.domain.observations import ObservationStatus
from trade_lab.services.broadcaster import WebSocketBroadcaster
from trade_lab.services.inference.features import FeatureComputationError
from trade_lab.services.inference.inference_engine import Prediction
from trade_lab.services.journal import PredictionJournal
from trade_lab.services.live import LiveConfig, LiveMarketDataService
from trade_lab.services.replay import HistoricalReplayService, ReplayConfig
from trade_lab.services.runtime import ApplicationRuntime, RuntimeUpdate

_FIXTURE_STRATEGY = Path(__file__).parent / "fixtures" / "strategy.json"


def _contract_features() -> list[str]:
    payload = json.loads(_FIXTURE_STRATEGY.read_text(encoding="utf-8"))
    return list(payload["feature_set"]["names"])


def _train_model(feature_names: list[str]) -> CatBoostClassifier:
    rows: list[list[float]] = []
    labels: list[int] = []
    for cls in range(3):
        for rep in range(4):
            base = float(cls) + 0.1 * rep
            rows.append([base + i * 0.01 for i in range(len(feature_names))])
            labels.append(cls)
    pool = Pool(np.array(rows), np.array(labels), feature_names=list(feature_names))
    model = CatBoostClassifier(iterations=30, depth=2, loss_function="MultiClass", verbose=False)
    model.fit(pool)
    return model


def _write_bundle(root: Path, model_id: str, model: CatBoostClassifier) -> Path:
    payload = json.loads(_FIXTURE_STRATEGY.read_text(encoding="utf-8"))
    directory = root / model_id
    directory.mkdir(parents=True)
    model.save_model(str(directory / "model.cbm"))
    (directory / "metadata.json").write_text(
        json.dumps({"selected_features": payload["feature_set"]["names"]}), encoding="utf-8"
    )
    (directory / "strategy.json").write_text(json.dumps(payload), encoding="utf-8")
    return directory


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        front_month_symbol="NQ.c.0",
        models_path=tmp_path,
        journal_path=tmp_path / "journal",
    )


def _runtime() -> ApplicationRuntime:
    return ApplicationRuntime(
        requested_symbol="NQ.c.0", tick_timeframes=(2, 147), observation_duration_seconds=300
    )


def _prediction(prediction_id: str = "pred-1") -> Prediction:
    return Prediction(
        prediction_id=prediction_id,
        touch_id="touch-1",
        observation_id="obs-1",
        event_ts_utc=datetime(2026, 6, 11, 14, 35, tzinfo=UTC),
        predicted_class="tradeable_reversal",
        probabilities=MappingProxyType({"tradeable_reversal": 0.8}),
        feature_values=MappingProxyType({"interaction_dwell_time": 1.5}),
        level_kind="pdh",
        level_price_ticks=80_000,
        direction="short",
        session="ny",
        is_eligible=True,
        model_id="bundle-a",
        contract_id="bundle-a",
        nan_count=0,
    )


# --------------------------------------------------------------------------- #
# P2b: atomic activation
# --------------------------------------------------------------------------- #


def test_late_construction_failure_leaves_previous_model_active(tmp_path: Path) -> None:
    """F12 negative: a bundle that passes discovery but fails late construction
    (corrupt model binary) returns 409 and the old model keeps serving."""

    _write_bundle(tmp_path, "good-model", _train_model(_contract_features()))
    corrupt_dir = _write_bundle(tmp_path, "corrupt-model", _train_model(_contract_features()))
    (corrupt_dir / "model.cbm").write_bytes(b"this is not a catboost model")
    client = TestClient(create_app(_settings(tmp_path)))

    # Both bundles pass discovery (contract + metadata are valid).
    discovered = {m["model_id"] for m in client.get("/api/v1/models").json()["models"]}
    assert {"good-model", "corrupt-model"} <= discovered

    assert client.post(
        "/api/v1/models/activate", json={"model_id": "good-model"}
    ).status_code == 200

    failed = client.post("/api/v1/models/activate", json={"model_id": "corrupt-model"})
    assert failed.status_code == 409
    assert "loadable CatBoost model" in failed.json()["detail"]

    active = client.get("/api/v1/models/active").json()
    assert active["loaded"] is True
    assert active["model_id"] == "good-model"


def test_registry_prepare_does_not_swap_until_commit(tmp_path: Path) -> None:
    from trade_lab.services.model_registry import ModelRegistry

    _write_bundle(tmp_path, "good-model", _train_model(_contract_features()))
    registry = ModelRegistry(tmp_path)
    candidate = registry.prepare_activation("good-model")
    assert registry.active() is None
    registry.commit_activation(candidate)
    assert registry.active() is candidate


# --------------------------------------------------------------------------- #
# P2a: flush wiring
# --------------------------------------------------------------------------- #


def test_flush_resolver_serves_and_journals_flushed_drops(tmp_path: Path) -> None:
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0",
        tick_timeframes=(2, 147),
        observation_duration_seconds=300,
        journal=PredictionJournal(tmp_path / "journal"),
    )
    decision_ts = datetime(2026, 6, 11, 14, 30, tzinfo=UTC)
    drop = StreamDrop(
        reason="no_forward", key="pred-1", decision_ts_utc=decision_ts, entry_price=20_001.25
    )

    class _Resolver:
        def __init__(self) -> None:
            self.flushed_at: list[datetime] = []

        def flush(self, now_ts_utc: datetime):
            self.flushed_at.append(now_ts_utc)
            return (drop,)

    resolver = _Resolver()
    runtime._honest_resolver = resolver
    runtime._open_predictions["pred-1"] = _prediction()

    update = runtime.flush_resolver(datetime(2026, 6, 11, 22, 0, tzinfo=UTC))

    assert resolver.flushed_at == [datetime(2026, 6, 11, 22, 0, tzinfo=UTC)]
    assert len(update.dropped) == 1
    assert update.dropped[0].reason == "no_forward"
    assert update.dropped[0].prediction_id == "pred-1"
    assert runtime.dropped == update.dropped
    assert runtime._open_predictions == {}
    lines = (tmp_path / "journal" / "2026-06-11.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["type"] == "drop"


def test_flush_resolver_without_timestamp_or_resolver_is_a_noop() -> None:
    runtime = _runtime()
    assert runtime.flush_resolver().has_deltas() is False  # no resolver
    runtime._honest_resolver = SimpleNamespace(flush=lambda ts: (_ for _ in ()).throw(AssertionError))
    assert runtime.flush_resolver().has_deltas() is False  # no timestamp known


def test_replay_end_flushes_the_resolver_with_the_last_event_instant() -> None:
    asyncio.run(_run_replay_end_flush())


async def _run_replay_end_flush() -> None:
    runtime = _runtime()
    flushed: list[object] = []
    runtime.flush_resolver = lambda ts=None: flushed.append(ts) or RuntimeUpdate()  # type: ignore[method-assign]
    replay = HistoricalReplayService(runtime)

    trade_ts = datetime(2026, 6, 11, 14, 30, tzinfo=UTC)

    class _Source:
        def scan(self, paths, **kwargs):
            yield TradeEvent(trade_ts, None, 1, "NQ.c.0", "NQM6", 80_000, 1, source_schema="trades")

    await replay.start(
        _Source(),
        ReplayConfig(paths=(Path("synthetic"),), requested_symbol="NQ.c.0", schema="trades"),
    )
    await replay._task
    assert flushed == [trade_ts]


def test_live_stop_flushes_the_resolver() -> None:
    asyncio.run(_run_live_stop_flush())


async def _run_live_stop_flush() -> None:
    runtime = _runtime()
    flushed: list[object] = []
    runtime.flush_resolver = lambda ts=None: flushed.append(ts) or RuntimeUpdate()  # type: ignore[method-assign]

    class _Feed:
        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

        async def events(self):
            if False:  # pragma: no cover
                yield object()

    live = LiveMarketDataService(
        runtime,
        LiveConfig(
            requested_symbol="NQ.c.0",
            dataset="GLBX.MDP3",
            trade_schema="trades",
            quote_schema="mbp-1",
            context_schemas=(),
            api_key_configured=True,
            enabled=True,
        ),
        lambda _config: _Feed(),
    )
    await live.start()
    await live.stop()
    assert len(flushed) == 1
    assert isinstance(flushed[0], datetime)


def test_hot_swap_flushes_the_old_resolver(tmp_path: Path) -> None:
    _write_bundle(tmp_path, "good-model", _train_model(_contract_features()))
    runtime = _runtime()
    flushed: list[object] = []
    original = runtime.flush_resolver
    runtime.flush_resolver = lambda ts=None: flushed.append(ts) or original(ts)  # type: ignore[method-assign]
    app = create_app(_settings(tmp_path), runtime=runtime)
    client = TestClient(app)
    assert client.post(
        "/api/v1/models/activate", json={"model_id": "good-model"}
    ).status_code == 200
    assert len(flushed) == 1


# --------------------------------------------------------------------------- #
# P2c: typed model.reset frame
# --------------------------------------------------------------------------- #


def test_model_reset_frame_leads_the_update_messages() -> None:
    runtime = _runtime()
    broadcaster = WebSocketBroadcaster(runtime)
    messages = broadcaster.messages_for_update(
        RuntimeUpdate(model_reset_reason="replay_reset", feed_status=runtime.feed_status)
    )
    first = json.loads(messages[0])
    assert first["type"] == "model.reset"
    assert first["payload"] == {"reason": "replay_reset"}
    assert json.loads(messages[1])["type"] == "feed.status"


def test_runtime_reset_carries_the_reset_reason() -> None:
    runtime = _runtime()
    update = runtime.reset(feed_message="runtime reset for live market data", reset_reason="live_reset")
    assert update.model_reset_reason == "live_reset"
    assert runtime.reset().model_reset_reason is None


# --------------------------------------------------------------------------- #
# P2d: named-feature failure visibility
# --------------------------------------------------------------------------- #


class _ExplodingEngine:
    has_active_model = True

    def predict_for_observation(self, observation, buffer):
        raise FeatureComputationError("interaction_dwell_time", ValueError("boom"))


def test_named_feature_failure_is_counted_and_surfaced_in_status(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    runtime = _runtime()
    runtime._inference_engine = _ExplodingEngine()
    observation = SimpleNamespace(
        status=ObservationStatus.EXPIRED,
        scheduled_end_ts_utc=datetime(2026, 6, 11, 14, 35, tzinfo=UTC),
    )
    with caplog.at_level("WARNING"):
        produced, dropped = runtime._run_inference((observation,))
    assert produced == () and dropped == ()
    record = next(r for r in caplog.records if "interaction_dwell_time" in r.message)
    assert record.exc_info is not None  # exc_info=True logging

    health = runtime.inference_health()
    assert health["error_count"] == 1
    assert health["last_error"]["feature_name"] == "interaction_dwell_time"
    assert health["last_error"]["ts_utc"] == "2026-06-11T14:35:00+00:00"

    app = create_app(_settings(tmp_path), runtime=runtime)
    client = TestClient(app)
    payload = client.get("/api/v1/status").json()
    assert payload["inference"]["error_count"] == 1
    assert payload["inference"]["last_error"]["feature_name"] == "interaction_dwell_time"


# --------------------------------------------------------------------------- #
# P2e: journal tagging through the runtime seam
# --------------------------------------------------------------------------- #


def test_runtime_journals_with_the_feed_mode_tag(tmp_path: Path) -> None:
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0",
        tick_timeframes=(2, 147),
        observation_duration_seconds=300,
        journal=PredictionJournal(tmp_path),
    )
    runtime.set_feed_status(
        FeedStatus(FeedConnectionState.REPLAYING, "replay", "NQ.c.0", last_message="replaying")
    )
    runtime._journal_records((_prediction(),), (), ())
    line = json.loads((tmp_path / "2026-06-11.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert line["mode"] == "replay"
    assert line["bundle_id"] == "bundle-a"
