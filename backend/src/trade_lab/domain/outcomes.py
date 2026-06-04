"""Resolved-outcome domain object for MAE-first forward labeling.

An :class:`Outcome` is the ground-truth label a :class:`~trade_lab.services.
inference.inference_engine.Prediction` resolves to once enough contract
``forward_bar_type`` bars have closed after the touch. It mirrors the offline
``dashboard_utility_labeling`` ladder (MAE checked first) so a live/replay
resolution matches how the model was trained. Like a prediction it is frozen and
path-free, carrying the ``prediction_id``/``touch_id`` it belongs to plus the
diagnostics (MFE/MAE in points, bars scanned) and the correctness flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ResolutionType(StrEnum):
    """How a prediction's forward scan terminated."""

    TP_HIT = "tp_hit"
    SL_HIT = "sl_hit"
    SESSION_END = "session_end"
    NO_RESOLUTION = "no_resolution"


@dataclass(frozen=True, slots=True)
class Outcome:
    """A frozen, MAE-first resolution of one open prediction."""

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
