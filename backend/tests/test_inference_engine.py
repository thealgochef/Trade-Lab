"""Stage 3 — ModelRegistry fail-closed loading + InferenceEngine + hot-swap.

A tiny synthetic CatBoost model is trained in-fixture (3 classes, the 6 contract
feature names, exact order) and saved into a temp bundle alongside a copy of the
real fixture ``strategy.json``. The ``.cbm`` is never read as text — it is loaded
via CatBoost (using it, not printing it), which is explicitly allowed.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from catboost import CatBoostClassifier, Pool

from trade_lab.domain.contracts import load_strategy_contract
from trade_lab.domain.levels import LevelKind
from trade_lab.domain.observations import Observation, ObservationStatus
from trade_lab.domain.sessions import SessionName
from trade_lab.services.inference.inference_engine import InferenceEngine, Prediction
from trade_lab.services.model_registry import (
    ModelNotFoundError,
    ModelRegistry,
    ModelValidationError,
)
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
_BASE = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


def _train_model(feature_names: list[str], *, n_classes: int = 3) -> CatBoostClassifier:
    """Train a tiny deterministic MultiClass model with the given feature names."""

    rows: list[list[float]] = []
    labels: list[int] = []
    for cls in range(n_classes):
        for rep in range(4):
            base = float(cls) + 0.1 * rep
            rows.append([base + i * 0.01 for i in range(len(feature_names))])
            labels.append(cls)
    pool = Pool(np.array(rows), np.array(labels), feature_names=list(feature_names))
    model = CatBoostClassifier(
        iterations=30, depth=2, loss_function="MultiClass", verbose=False
    )
    model.fit(pool)
    return model


def _write_bundle(
    root: Path,
    model_id: str,
    model: CatBoostClassifier,
    *,
    strategy_payload: dict | None = None,
    with_checksum: bool = False,
) -> Path:
    payload = strategy_payload or json.loads(_FIXTURE_STRATEGY.read_text(encoding="utf-8"))
    directory = root / model_id
    directory.mkdir(parents=True)
    model_path = directory / "model.cbm"
    model.save_model(str(model_path))
    (directory / "metadata.json").write_text(
        json.dumps({"selected_features": list(payload["feature_set"]["names"])}),
        encoding="utf-8",
    )
    (directory / "strategy.json").write_text(json.dumps(payload), encoding="utf-8")
    if with_checksum:
        import hashlib

        digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
        (directory / "model.cbm.sha256").write_text(digest, encoding="utf-8")
    return directory


def _completed_observation(
    *,
    level_kind: LevelKind = LevelKind.NY_LOW,
    session: SessionName = SessionName.NY,
    level_price_ticks: int = 68_000,
    touch_ts: datetime = _BASE,
) -> Observation:
    return Observation(
        observation_id="obs-1",
        originating_touch_id="touch-1",
        start_ts_utc=touch_ts,
        scheduled_end_ts_utc=touch_ts + timedelta(minutes=5),
        status=ObservationStatus.EXPIRED,
        trading_day=touch_ts.date(),
        session=session,
        level_kind=level_kind,
        level_price_ticks=level_price_ticks,
    )


def _seed_buffer(runtime: ApplicationRuntime, *, touch_ts: datetime, level_ticks: int) -> None:
    """Populate the runtime buffer with approach + interaction data for a touch."""

    buffer = runtime.market_context
    # Approach window (pre-touch): trades + quotes back to touch - 30m.
    for offset_min in range(30, 0, -1):
        ts = touch_ts - timedelta(minutes=offset_min)
        from trade_lab.domain.events import TradeSide

        buffer.append_trade(ts, level_ticks - 4, 12, TradeSide.BUY)
        buffer.append_quote(ts, level_ticks - 5, level_ticks - 3)
    # Interaction window (post-touch 5m): quotes around the level.
    from trade_lab.domain.events import TradeSide

    for offset_sec in range(0, 300, 10):
        ts = touch_ts + timedelta(seconds=offset_sec)
        buffer.append_quote(ts, level_ticks - 1, level_ticks + 1)
        buffer.append_trade(ts, level_ticks, 3, TradeSide.SELL)


# --------------------------------------------------------------------------- #
# Load + validate + activate
# --------------------------------------------------------------------------- #


def test_activate_loads_and_validates_matching_model(tmp_path: Path) -> None:
    model = _train_model(_CONTRACT_FEATURES)
    _write_bundle(tmp_path, "good-model", model)
    registry = ModelRegistry(tmp_path)

    active = registry.activate("good-model")

    assert active.model_id == "good-model"
    assert tuple(active.model.feature_names_) == tuple(_CONTRACT_FEATURES)
    assert registry.active() is active
    assert registry.active_model_id() == "good-model"


def test_activate_verifies_matching_checksum(tmp_path: Path) -> None:
    model = _train_model(_CONTRACT_FEATURES)
    _write_bundle(tmp_path, "checksummed", model, with_checksum=True)
    registry = ModelRegistry(tmp_path)

    active = registry.activate("checksummed")

    assert active.model_id == "checksummed"


def test_activate_fails_closed_on_bad_checksum(tmp_path: Path) -> None:
    model = _train_model(_CONTRACT_FEATURES)
    directory = _write_bundle(tmp_path, "bad-sum", model)
    (directory / "model.cbm.sha256").write_text("0" * 64, encoding="utf-8")
    registry = ModelRegistry(tmp_path)

    with pytest.raises(ModelValidationError, match="checksum"):
        registry.activate("bad-sum")
    assert registry.active() is None


def test_activate_fails_closed_on_wrong_feature_order(tmp_path: Path) -> None:
    reordered = [_CONTRACT_FEATURES[1], _CONTRACT_FEATURES[0], *_CONTRACT_FEATURES[2:]]
    model = _train_model(reordered)
    _write_bundle(tmp_path, "wrong-order", model)
    registry = ModelRegistry(tmp_path)

    with pytest.raises(ModelValidationError, match="feature_names_"):
        registry.activate("wrong-order")
    assert registry.active() is None


def test_activate_fails_closed_on_wrong_feature_count(tmp_path: Path) -> None:
    model = _train_model(_CONTRACT_FEATURES[:5])
    _write_bundle(tmp_path, "few-features", model)
    registry = ModelRegistry(tmp_path)

    with pytest.raises(ModelValidationError, match="feature_names_"):
        registry.activate("few-features")
    assert registry.active() is None


def test_activate_fails_closed_on_wrong_class_count(tmp_path: Path) -> None:
    model = _train_model(_CONTRACT_FEATURES, n_classes=2)
    _write_bundle(tmp_path, "two-class", model)
    registry = ModelRegistry(tmp_path)

    with pytest.raises(ModelValidationError, match="class count"):
        registry.activate("two-class")
    assert registry.active() is None


def test_activate_rejects_non_catboost_binary(tmp_path: Path) -> None:
    directory = tmp_path / "junk-model"
    directory.mkdir()
    (directory / "model.cbm").write_bytes(b"not a real catboost model")
    payload = json.loads(_FIXTURE_STRATEGY.read_text(encoding="utf-8"))
    (directory / "metadata.json").write_text(
        json.dumps({"selected_features": list(payload["feature_set"]["names"])}),
        encoding="utf-8",
    )
    (directory / "strategy.json").write_text(json.dumps(payload), encoding="utf-8")
    registry = ModelRegistry(tmp_path)

    with pytest.raises(ModelValidationError):
        registry.activate("junk-model")
    assert registry.active() is None


def test_activate_unknown_model_raises_not_found(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path)
    with pytest.raises(ModelNotFoundError):
        registry.activate("missing")
    with pytest.raises(ModelNotFoundError):
        registry.activate("../escape")


def test_deactivate_unloads_active_model(tmp_path: Path) -> None:
    model = _train_model(_CONTRACT_FEATURES)
    _write_bundle(tmp_path, "good-model", model)
    registry = ModelRegistry(tmp_path)
    registry.activate("good-model")

    registry.deactivate()

    assert registry.active() is None
    assert registry.active_model_id() is None


# --------------------------------------------------------------------------- #
# Inference
# --------------------------------------------------------------------------- #


def _active_registry(tmp_path: Path, model_id: str = "good-model") -> ModelRegistry:
    model = _train_model(_CONTRACT_FEATURES)
    _write_bundle(tmp_path, model_id, model)
    registry = ModelRegistry(tmp_path)
    registry.activate(model_id)
    return registry


def test_prediction_is_deterministic_and_well_formed(tmp_path: Path) -> None:
    registry = _active_registry(tmp_path)
    engine = InferenceEngine(registry)
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0", tick_timeframes=(2,), observation_duration_seconds=300
    )
    _seed_buffer(runtime, touch_ts=_BASE, level_ticks=68_000)
    observation = _completed_observation()

    first = engine.predict_for_observation(observation, runtime.market_context)
    second = engine.predict_for_observation(observation, runtime.market_context)

    assert isinstance(first, Prediction)
    assert first is not None and second is not None
    assert first.predicted_class in {
        "tradeable_reversal",
        "trap_reversal",
        "aggressive_blowthrough",
    }
    assert first.probabilities == second.probabilities
    assert set(first.probabilities) == {
        "tradeable_reversal",
        "trap_reversal",
        "aggressive_blowthrough",
    }
    assert first.probabilities[first.predicted_class] == max(first.probabilities.values())
    assert abs(sum(first.probabilities.values()) - 1.0) < 1e-6
    assert set(first.feature_values) == set(_CONTRACT_FEATURES)
    assert first.contract_id.startswith("NQ_")
    assert first.model_id == "good-model"
    assert first.direction == "long"  # ny_low -> touched from above -> long reversal
    assert first.nan_count == 0


def test_eligibility_requires_class_session_and_confidence(tmp_path: Path) -> None:
    registry = _active_registry(tmp_path)
    engine = InferenceEngine(registry)
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0", tick_timeframes=(2,), observation_duration_seconds=300
    )
    _seed_buffer(runtime, touch_ts=_BASE, level_ticks=68_000)

    # Wrong session (asia) can never be eligible regardless of class/confidence.
    asia_obs = _completed_observation(session=SessionName.ASIA, level_kind=LevelKind.ASIA_LOW)
    asia_pred = engine.predict_for_observation(asia_obs, runtime.market_context)
    assert asia_pred is not None
    assert asia_pred.is_eligible is False

    # NY session: eligibility holds iff predicted class is the eligible class AND its
    # probability clears the gate. Assert the gate logic matches the contract.
    ny_obs = _completed_observation(session=SessionName.NY, level_kind=LevelKind.NY_LOW)
    ny_pred = engine.predict_for_observation(ny_obs, runtime.market_context)
    assert ny_pred is not None
    contract = load_strategy_contract(_FIXTURE_STRATEGY)
    expected = (
        ny_pred.predicted_class == contract.inference.eligible_class
        and ny_pred.probabilities[contract.inference.eligible_class]
        >= contract.inference.confidence_gate
    )
    assert ny_pred.is_eligible is expected


def test_no_active_model_produces_no_prediction(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path)  # nothing activated
    engine = InferenceEngine(registry)
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0", tick_timeframes=(2,), observation_duration_seconds=300
    )
    observation = _completed_observation()

    assert engine.has_active_model is False
    assert engine.predict_for_observation(observation, runtime.market_context) is None


# --------------------------------------------------------------------------- #
# Runtime integration + hot-swap
# --------------------------------------------------------------------------- #


def _runtime_with_engine(registry: ModelRegistry) -> ApplicationRuntime:
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0", tick_timeframes=(2,), observation_duration_seconds=300
    )
    runtime.set_inference_engine(InferenceEngine(registry))
    return runtime


def _trade(ts: datetime, price_ticks: int, size: int = 1):
    from trade_lab.domain.events import TradeEvent

    return TradeEvent(
        event_ts_utc=ts,
        receive_ts_utc=None,
        instrument_id=1,
        requested_symbol="NQ.c.0",
        raw_symbol="NQM6",
        price_ticks=price_ticks,
        size=size,
        source_schema="trades",
    )


def _drive_pdh_touch(
    runtime: ApplicationRuntime, day0: datetime, *, level_ticks: int = 68_010
):
    """Close a decision bar whose range intersects PDH under Strategy-Core rules."""

    runtime.levels.load_prior_day_summary(
        (day0 - timedelta(days=1)).date(), level_ticks, level_ticks - 20
    )
    runtime.process_market_event(_trade(day0, level_ticks - 2))
    return runtime.process_market_event(_trade(day0 + timedelta(seconds=1), level_ticks + 2))


def test_runtime_produces_prediction_when_observation_completes(tmp_path: Path) -> None:
    registry = _active_registry(tmp_path)
    runtime = _runtime_with_engine(registry)

    # Build a real touch + observation via the hot path, then drive the clock past the
    # 5-minute observation window so it completes and inference fires.
    # Establish a prior-day level (PDH), then close a decision bar whose range
    # intersects it without relying on the deprecated one-print exact-touch path.
    day0 = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    update = _drive_pdh_touch(runtime, day0)
    assert update.touches, "expected a touch to seed an observation"

    # No prediction yet (observation still active).
    assert update.predictions == ()

    # A later trade past the 5-minute window expires the observation -> prediction.
    later = day0 + timedelta(minutes=6)
    completion_update = runtime.process_market_event(_trade(later, 68_011))

    assert len(completion_update.predictions) == 1
    assert completion_update.predictions[0].observation_id is not None
    assert runtime.predictions == completion_update.predictions
    assert completion_update.has_deltas()


def test_runtime_without_active_model_serves_market_data_without_predictions(
    tmp_path: Path,
) -> None:
    registry = ModelRegistry(tmp_path)  # discovery only, nothing active
    runtime = _runtime_with_engine(registry)
    day0 = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    _drive_pdh_touch(runtime, day0)
    completion = runtime.process_market_event(_trade(day0 + timedelta(minutes=6), 68_011))

    assert completion.predictions == ()
    # Market data is still flowing.
    assert runtime.snapshot().display_levels


def test_hot_swap_clears_prediction_state_but_not_market_data(tmp_path: Path) -> None:
    registry = _active_registry(tmp_path)
    runtime = _runtime_with_engine(registry)
    day0 = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    _drive_pdh_touch(runtime, day0)
    runtime.process_market_event(_trade(day0 + timedelta(minutes=6), 68_011))
    assert runtime.predictions, "a prediction should have been produced"
    bars_before = runtime.snapshot().display_levels

    # Hot-swap: a new engine (re-activated model) clears prediction state.
    _write_bundle(tmp_path, "swapped-model", _train_model(_CONTRACT_FEATURES))
    registry.activate("swapped-model")
    runtime.set_inference_engine(InferenceEngine(registry))

    assert runtime.predictions == ()
    # Market-data state (levels) is untouched by the swap.
    assert runtime.snapshot().display_levels == bars_before


def test_runtime_reset_clears_predictions(tmp_path: Path) -> None:
    registry = _active_registry(tmp_path)
    runtime = _runtime_with_engine(registry)
    day0 = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    _drive_pdh_touch(runtime, day0)
    runtime.process_market_event(_trade(day0 + timedelta(minutes=6), 68_011))
    assert runtime.predictions

    runtime.reset()

    assert runtime.predictions == ()


# --------------------------------------------------------------------------- #
# Optional real-bundle integration (skipped if unreachable)
# --------------------------------------------------------------------------- #

_REAL_MODELS_ROOT = Path(r"C:\Users\gonza\Documents\Claude-Quant-Lab\models")


@pytest.mark.skipif(
    not _REAL_MODELS_ROOT.is_dir(),
    reason="real Claude-Quant-Lab models directory is not reachable",
)
def test_real_bundle_loads_and_validates_if_present() -> None:
    registry = ModelRegistry(_REAL_MODELS_ROOT)
    bundles = registry.discover()
    valid = [bundle for bundle in bundles if bundle.validation_ok]
    if not valid:
        pytest.skip("no valid real bundle discovered")
    active = registry.activate(valid[0].model_id)
    contract = load_strategy_contract(
        _REAL_MODELS_ROOT / active.model_id / "strategy.json"
    )
    assert tuple(active.model.feature_names_) == tuple(contract.feature_set.names)


def test_environment_has_catboost() -> None:
    import catboost

    assert catboost.__version__
    # Sanity: the test environment that runs pytest is the one with catboost.
    assert os.path.exists(__file__)
