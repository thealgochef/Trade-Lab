"""Stage 5 — inference API contract: model hot-swap REST + ws envelopes.

A tiny synthetic CatBoost model is trained in-fixture (3 classes, the 6 contract
feature names, exact order) and saved into a temp bundle alongside a copy of the
real fixture ``strategy.json``. The ``.cbm`` is never read as text — it is loaded
via CatBoost when an activation happens, which is using it (allowed), not printing
it. No model/prediction payload may leak a filesystem path or secret.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from catboost import CatBoostClassifier, Pool
from fastapi.testclient import TestClient

from trade_lab.api.app import create_app
from trade_lab.api.dto import MESSAGE_VERSION
from trade_lab.config import Settings
from trade_lab.domain.events import TradeEvent
from trade_lab.services.broadcaster import WebSocketBroadcaster
from trade_lab.services.replay import HistoricalReplayService
from trade_lab.services.runtime import ApplicationRuntime

_FIXTURE_STRATEGY = Path(__file__).parent / "fixtures" / "strategy.json"
_CONTRACT_FEATURES = [
    "int_time_beyond_level",
    "int_time_within_2pts",
    "int_absorption_ratio",
    "app_large_trade_vol_pct",
    "app_avg_trade_size",
    "app_max_spread",
]
_OPERATOR_TOKEN = "super-secret-operator-token"
_SECRET_PATTERNS = (
    "super-secret",
    "secret-token",
    "databento_api_key",
    "token=",
    "password=",
    _OPERATOR_TOKEN,
)


def _assert_no_secret_text(payload: object) -> None:
    text = json.dumps(payload, default=str).lower()
    for pattern in _SECRET_PATTERNS:
        assert pattern.lower() not in text
    assert "c:\\users" not in text
    assert "/users/" not in text
    assert ".parquet" not in text
    assert ".cbm" not in text


def _assert_no_temp_path(payload: object, tmp_path: Path) -> None:
    text = json.dumps(payload, default=str)
    # No absolute path component of the bundle root may surface in any payload.
    assert str(tmp_path) not in text
    assert tmp_path.name not in text


def _train_model(feature_names: list[str], *, n_classes: int = 3) -> CatBoostClassifier:
    rows: list[list[float]] = []
    labels: list[int] = []
    for cls in range(n_classes):
        for rep in range(4):
            base = float(cls) + 0.1 * rep
            rows.append([base + i * 0.01 for i in range(len(feature_names))])
            labels.append(cls)
    pool = Pool(np.array(rows), np.array(labels), feature_names=list(feature_names))
    model = CatBoostClassifier(iterations=30, depth=2, loss_function="MultiClass", verbose=False)
    model.fit(pool)
    return model


def _write_bundle(
    root: Path,
    model_id: str,
    model: CatBoostClassifier,
    *,
    selected_features: list[str] | None = None,
) -> Path:
    payload = json.loads(_FIXTURE_STRATEGY.read_text(encoding="utf-8"))
    directory = root / model_id
    directory.mkdir(parents=True)
    model.save_model(str(directory / "model.cbm"))
    features = selected_features if selected_features is not None else list(
        payload["feature_set"]["names"]
    )
    (directory / "metadata.json").write_text(
        json.dumps({"selected_features": features}), encoding="utf-8"
    )
    (directory / "strategy.json").write_text(json.dumps(payload), encoding="utf-8")
    return directory


def _settings(tmp_path: Path, *, with_token: bool = False) -> Settings:
    kwargs: dict[str, object] = {
        "_env_file": None,
        "front_month_symbol": "NQ.c.0",
        "models_path": tmp_path,
        "databento_api_key": "super-secret-key",
    }
    if with_token:
        kwargs["operator_token"] = _OPERATOR_TOKEN
    return Settings(**kwargs)


def _good_bundle_app(tmp_path: Path, *, with_token: bool = False) -> TestClient:
    _write_bundle(tmp_path, "good-model", _train_model(_CONTRACT_FEATURES))
    return TestClient(create_app(_settings(tmp_path, with_token=with_token)))


# --------------------------------------------------------------------------- #
# GET /models discovery
# --------------------------------------------------------------------------- #


def test_list_models_returns_fixture_bundle_without_paths_or_secrets(tmp_path: Path) -> None:
    client = _good_bundle_app(tmp_path)

    response = client.get("/api/v1/models")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["models"]) == 1
    bundle = payload["models"][0]
    assert bundle["model_id"] == "good-model"
    assert set(bundle) == {
        "model_id",
        "strategy_id",
        "training_mode",
        "instrument",
        "feature_count",
        "class_map",
        "has_checksum",
        "validation_ok",
        "validation_detail",
    }
    assert bundle["feature_count"] == len(_CONTRACT_FEATURES)
    assert bundle["validation_ok"] is True
    _assert_no_secret_text(payload)
    _assert_no_temp_path(payload, tmp_path)


def test_active_model_defaults_to_unloaded(tmp_path: Path) -> None:
    client = _good_bundle_app(tmp_path)

    response = client.get("/api/v1/models/active")

    assert response.status_code == 200
    payload = response.json()
    assert payload["loaded"] is False
    assert payload["model_id"] is None
    _assert_no_secret_text(payload)


# --------------------------------------------------------------------------- #
# POST /models/activate
# --------------------------------------------------------------------------- #


def test_activate_happy_path_loads_and_reports_status(tmp_path: Path) -> None:
    client = _good_bundle_app(tmp_path)

    response = client.post("/api/v1/models/activate", json={"model_id": "good-model"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["loaded"] is True
    assert payload["model_id"] == "good-model"
    assert payload["feature_names"] == _CONTRACT_FEATURES
    assert payload["validation_ok"] is True
    assert payload.get("class_map")

    active = client.get("/api/v1/models/active").json()
    assert active["model_id"] == "good-model"
    _assert_no_secret_text(payload)
    _assert_no_temp_path(payload, tmp_path)


def test_activate_broadcasts_model_status_over_websocket(tmp_path: Path) -> None:
    client = _good_bundle_app(tmp_path)

    with client.websocket_connect("/ws/v1") as websocket:
        snapshot = json.loads(websocket.receive_bytes())
        json.loads(websocket.receive_bytes())  # heartbeat
        assert snapshot["payload"]["model_status"]["loaded"] is False

        activate = client.post("/api/v1/models/activate", json={"model_id": "good-model"})
        assert activate.status_code == 200

        model_status = json.loads(websocket.receive_bytes())

    assert model_status["version"] == MESSAGE_VERSION
    assert model_status["type"] == "model.status"
    assert model_status["sequence"] > snapshot["sequence"]
    assert model_status["payload"]["loaded"] is True
    assert model_status["payload"]["model_id"] == "good-model"
    _assert_no_secret_text(model_status)
    _assert_no_temp_path(model_status, tmp_path)


def test_activate_rejects_path_like_id_as_400(tmp_path: Path) -> None:
    client = _good_bundle_app(tmp_path)
    path_like = [
        "C:\\Users\\model",
        "C:/Users/model",
        "..\\escape",
        "../escape",
        "good/../good-model",
        "good model",
    ]

    responses = [
        client.post("/api/v1/models/activate", json={"model_id": value}) for value in path_like
    ]

    assert [r.status_code for r in responses] == [400] * len(path_like)
    for response in responses:
        _assert_no_secret_text(response.json())
        _assert_no_temp_path(response.json(), tmp_path)


def test_activate_unknown_id_is_404(tmp_path: Path) -> None:
    client = _good_bundle_app(tmp_path)

    response = client.post("/api/v1/models/activate", json={"model_id": "nope-missing"})

    assert response.status_code == 404
    _assert_no_secret_text(response.json())
    _assert_no_temp_path(response.json(), tmp_path)


def test_activate_invalid_bundle_is_409_with_safe_message(tmp_path: Path) -> None:
    # Train a model whose feature order disagrees with the shipped contract: the
    # registry fails closed at load time -> 409 with a path-free message.
    reordered = [_CONTRACT_FEATURES[1], _CONTRACT_FEATURES[0], *_CONTRACT_FEATURES[2:]]
    _write_bundle(tmp_path, "wrong-order", _train_model(reordered))
    client = TestClient(create_app(_settings(tmp_path)))

    response = client.post("/api/v1/models/activate", json={"model_id": "wrong-order"})

    assert response.status_code == 409
    assert client.get("/api/v1/models/active").json()["loaded"] is False
    _assert_no_secret_text(response.json())
    _assert_no_temp_path(response.json(), tmp_path)


# --------------------------------------------------------------------------- #
# POST /models/deactivate
# --------------------------------------------------------------------------- #


def test_deactivate_unloads_and_keeps_serving(tmp_path: Path) -> None:
    client = _good_bundle_app(tmp_path)
    client.post("/api/v1/models/activate", json={"model_id": "good-model"})

    response = client.post("/api/v1/models/deactivate")

    assert response.status_code == 200
    assert response.json()["loaded"] is False
    assert client.get("/api/v1/models/active").json()["loaded"] is False
    # Market-data status still serves normally.
    assert client.get("/api/v1/status").status_code == 200


# --------------------------------------------------------------------------- #
# Operator-token / origin gating
# --------------------------------------------------------------------------- #


def test_activate_rejects_disallowed_browser_origin(tmp_path: Path) -> None:
    client = _good_bundle_app(tmp_path)

    response = client.post(
        "/api/v1/models/activate",
        json={"model_id": "good-model"},
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403
    _assert_no_secret_text(response.json())


def test_activate_with_operator_token_accepts_allowlisted_origin(tmp_path: Path) -> None:
    client = _good_bundle_app(tmp_path, with_token=True)

    response = client.post(
        "/api/v1/models/activate",
        json={"model_id": "good-model"},
        headers={
            "Origin": "http://localhost:5174",
            "x-trade-lab-operator-token": _OPERATOR_TOKEN,
        },
    )

    assert response.status_code == 200
    assert response.json()["loaded"] is True
    _assert_no_secret_text(response.json())


def test_deactivate_rejects_disallowed_browser_origin(tmp_path: Path) -> None:
    client = _good_bundle_app(tmp_path)

    response = client.post(
        "/api/v1/models/deactivate", headers={"Origin": "https://evil.example"}
    )

    assert response.status_code == 403
    _assert_no_secret_text(response.json())


# --------------------------------------------------------------------------- #
# Status session/trading_day + prediction ws envelopes
# --------------------------------------------------------------------------- #


def test_status_exposes_session_and_trading_day_keys(tmp_path: Path) -> None:
    client = _good_bundle_app(tmp_path)

    payload = client.get("/api/v1/status").json()

    assert "session" in payload
    assert "trading_day" in payload
    # No event processed yet -> both null (never fabricated).
    assert payload["session"] is None
    assert payload["trading_day"] is None


def _trade(ts: datetime, price_ticks: int) -> TradeEvent:
    return TradeEvent(
        event_ts_utc=ts,
        receive_ts_utc=None,
        instrument_id=1,
        requested_symbol="NQ.c.0",
        raw_symbol="NQM6",
        price_ticks=price_ticks,
        size=1,
        source_schema="trades",
    )


def _drive_pdh_touch(runtime: ApplicationRuntime, day0: datetime) -> None:
    runtime.levels.load_prior_day_summary((day0 - timedelta(days=1)).date(), 68_010, 67_990)
    runtime.process_market_event(_trade(day0, 68_008))
    runtime.process_market_event(_trade(day0 + timedelta(seconds=1), 68_012))


def test_status_session_derived_from_latest_event(tmp_path: Path) -> None:
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0", tick_timeframes=(2,), observation_duration_seconds=300
    )
    # 14:30 UTC on 2026-01-05 is the NY cash session for the 2026-01-05 trading day.
    runtime.process_market_event(_trade(datetime(2026, 1, 5, 14, 30, tzinfo=UTC), 68_000))
    client = TestClient(
        create_app(_settings(tmp_path), runtime=runtime, replay=HistoricalReplayService(runtime))
    )

    payload = client.get("/api/v1/status").json()

    assert payload["session"] == "ny"
    assert payload["trading_day"] == "2026-01-05"


def test_prediction_created_and_resolved_envelopes_validate(tmp_path: Path) -> None:
    _client, runtime, broadcaster = _runtime_app_with_active_model(tmp_path)

    # Drive a real touch + observation completion through the hot path so the update
    # carries a prediction; then map it through the broadcaster the way the live ws
    # fan-out does (mirrors test_api_contract's messages_for_update assertions).
    day0 = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    _drive_pdh_touch(runtime, day0)
    completion = runtime.process_market_event(_trade(day0 + timedelta(minutes=6), 68_011))
    assert completion.predictions, "expected a prediction on observation completion"

    envelopes = [json.loads(raw) for raw in broadcaster.messages_for_update(completion)]
    created = [e for e in envelopes if e["type"] == "prediction.created"]

    assert created, "no prediction.created envelope was broadcast"
    envelope = created[0]
    assert envelope["version"] == MESSAGE_VERSION
    assert envelope["type"] == "prediction.created"
    assert envelope["payload"]["prediction"]["model_id"] == "good-model"
    assert set(envelope["payload"]["prediction"]) == {
        "prediction_id",
        "touch_id",
        "observation_id",
        "event_ts_utc",
        "predicted_class",
        "probabilities",
        "feature_values",
        "level_kind",
        "level_price_ticks",
        "direction",
        "session",
        "is_eligible",
        "model_id",
        "contract_id",
        "nan_count",
    }
    sequences = [e["sequence"] for e in envelopes]
    assert sequences == sorted(sequences)
    _assert_no_secret_text(envelopes)
    _assert_no_temp_path(envelopes, tmp_path)


def test_resolved_outcome_envelope_validates_when_a_forward_bar_closes(tmp_path: Path) -> None:
    _client, runtime, broadcaster = _runtime_app_with_active_model(tmp_path)

    day0 = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    _drive_pdh_touch(runtime, day0)
    runtime.process_market_event(_trade(day0 + timedelta(minutes=6), 68_011))

    # Synthesize an outcome envelope directly from the runtime API to validate the
    # prediction.resolved shape without depending on forward-resolution timing.
    from trade_lab.api.dto import outcome_payload, outcome_to_dto
    from trade_lab.domain.outcomes import Outcome, ResolutionType

    sample = Outcome(
        outcome_id="outcome-1",
        prediction_id="prediction-1",
        touch_id="touch-1",
        resolution_type=ResolutionType.TP_HIT,
        actual_class="tradeable_reversal",
        predicted_class="tradeable_reversal",
        correct=True,
        max_mfe_pts=12.0,
        max_mae_pts=2.0,
        bars_to_resolution=3,
        resolved_ts_utc=day0 + timedelta(minutes=10),
    )
    raw = broadcaster.envelope_bytes("prediction.resolved", outcome_payload(sample))
    envelope = json.loads(raw)

    assert envelope["version"] == MESSAGE_VERSION
    assert envelope["type"] == "prediction.resolved"
    assert envelope["payload"]["outcome"] == outcome_to_dto(sample).model_dump(mode="json")
    _assert_no_secret_text(envelope)
    _assert_no_temp_path(envelope, tmp_path)


def _runtime_app_with_active_model(
    tmp_path: Path,
) -> tuple[TestClient, ApplicationRuntime, WebSocketBroadcaster]:
    _write_bundle(tmp_path, "good-model", _train_model(_CONTRACT_FEATURES))
    # D1a: the contract's forward bar type (147t) must be among the configured
    # timeframes — activation fails loud otherwise; 2t remains the decision bar.
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0", tick_timeframes=(2, 147), observation_duration_seconds=300
    )
    broadcaster = WebSocketBroadcaster(runtime)
    app = create_app(
        _settings(tmp_path),
        runtime=runtime,
        replay=HistoricalReplayService(runtime),
        broadcaster=broadcaster,
    )
    client = TestClient(app)
    activate = client.post("/api/v1/models/activate", json={"model_id": "good-model"})
    assert activate.status_code == 200
    return client, runtime, broadcaster
