"""Adapt Strategy-Core streaming honest-resolver emissions to served TL shapes.

The D1b serving seam: ``StreamResolution``/``StreamDrop`` (SC, prediction-free —
keyed by the ``prediction_id`` the runtime registered) + the originating
:class:`Prediction` map onto the path-free domain objects the API edge serves.
Correctness (predicted vs actual) is computed HERE — TL's concern, never SC's.

``parse_bar_type`` relocated from the retired ``outcome_tracker`` module
(regex + ValueError semantics intact); the resolver build consumes it to map a
contract ``forward_bar_type`` onto the runtime's tick timeframes.
"""

from __future__ import annotations

import re
from uuid import uuid4

from strategy_core import StreamDrop, StreamResolution

from trade_lab.domain.outcomes import DroppedPrediction, Outcome, ResolutionType
from trade_lab.services.inference.inference_engine import Prediction

_BAR_TYPE_RE = re.compile(r"^(\d+)t$")

#: The ratified label -> resolution-type mapping: a tradeable reversal is the
#: target (TP); both loss shapes (blowthrough, trap) are the stop (SL). The honest
#: resolver never force-labels, so no other resolution type exists.
_RESOLUTION_TYPE_BY_LABEL = {
    "tradeable_reversal": ResolutionType.TP_HIT,
    "aggressive_blowthrough": ResolutionType.SL_HIT,
    "trap_reversal": ResolutionType.SL_HIT,
}


def parse_bar_type(bar_type: str) -> int:
    """Map a contract ``bar_type`` like ``147t`` to its tick count ``147``."""

    match = _BAR_TYPE_RE.match(bar_type.strip().lower())
    if match is None:
        raise ValueError(f"unsupported forward_bar_type {bar_type!r}; expected '<n>t'")
    return int(match.group(1))


def resolution_to_outcome(resolution: StreamResolution, prediction: Prediction) -> Outcome:
    """Map one streaming resolution + its prediction onto the served Outcome.

    ``bars_to_resolution`` is served with the engine's ZERO-BASED semantics (the
    resolving bar's index within the forward window); the retired tracker served a
    1-based bar count. ``entry_price`` is the honest decision-time fill the
    excursions were anchored on. Fails loud on a label outside the engine's
    three-class vocabulary — a new label must be mapped deliberately, not guessed.
    """

    label = resolution.result.label
    try:
        resolution_type = _RESOLUTION_TYPE_BY_LABEL[label]
    except KeyError:
        raise ValueError(f"unmapped resolution label {label!r}") from None
    return Outcome(
        outcome_id=str(uuid4()),
        prediction_id=prediction.prediction_id,
        touch_id=prediction.touch_id,
        resolution_type=resolution_type,
        actual_class=label,
        predicted_class=prediction.predicted_class,
        correct=prediction.predicted_class == label,
        max_mfe_pts=resolution.result.max_mfe,
        max_mae_pts=resolution.result.max_mae,
        bars_to_resolution=resolution.result.bars_to_resolution,
        resolved_ts_utc=resolution.resolved_ts_utc,
        entry_price=resolution.entry_price,
    )


def drop_to_dropped(drop: StreamDrop, prediction: Prediction) -> DroppedPrediction:
    """Map one streaming drop + its prediction onto the served drop record."""

    return DroppedPrediction(
        prediction_id=prediction.prediction_id,
        touch_id=prediction.touch_id,
        reason=drop.reason,
        decision_ts_utc=drop.decision_ts_utc,
        entry_price=drop.entry_price,
    )
