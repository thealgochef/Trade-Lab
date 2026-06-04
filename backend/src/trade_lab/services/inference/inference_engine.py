"""Turn a completed observation into a contract-stamped :class:`Prediction`.

The engine sits behind the runtime hot path: when an observation completes it
builds the level context + feature window from the active strategy contract,
computes the contract-ordered feature vector against the shared L1/L0
:class:`MarketContextBuffer`, then asks the active CatBoost model for class
probabilities. The result is a frozen, path-free :class:`Prediction` carrying the
diagnostics (feature values, probabilities, eligibility) and the ``contract_id`` /
``model_id`` it was produced under, so a hot-swap can never silently mix bundles.

If no model is active the engine returns ``None`` and the runtime keeps serving
market data untouched. Nothing here loads or broadcasts; the runtime owns state.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING
from uuid import uuid4

from trade_lab.domain.contracts import StrategyContract
from trade_lab.services.inference.features import (
    DEFAULT_FEATURE_REGISTRY,
    FeatureFunctionRegistry,
    LevelContext,
    LevelDirection,
    build_feature_vector,
)

if TYPE_CHECKING:
    from trade_lab.domain.market_context import MarketContextBuffer
    from trade_lab.domain.observations import Observation
    from trade_lab.services.model_registry import ActiveModel, ModelRegistry


@dataclass(frozen=True, slots=True)
class Prediction:
    """A frozen, contract-stamped inference result for one completed observation."""

    prediction_id: str
    touch_id: str
    observation_id: str
    event_ts_utc: datetime
    predicted_class: str
    probabilities: MappingProxyType[str, float]
    feature_values: MappingProxyType[str, float]
    level_kind: str
    level_price_ticks: int
    direction: str
    session: str
    is_eligible: bool
    model_id: str
    contract_id: str
    nan_count: int


def _level_side(level_kind: str) -> str:
    """Map a level kind to the ``low``/``high`` side the contract keys direction on.

    ``pdh`` / ``*_high`` are resistance (touched from below -> a short reversal);
    ``pdl`` / ``*_low`` are support (touched from above -> a long reversal).
    """

    kind = level_kind.lower()
    if kind.endswith("high") or kind == "pdh":
        return "high"
    if kind.endswith("low") or kind == "pdl":
        return "low"
    raise ValueError(f"cannot derive a level side from level_kind {level_kind!r}")


def _direction_from_contract(contract: StrategyContract, level_kind: str) -> LevelDirection:
    side = _level_side(level_kind)
    mapped = contract.touch_rule.direction_from_side.get(side)
    if mapped is None:
        raise ValueError(f"contract has no direction mapping for level side {side!r}")
    return LevelDirection(mapped.lower())


def _session_matches(observation_session: str, eligible_session: str) -> bool:
    """Match the runtime session name against the contract's eligible session.

    The runtime labels the US cash session ``ny`` (``SessionName.NY``) while a
    contract may name the eligible session ``ny_rth``. Compare on the leading
    token so the two conventions line up without hardcoding either spelling.
    """

    obs = observation_session.lower()
    eligible = eligible_session.lower()
    if obs == eligible:
        return True
    return eligible.split("_", 1)[0] == obs.split("_", 1)[0]


class InferenceEngine:
    """Produce predictions from completed observations using the active model."""

    def __init__(
        self,
        registry: ModelRegistry,
        *,
        feature_registry: FeatureFunctionRegistry = DEFAULT_FEATURE_REGISTRY,
    ) -> None:
        self._registry = registry
        self._feature_registry = feature_registry

    @property
    def has_active_model(self) -> bool:
        return self._registry.active() is not None

    def active(self) -> ActiveModel | None:
        """Return the registry's active ``(model, contract, model_id)`` or ``None``.

        Lets the runtime build a path-free model status (id + contract) for the API
        edge without reaching into the registry directly.
        """

        return self._registry.active()

    @property
    def active_contract(self) -> StrategyContract | None:
        """The contract the active model was validated against, or ``None``.

        Lets the runtime build a contract-specific OutcomeTracker without reaching
        into the registry, so a hot-swap re-derives forward thresholds/bar type.
        """

        active = self._registry.active()
        return active.contract if active is not None else None

    def predict_for_observation(
        self, observation: Observation, buffer: MarketContextBuffer
    ) -> Prediction | None:
        """Build features and predict for one completed observation.

        Returns ``None`` when no model is active so the runtime keeps serving
        market data. ``buffer`` is the runtime's shared L1/L0 context, read through
        the contract's feature windows.
        """

        active = self._registry.active()
        if active is None:
            return None
        return self._predict(active, observation, buffer)

    def _predict(
        self,
        active: ActiveModel,
        observation: Observation,
        buffer: MarketContextBuffer,
    ) -> Prediction:
        contract = active.contract
        reference_price = Decimal(observation.level_price_ticks) * Decimal(str(contract.tick_size))
        direction = _direction_from_contract(contract, observation.level_kind.value)
        level_ctx = LevelContext.from_contract(
            contract, reference_price=reference_price, direction=direction
        )
        ordered, by_name = build_feature_vector(
            buffer,
            level_ctx,
            contract,
            registry=self._feature_registry,
            touch_ts_utc=observation.start_ts_utc,
        )
        nan_count = sum(1 for value in ordered if math.isnan(value))

        proba = active.model.predict_proba([ordered])[0]
        probabilities = self._map_probabilities(active, contract, proba)
        predicted_class = max(probabilities, key=lambda label: probabilities[label])

        session = observation.session.value
        gate = contract.inference.confidence_gate
        eligible_class = contract.inference.eligible_class
        is_eligible = (
            predicted_class == eligible_class
            and _session_matches(session, contract.inference.eligible_session)
            and probabilities.get(eligible_class, 0.0) >= gate
        )

        return Prediction(
            prediction_id=str(uuid4()),
            touch_id=observation.originating_touch_id,
            observation_id=observation.observation_id,
            event_ts_utc=observation.scheduled_end_ts_utc,
            predicted_class=predicted_class,
            probabilities=MappingProxyType(probabilities),
            feature_values=MappingProxyType(dict(by_name)),
            level_kind=observation.level_kind.value,
            level_price_ticks=observation.level_price_ticks,
            direction=direction.value,
            session=session,
            is_eligible=is_eligible,
            model_id=active.model_id,
            contract_id=contract.strategy_id,
            nan_count=nan_count,
        )

    @staticmethod
    def _map_probabilities(
        active: ActiveModel, contract: StrategyContract, proba
    ) -> dict[str, float]:
        """Map CatBoost MultiClass probabilities to contract labels.

        ``model.classes_`` are the encoded integer class indices (possibly ordered
        differently than the contract). Each is mapped via ``class_map`` keyed by
        ``str(int(cls))`` -> label, so probabilities line up with labels by the
        model's own class order rather than positionally.
        """

        class_map = contract.class_map.mapping
        probabilities: dict[str, float] = {}
        for cls, value in zip(active.model.classes_, proba, strict=True):
            label = class_map[int(cls)]
            probabilities[label] = float(value)
        return probabilities
