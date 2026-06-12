"""Append-only prediction journal (W2 P2e, D-P-07).

One JSONL file per trading day (18:00 ET roll) under a settings-driven directory
(``TRADE_LAB_JOURNAL_PATH``). Records predictions (features, probabilities,
eligibility), outcomes, and drops, each tagged ``replay``/``live`` plus the bundle
id, so serving evidence survives restarts. Write-only this window — W3's soak
reads it. Line-buffered append per record (predictions are rare); fsync is
deliberately not required. Journal failures are logged and never propagate.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from trade_lab.domain.outcomes import DroppedPrediction, Outcome
from trade_lab.domain.trading_day import trading_day_for
from trade_lab.services.inference.inference_engine import Prediction

logger = logging.getLogger(__name__)


class PredictionJournal:
    """Append prediction/outcome/drop records to per-trading-day JSONL files."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def record_prediction(self, prediction: Prediction, *, mode: str) -> None:
        self._append(
            prediction.event_ts_utc,
            {
                "type": "prediction",
                "mode": mode,
                "bundle_id": prediction.model_id,
                "ts_utc": prediction.event_ts_utc,
                "prediction_id": prediction.prediction_id,
                "touch_id": prediction.touch_id,
                "observation_id": prediction.observation_id,
                "predicted_class": prediction.predicted_class,
                "probabilities": dict(prediction.probabilities),
                "feature_values": dict(prediction.feature_values),
                "is_eligible": prediction.is_eligible,
                "direction": prediction.direction,
                "session": prediction.session,
                "level_kind": prediction.level_kind,
                "level_price_ticks": prediction.level_price_ticks,
                "contract_id": prediction.contract_id,
                "nan_count": prediction.nan_count,
            },
        )

    def record_outcome(self, outcome: Outcome, *, mode: str, bundle_id: str | None) -> None:
        self._append(
            outcome.resolved_ts_utc,
            {
                "type": "outcome",
                "mode": mode,
                "bundle_id": bundle_id,
                "ts_utc": outcome.resolved_ts_utc,
                "outcome_id": outcome.outcome_id,
                "prediction_id": outcome.prediction_id,
                "touch_id": outcome.touch_id,
                "resolution_type": outcome.resolution_type.value,
                "actual_class": outcome.actual_class,
                "predicted_class": outcome.predicted_class,
                "correct": outcome.correct,
                "max_mfe_pts": outcome.max_mfe_pts,
                "max_mae_pts": outcome.max_mae_pts,
                "bars_to_resolution": outcome.bars_to_resolution,
                "entry_price": outcome.entry_price,
            },
        )

    def record_drop(
        self, drop: DroppedPrediction, *, mode: str, bundle_id: str | None
    ) -> None:
        self._append(
            drop.decision_ts_utc,
            {
                "type": "drop",
                "mode": mode,
                "bundle_id": bundle_id,
                "ts_utc": drop.decision_ts_utc,
                "prediction_id": drop.prediction_id,
                "touch_id": drop.touch_id,
                "reason": drop.reason,
                "entry_price": drop.entry_price,
            },
        )

    def _append(self, ts_utc: datetime | None, payload: dict[str, Any]) -> None:
        try:
            day = trading_day_for(ts_utc) if ts_utc is not None else None
            name = f"{day.isoformat()}.jsonl" if day is not None else "undated.jsonl"
            self._root.mkdir(parents=True, exist_ok=True)
            line = json.dumps(payload, default=_json_default, separators=(",", ":"))
            with (self._root / name).open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:
            logger.warning("prediction journal append failed", exc_info=True)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return str(value)
