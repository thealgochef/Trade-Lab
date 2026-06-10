"""Resolved-outcome domain objects for honest decision-time labeling.

An :class:`Outcome` is the ground-truth label a :class:`~trade_lab.services.
inference.inference_engine.Prediction` resolves to on the Strategy-Core streaming
honest resolver (D1b): entry is the realistic front-month trade print at the
decision instant (the honest fill, NOT the level price), excursions are MAE-first
over the contract ``forward_bar_type`` bars strictly inside the decision->cutoff
window, exactly the batch ``resolve_honest_outcome`` semantics. Like a prediction
it is frozen and path-free, carrying the ``prediction_id``/``touch_id`` it belongs
to plus the diagnostics (MFE/MAE in points, bars scanned) and the correctness flag.

``bars_to_resolution`` is the engine's ZERO-BASED index of the resolving bar
within the forward window (the retired tracker reported a 1-based bar count).

A :class:`DroppedPrediction` is a prediction the resolver yields NO tradeable
outcome for — surfaced explicitly with its reason instead of being force-labeled:
``flatten``/``cutoff``/``no_fill`` at registration (no entry queried/filled),
``no_forward``/``no_resolution`` at the cutoff (entry carried).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ResolutionType(StrEnum):
    """How a prediction's forward scan terminated.

    D1b: ``SESSION_END`` and ``NO_RESOLUTION`` are RETIRED — the honest resolver
    never force-labels; an unresolved-at-cutoff setup surfaces as a
    :class:`DroppedPrediction` (reason ``no_resolution``) instead.
    """

    TP_HIT = "tp_hit"
    SL_HIT = "sl_hit"


@dataclass(frozen=True, slots=True)
class Outcome:
    """A frozen, MAE-first resolution of one open prediction (honest entry)."""

    outcome_id: str
    prediction_id: str
    touch_id: str
    resolution_type: ResolutionType
    actual_class: str
    predicted_class: str
    correct: bool
    max_mfe_pts: float
    max_mae_pts: float
    bars_to_resolution: int
    resolved_ts_utc: datetime
    entry_price: float


@dataclass(frozen=True, slots=True)
class DroppedPrediction:
    """A prediction the honest resolver dropped instead of resolving.

    ``entry_price`` is ``None`` for registration-time drops (``flatten``/
    ``cutoff``/``no_fill`` — no fill was queried or none existed) and carries the
    honest fill for terminal drops (``no_forward``/``no_resolution``).
    """

    prediction_id: str
    touch_id: str
    reason: str
    decision_ts_utc: datetime
    entry_price: float | None = None
