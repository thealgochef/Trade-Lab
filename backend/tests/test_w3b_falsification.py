"""W3b P3 — falsification / sensitivity suite.

A vacuously-green gate is worthless. These tests prove the per-touch parity gate
goes RED on a single-axis divergence and trips EXACTLY the corresponding
assertion (no spurious cross-axis failures). They drive the real join/assert core
(``parity.diff_touch_sets``) and the real journal parser (``parity.parse_journal``)
over synthetic touch sets, so no 3GB replay or QL data is needed — the gate logic
itself is under test.

Axes (>= 6 required): feature value, label flip, level price offset, touch-instant
shift, MFE/MAE excursion, dropped touch, added touch, model probability, gate
eligibility, and an orphan (unresolved) prediction.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from w3b.parity import (  # noqa: E402
    CONTRACT_FEATURES,
    ServingDay,
    ServingTouch,
    TrainingTouch,
    diff_touch_sets,
    parse_journal,
)

_TICK = 0.25
_BASE_FEATURES = {
    "int_time_within_2pts": 13.5525,
    "int_absorption_ratio": 0.171196,
    "app_avg_trade_size": 1.366962,
    "app_large_trade_vol_pct": 0.017843,
    "app_max_spread": 6.25,
}
_BASE_PROBA = {
    "tradeable_reversal": 0.80,
    "trap_reversal": 0.12,
    "aggressive_blowthrough": 0.08,
}


class _StubScorer:
    """Deterministic offline scorer: returns a fixed proba regardless of input.

    Lets the baseline match serving exactly (P2) so a perturbation is the ONLY
    thing that can turn the proba/eligible axes red.
    """

    def __init__(self, proba: dict[str, float]) -> None:
        self._proba = dict(proba)

    def score(self, features: dict[str, float]) -> dict[str, float]:
        return dict(self._proba)


def _instant() -> pd.Timestamp:
    return pd.Timestamp("2026-02-12T19:48:00.355786543Z")


def _training() -> TrainingTouch:
    rep_price = 24721.50
    return TrainingTouch(
        level_type="pdl",
        direction="long",
        session="asia",
        touch_instant=_instant(),
        representative_price=rep_price,
        label="trap_reversal",
        label_encoded=1,
        max_mfe=7.75,
        max_mae=23.50,
        features=dict(_BASE_FEATURES),
    )


def _serving_touch() -> ServingTouch:
    rep_price = 24721.50
    proba = dict(_BASE_PROBA)
    # eligible iff argmax==tradeable_reversal AND session==ny AND p>=0.70; this is
    # an asia touch so it must be ineligible — matches the stub-scored offline gate.
    return ServingTouch(
        prediction_id="pid-1",
        touch_id="touch-1",
        level_kind="pdl",
        direction="long",
        session="asia",
        touch_instant=_instant(),
        level_price_ticks=round(rep_price / _TICK),
        feature_values=dict(_BASE_FEATURES),
        predicted_class="tradeable_reversal",
        probabilities=proba,
        is_eligible=False,
        nan_count=0,
        actual_class="trap_reversal",
        max_mfe_pts=7.75,
        max_mae_pts=23.50,
        resolution_type="sl_hit",
    )


def _serving_day(survivors, drops=()) -> ServingDay:
    survivors = list(survivors)
    drops = list(drops)
    return ServingDay(
        survivors=survivors,
        drops=drops,
        n_predictions=len(survivors) + len(drops),
        n_orphan_predictions=0,
    )


def _diff(training, serving):
    return diff_touch_sets(
        "2026-02-13", training, serving, cache_present=True, scorer=_StubScorer(_BASE_PROBA)
    )


# ── Baseline: a perfectly aligned day is GREEN ──────────────────────────────
def test_baseline_is_green():
    diff = _diff([_training()], _serving_day([_serving_touch()]))
    assert diff.green, diff.summary()
    assert diff.matched == 1
    assert diff.max_instant_diff_ns == 0


# ── Single-axis perturbations each trip EXACTLY their assertion ──────────────
def test_feature_perturbation_red():
    s = _serving_touch()
    bad = dict(s.feature_values)
    bad["int_absorption_ratio"] += 1e-9  # one ULP-ish nudge; tol is 0
    diff = _diff([_training()], _serving_day([replace(s, feature_values=bad)]))
    assert not diff.green
    assert diff.feature_mismatches and not diff.label_mismatches
    assert not diff.price_mismatches and not diff.excursion_mismatches
    assert diff.feature_mismatches[0].axis == "feature:int_absorption_ratio"


def test_label_flip_red():
    s = replace(_serving_touch(), actual_class="aggressive_blowthrough")
    diff = _diff([_training()], _serving_day([s]))
    assert not diff.green
    assert diff.label_mismatches and not diff.feature_mismatches


def test_price_offset_one_tick_red():
    s = _serving_touch()
    s = replace(s, level_price_ticks=s.level_price_ticks + 1)
    diff = _diff([_training()], _serving_day([s]))
    assert not diff.green
    assert diff.price_mismatches and not diff.feature_mismatches


def test_timestamp_shift_red():
    s = _serving_touch()
    s = replace(s, touch_instant=s.touch_instant + pd.Timedelta(seconds=1))
    diff = _diff([_training()], _serving_day([s]))
    assert not diff.green
    assert diff.instant_mismatches and not diff.feature_mismatches
    assert diff.max_instant_diff_ns == 1_000_000_000


def test_mfe_perturbation_red():
    s = replace(_serving_touch(), max_mfe_pts=8.0)
    diff = _diff([_training()], _serving_day([s]))
    assert not diff.green
    assert diff.excursion_mismatches and not diff.label_mismatches


def test_mae_perturbation_red():
    s = replace(_serving_touch(), max_mae_pts=24.0)
    diff = _diff([_training()], _serving_day([s]))
    assert not diff.green
    assert diff.excursion_mismatches


def test_dropped_touch_red():
    # serving lost a touch the cache kept -> unmatched_training
    diff = _diff([_training()], _serving_day([]))
    assert not diff.green
    assert diff.unmatched_training == ["pdl|long"]
    assert not diff.unmatched_serving


def test_added_touch_red():
    # serving emitted a survivor the cache never kept -> unmatched_serving
    extra = replace(
        _serving_touch(),
        prediction_id="pid-2",
        touch_id="touch-2",
        level_kind="pdh",
        direction="short",
    )
    diff = _diff([_training()], _serving_day([_serving_touch(), extra]))
    assert not diff.green
    assert diff.unmatched_serving == ["pdh|short"]
    assert not diff.unmatched_training


def test_probability_perturbation_red():
    s = _serving_touch()
    bad = dict(s.probabilities)
    bad["tradeable_reversal"] -= 1e-3  # well beyond the 1e-6 P2 tolerance
    diff = _diff([_training()], _serving_day([replace(s, probabilities=bad)]))
    assert not diff.green
    assert diff.proba_mismatches


def test_eligibility_flip_red():
    # serving claims eligible while the offline gate (stub proba, asia) says no
    s = replace(_serving_touch(), is_eligible=True)
    diff = _diff([_training()], _serving_day([s]))
    assert not diff.green
    assert diff.eligible_mismatches and not diff.proba_mismatches


def test_orphan_prediction_red():
    day = _serving_day([_serving_touch()])
    day.n_orphan_predictions = 1  # a prediction that never resolved or dropped
    diff = _diff([_training()], day)
    assert not diff.green
    assert diff.orphan_predictions == 1


# ── The journal parser itself: a drop reconciles, an outcome survives ────────
def test_parse_journal_partitions_survivors_and_drops(tmp_path):
    import json

    path = tmp_path / "2026-02-13.jsonl"
    pred = {
        "type": "prediction", "prediction_id": "p1", "touch_id": "t1",
        "observation_id": "o1", "ts_utc": "2026-02-13T14:35:00.000000001+00:00",
        "predicted_class": "tradeable_reversal",
        "probabilities": {"tradeable_reversal": 0.8, "trap_reversal": 0.1,
                          "aggressive_blowthrough": 0.1},
        "feature_values": dict(_BASE_FEATURES), "is_eligible": True,
        "direction": "LONG", "session": "ny", "level_kind": "pdl",
        "level_price_ticks": 98886, "contract_id": "b", "nan_count": 0,
    }
    pred2 = {**pred, "prediction_id": "p2", "touch_id": "t2", "level_kind": "pdh",
             "direction": "SHORT"}
    outcome = {
        "type": "outcome", "prediction_id": "p1", "touch_id": "t1", "outcome_id": "oc1",
        "ts_utc": "2026-02-13T14:40:00+00:00", "resolution_type": "tp_hit",
        "actual_class": "tradeable_reversal", "predicted_class": "tradeable_reversal",
        "correct": True, "max_mfe_pts": 15.0, "max_mae_pts": 3.0,
        "bars_to_resolution": 4, "entry_price": 24700.0,
    }
    drop = {
        "type": "drop", "prediction_id": "p2", "touch_id": "t2",
        "ts_utc": "2026-02-13T14:36:00+00:00", "reason": "no_resolution",
        "entry_price": None,
    }
    with path.open("w", encoding="utf-8") as fh:
        for rec in (pred, pred2, outcome, drop):
            fh.write(json.dumps(rec) + "\n")

    day = parse_journal(path)
    assert day.n_predictions == 2
    assert day.n_orphan_predictions == 0
    assert len(day.survivors) == 1 and day.survivors[0].touch_id == "t1"
    # direction is normalized to lowercase across the seam
    assert day.survivors[0].direction == "long"
    assert len(day.drops) == 1 and day.drops[0].reason == "no_resolution"
    assert all(f in day.survivors[0].feature_values for f in CONTRACT_FEATURES)
