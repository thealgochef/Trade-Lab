"""Pydantic models mirroring ``strategy.json`` plus a validating loader.

Pydantic is used here because contracts live at a trust boundary: they are
authored externally (by Claude-Quant-Lab) and shipped with each model bundle, so
they must be parsed strictly and rejected loudly on drift. Nothing in this module
loads the CatBoost binary or computes features; it only describes and validates
the contract that later inference stages consume.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import strategy_core
from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "trade_lab_contract_v1"

logger = logging.getLogger(__name__)


class ContractError(ValueError):
    """Raised when a ``strategy.json`` fails to parse or violates the contract."""


class _ContractModel(BaseModel):
    """Base for contract sections: forbid unknown keys so drift is never silent."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Model(_ContractModel):
    type: str = Field(min_length=1, max_length=32)
    loss_function: str = Field(min_length=1, max_length=32)
    file: str = Field(min_length=1, max_length=128)


class FeatureSet(_ContractModel):
    names: tuple[str, ...] = Field(min_length=1, max_length=256)
    order_is_contractual: bool
    interaction_features: tuple[str, ...] = Field(max_length=256)
    approach_features: tuple[str, ...] = Field(max_length=256)
    nan_policy: str = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def _names_partition_into_interaction_and_approach(self) -> FeatureSet:
        split = (*self.interaction_features, *self.approach_features)
        if set(split) != set(self.names) or len(split) != len(self.names):
            raise ValueError(
                "feature_set.names must be exactly the union of interaction_features "
                "and approach_features"
            )
        return self


class SessionWindow(_ContractModel):
    start: str = Field(min_length=1, max_length=8)
    end: str = Field(min_length=1, max_length=8)
    crosses_midnight: bool = False


class SessionScheme(_ContractModel):
    timezone: str = Field(min_length=1, max_length=64)
    trading_day_boundary: str = Field(min_length=1, max_length=8)
    sessions: dict[str, SessionWindow]


class LevelScheme(_ContractModel):
    pdh_pdl_source: str = Field(min_length=1, max_length=64)
    session_levels: tuple[str, ...] = Field(max_length=64)
    available_from_guard: bool


class TouchRule(_ContractModel):
    type: str = Field(min_length=1, max_length=32)
    bar_type: str = Field(min_length=1, max_length=16)
    zone_proximity_pts: float = Field(ge=0.0)
    zone_representative_price: str = Field(min_length=1, max_length=64)
    scope: str = Field(min_length=1, max_length=64)
    direction_from_side: dict[str, str]


class FeatureWindows(_ContractModel):
    interaction_window_minutes: int = Field(gt=0, le=1440)
    approach_window_minutes: int = Field(gt=0, le=1440)
    within_band_pts: float = Field(ge=0.0)
    level_proximity_pts: float = Field(ge=0.0)
    large_trade_threshold: int = Field(gt=0)
    mid_price_source: str = Field(min_length=1, max_length=32)


class LabelPolicy(_ContractModel):
    resolution: str = Field(min_length=1, max_length=32)
    entry_reference: str = Field(min_length=1, max_length=64)
    tp_points: float = Field(gt=0.0)
    sl_points: float = Field(gt=0.0)
    trap_mfe_min: float = Field(ge=0.0)
    forward_bar_type: str = Field(min_length=1, max_length=16)
    forward_cutoff: str = Field(min_length=1, max_length=64)
    no_resolution_dropped: bool


class InferencePolicy(_ContractModel):
    eligible_class: str = Field(min_length=1, max_length=64)
    eligible_session: str = Field(min_length=1, max_length=32)
    confidence_gate: float = Field(ge=0.0, le=1.0)


class DataRequirements(_ContractModel):
    min_book_level: str = Field(min_length=1, max_length=8)
    live_schemas: tuple[str, ...] = Field(max_length=16)
    replay_schemas: tuple[str, ...] = Field(max_length=16)
    depth_usage: str = Field(min_length=1, max_length=32)


class Provenance(_ContractModel):
    dataset_config_hash: str = Field(min_length=1, max_length=64)
    catboost: dict[str, Any]


class ClassMap(_ContractModel):
    """The integer class index → human label map from the model bundle.

    Stored as a model (not a bare dict) so a contract with a malformed class map
    is rejected at parse time and ``labels`` can expose an ordered, contiguous view.
    """

    model_config = ConfigDict(frozen=True)

    mapping: dict[int, str]

    @model_validator(mode="before")
    @classmethod
    def _coerce_string_keys(cls, value: Any) -> Any:
        # strategy.json encodes class indices as JSON object keys (strings).
        if isinstance(value, dict) and "mapping" not in value:
            return {"mapping": value}
        return value

    @model_validator(mode="after")
    def _classes_are_contiguous_from_zero(self) -> ClassMap:
        keys = sorted(self.mapping)
        if not keys or keys != list(range(len(keys))):
            raise ValueError("class_map indices must be contiguous and start at 0")
        if len(set(self.mapping.values())) != len(self.mapping):
            raise ValueError("class_map labels must be unique")
        return self

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(self.mapping[index] for index in range(len(self.mapping)))

    def __len__(self) -> int:
        return len(self.mapping)


class StrategyContract(_ContractModel):
    """A fully parsed, validated ``strategy.json`` for one model bundle."""

    contract_version: str = Field(min_length=1, max_length=64)
    # audit N1: the Strategy-Core engine that produced this bundle. Optional so
    # legacy bundles (authored before engine binding) still parse; the loader
    # fails closed in load_strategy_contract when it is present but does not match
    # the running strategy_core.ENGINE_VERSION.
    engine_version: str | None = Field(default=None, max_length=64)
    strategy_id: str = Field(min_length=1, max_length=256)
    training_mode: str = Field(min_length=1, max_length=64)
    supported_by_runtime: bool
    instrument: str = Field(min_length=1, max_length=32)
    tick_size: float = Field(gt=0.0)
    point_value: float = Field(gt=0.0)
    model: Model
    feature_set: FeatureSet
    class_map: ClassMap
    session_scheme: SessionScheme
    level_scheme: LevelScheme
    touch_rule: TouchRule
    feature_windows: FeatureWindows
    label_policy: LabelPolicy
    inference: InferencePolicy
    data_requirements: DataRequirements
    provenance: Provenance

    @property
    def feature_count(self) -> int:
        return len(self.feature_set.names)


def load_strategy_contract(path: Path | str) -> StrategyContract:
    """Parse and validate a ``strategy.json`` from disk.

    Raises :class:`ContractError` (never a bare ``ValidationError``) on any parse
    failure, an unexpected ``contract_version``, or a feature/class mismatch so
    callers have a single exception type to fail closed on.
    """

    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError(f"strategy contract is unreadable: {path.name}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ContractError(f"strategy contract is not valid JSON: {path.name}") from exc

    if not isinstance(payload, dict):
        raise ContractError("strategy contract must be a JSON object")

    declared_version = payload.get("contract_version")
    if declared_version != CONTRACT_VERSION:
        raise ContractError(
            f"unsupported contract_version {declared_version!r}; "
            f"expected {CONTRACT_VERSION!r}"
        )

    try:
        contract = StrategyContract.model_validate(payload)
    except ValueError as exc:
        raise ContractError(f"invalid strategy contract: {exc}") from exc

    # audit N1: bind the bundle to the running engine. A bundle that declares an
    # engine_version the runtime cannot match must never serve predictions, so we
    # fail closed here. A legacy bundle (no engine_version) still loads but is
    # flagged unbound so the gap is visible rather than silent.
    if (
        contract.engine_version is not None
        and contract.engine_version != strategy_core.ENGINE_VERSION
    ):
        raise ContractError(
            f"unsupported engine_version {contract.engine_version!r}; "
            f"expected {strategy_core.ENGINE_VERSION!r}"
        )
    if contract.engine_version is None:
        logger.warning(
            "strategy contract has no engine_version; loading unbound against %s",
            strategy_core.ENGINE_VERSION,
        )

    return contract
