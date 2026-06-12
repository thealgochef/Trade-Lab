"""W2 P2e (D-P-07): append-only prediction journal — record shape + 18:00 ET roll."""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

from trade_lab.domain.outcomes import DroppedPrediction, Outcome, ResolutionType
from trade_lab.services.inference.inference_engine import Prediction
from trade_lab.services.journal import PredictionJournal


def _prediction(ts: datetime) -> Prediction:
    return Prediction(
        prediction_id="pred-1",
        touch_id="touch-1",
        observation_id="obs-1",
        event_ts_utc=ts,
        predicted_class="tradeable_reversal",
        probabilities=MappingProxyType({"tradeable_reversal": 0.8, "trap_reversal": 0.2}),
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


def _outcome(ts: datetime) -> Outcome:
    return Outcome(
        outcome_id="out-1",
        prediction_id="pred-1",
        touch_id="touch-1",
        resolution_type=ResolutionType.TP_HIT,
        actual_class="tradeable_reversal",
        predicted_class="tradeable_reversal",
        correct=True,
        max_mfe_pts=16.0,
        max_mae_pts=3.5,
        bars_to_resolution=4,
        resolved_ts_utc=ts,
        entry_price=20_001.25,
    )


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_journal_records_prediction_outcome_and_drop_shapes(tmp_path: Path) -> None:
    journal = PredictionJournal(tmp_path / "journal")
    ts = datetime(2026, 6, 11, 14, 30, tzinfo=UTC)  # 10:30 ET -> trading day 2026-06-11

    journal.record_prediction(_prediction(ts), mode="live")
    journal.record_outcome(_outcome(ts), mode="live", bundle_id="bundle-a")
    journal.record_drop(
        DroppedPrediction("pred-2", "touch-2", "flatten", ts),
        mode="replay",
        bundle_id="bundle-a",
    )

    lines = _read_lines(tmp_path / "journal" / "2026-06-11.jsonl")
    assert [line["type"] for line in lines] == ["prediction", "outcome", "drop"]
    prediction = lines[0]
    assert prediction["mode"] == "live"
    assert prediction["bundle_id"] == "bundle-a"
    assert prediction["probabilities"] == {"tradeable_reversal": 0.8, "trap_reversal": 0.2}
    assert prediction["feature_values"] == {"interaction_dwell_time": 1.5}
    assert prediction["is_eligible"] is True
    outcome = lines[1]
    assert outcome["resolution_type"] == "tp_hit"
    assert outcome["correct"] is True
    assert outcome["entry_price"] == 20_001.25
    drop = lines[2]
    assert drop["mode"] == "replay"
    assert drop["reason"] == "flatten"


def test_journal_rotates_at_the_1800_et_roll(tmp_path: Path) -> None:
    journal = PredictionJournal(tmp_path)
    before_roll = datetime(2026, 6, 11, 21, 59, tzinfo=UTC)  # 17:59 ET Thu
    after_roll = datetime(2026, 6, 11, 22, 0, tzinfo=UTC)  # 18:00 ET Thu -> Friday file

    journal.record_prediction(_prediction(before_roll), mode="live")
    journal.record_prediction(_prediction(after_roll), mode="live")

    assert len(_read_lines(tmp_path / "2026-06-11.jsonl")) == 1
    assert len(_read_lines(tmp_path / "2026-06-12.jsonl")) == 1


def test_journal_append_failure_never_raises(tmp_path: Path) -> None:
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    journal = PredictionJournal(blocker / "journal")
    ts = datetime(2026, 6, 11, 14, 30, tzinfo=UTC)
    journal.record_prediction(_prediction(ts), mode="live")  # must not raise
