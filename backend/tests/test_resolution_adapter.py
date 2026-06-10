"""D1b resolution adapter: SC streaming emissions -> served TL domain shapes.

Pins the ratified mapping table (label -> ResolutionType), TL-side correctness,
field passthrough (0-based bars, honest entry, resolving-bar timestamp), the drop
mapping, and the relocated ``parse_bar_type`` (regex + ValueError semantics
carried intact from the deleted ``outcome_tracker`` module).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import MappingProxyType

import pytest
from strategy_core import StreamDrop, StreamResolution
from strategy_core.decisions.outcomes import OutcomeResult

from trade_lab.domain.outcomes import ResolutionType
from trade_lab.services.inference.inference_engine import Prediction
from trade_lab.services.inference.resolution_adapter import (
    drop_to_dropped,
    parse_bar_type,
    resolution_to_outcome,
)

_DECISION_TS = datetime(2026, 1, 5, 14, 5, tzinfo=UTC)
_RESOLVED_TS = datetime(2026, 1, 5, 14, 20, tzinfo=UTC)


def _prediction(predicted_class: str = "tradeable_reversal") -> Prediction:
    return Prediction(
        prediction_id="pred-1",
        touch_id="touch-1",
        observation_id="obs-1",
        event_ts_utc=_DECISION_TS,
        predicted_class=predicted_class,
        probabilities=MappingProxyType({predicted_class: 1.0}),
        feature_values=MappingProxyType({}),
        level_kind="ny_low",
        level_price_ticks=68_000,
        direction="long",
        session="ny",
        is_eligible=True,
        model_id="m-1",
        contract_id="NQ_test",
        nan_count=0,
    )


def _resolution(label: str, *, bars: int = 3) -> StreamResolution:
    return StreamResolution(
        key="pred-1",
        decision_ts_utc=_DECISION_TS,
        entry_price=17_001.25,
        resolved_ts_utc=_RESOLVED_TS,
        result=OutcomeResult(
            label=label,
            label_encoded=None,
            max_mfe=16.0,
            max_mae=2.5,
            bars_to_resolution=bars,
        ),
    )


def test_tradeable_reversal_maps_to_tp_hit_and_correct_when_predicted() -> None:
    outcome = resolution_to_outcome(_resolution("tradeable_reversal"), _prediction())
    assert outcome.resolution_type is ResolutionType.TP_HIT
    assert outcome.actual_class == "tradeable_reversal"
    assert outcome.correct is True
    assert outcome.prediction_id == "pred-1"
    assert outcome.touch_id == "touch-1"
    assert outcome.predicted_class == "tradeable_reversal"
    # Passthrough: honest entry, 4dp extremes, ZERO-BASED bars, resolving-bar close.
    assert outcome.entry_price == 17_001.25
    assert (outcome.max_mfe_pts, outcome.max_mae_pts) == (16.0, 2.5)
    assert outcome.bars_to_resolution == 3
    assert outcome.resolved_ts_utc == _RESOLVED_TS


def test_aggressive_blowthrough_maps_to_sl_hit_and_incorrect_vs_tradeable() -> None:
    outcome = resolution_to_outcome(_resolution("aggressive_blowthrough"), _prediction())
    assert outcome.resolution_type is ResolutionType.SL_HIT
    assert outcome.actual_class == "aggressive_blowthrough"
    assert outcome.correct is False  # predicted tradeable_reversal


def test_trap_reversal_maps_to_sl_hit_and_correct_when_predicted() -> None:
    outcome = resolution_to_outcome(
        _resolution("trap_reversal"), _prediction(predicted_class="trap_reversal")
    )
    assert outcome.resolution_type is ResolutionType.SL_HIT
    assert outcome.actual_class == "trap_reversal"
    assert outcome.correct is True


def test_unmapped_label_fails_loud() -> None:
    with pytest.raises(ValueError, match="unmapped resolution label"):
        resolution_to_outcome(_resolution("session_end"), _prediction())


def test_outcome_ids_are_unique_per_adaptation() -> None:
    first = resolution_to_outcome(_resolution("tradeable_reversal"), _prediction())
    second = resolution_to_outcome(_resolution("tradeable_reversal"), _prediction())
    assert first.outcome_id != second.outcome_id


def test_registration_drop_maps_without_entry() -> None:
    drop = StreamDrop(reason="flatten", key="pred-1", decision_ts_utc=_DECISION_TS)
    dropped = drop_to_dropped(drop, _prediction())
    assert dropped.prediction_id == "pred-1"
    assert dropped.touch_id == "touch-1"
    assert dropped.reason == "flatten"
    assert dropped.decision_ts_utc == _DECISION_TS
    assert dropped.entry_price is None


def test_terminal_drop_carries_the_honest_entry() -> None:
    drop = StreamDrop(
        reason="no_resolution",
        key="pred-1",
        decision_ts_utc=_DECISION_TS,
        entry_price=17_001.25,
        max_mfe=3.0,
        max_mae=2.0,
        bars_to_resolution=-1,
    )
    dropped = drop_to_dropped(drop, _prediction())
    assert dropped.reason == "no_resolution"
    assert dropped.entry_price == 17_001.25


def test_parse_bar_type_maps_tick_counts() -> None:
    assert parse_bar_type("147t") == 147
    assert parse_bar_type(" 2T ") == 2  # strip + lower, exactly the tracker's semantics


@pytest.mark.parametrize("bad", ["147", "t147", "", "147s", "1.5t"])
def test_parse_bar_type_rejects_non_tick_forms(bad: str) -> None:
    with pytest.raises(ValueError, match="unsupported forward_bar_type"):
        parse_bar_type(bad)
