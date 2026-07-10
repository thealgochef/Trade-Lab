"""Unit tests for the REPORT P2 performance aggregator (synthetic journals).

Coverage per the window spec: all resolution types, drops, mode mixing,
multi-day (incl. the 18:00 ET boundary), empty dir, malformed lines (skip +
count), plus the pricing-source ladder, duplicate/orphan/conflict buckets,
filters, the cohort funnel, and the OOS comparison section.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from trade_lab.services.performance import (
    InvalidPerformanceFilter,
    JournalDirectoryNotFound,
    PerformanceFilters,
    aggregate_performance,
)

BUNDLE = "NQ_TEST_BUNDLE"


def _prediction(pid: str, **over) -> dict:
    row = {
        "type": "prediction",
        "mode": "live",
        "bundle_id": BUNDLE,
        "ts_utc": "2026-06-18T08:00:00+00:00",
        "prediction_id": pid,
        "touch_id": f"touch-{pid}",
        "observation_id": f"obs-{pid}",
        "predicted_class": "tradeable_reversal",
        "probabilities": {"tradeable_reversal": 0.8},
        "feature_values": {"f": 1.0},
        "is_eligible": True,
        "direction": "long",
        "session": "ny",
        "level_kind": "pdl",
        "level_price_ticks": 100_000,
        "contract_id": BUNDLE,
        "nan_count": 0,
    }
    row.update(over)
    return row


def _outcome(pid: str, **over) -> dict:
    row = {
        "type": "outcome",
        "mode": "live",
        "bundle_id": BUNDLE,
        "ts_utc": "2026-06-18T08:10:00+00:00",
        "outcome_id": f"out-{pid}",
        "prediction_id": pid,
        "touch_id": f"touch-{pid}",
        "resolution_type": "tp_hit",
        "actual_class": "tradeable_reversal",
        "predicted_class": "tradeable_reversal",
        "correct": True,
        "max_mfe_pts": 16.0,
        "max_mae_pts": 4.0,
        "bars_to_resolution": 3,
        "entry_price": 20000.0,
    }
    row.update(over)
    return row


def _drop(pid: str, **over) -> dict:
    row = {
        "type": "drop",
        "mode": "live",
        "bundle_id": BUNDLE,
        "ts_utc": "2026-06-18T08:05:00+00:00",
        "prediction_id": pid,
        "touch_id": f"touch-{pid}",
        "reason": "no_fill",
        "entry_price": None,
    }
    row.update(over)
    return row


def _write(journal: Path, name: str, rows: list) -> None:
    journal.mkdir(parents=True, exist_ok=True)
    lines = [row if isinstance(row, str) else json.dumps(row) for row in rows]
    (journal / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _bundle_dir(root: Path, name: str = BUNDLE, tp: float = 15.0, sl: float = 15.0) -> Path:
    bundle = root / name
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "strategy.json").write_text(
        json.dumps(
            {
                "label_policy": {"tp_points": tp, "sl_points": sl},
                "point_value": 20.0,
                "inference": {"eligible_class": "tradeable_reversal"},
            }
        ),
        encoding="utf-8",
    )
    return bundle


def test_missing_journal_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(JournalDirectoryNotFound):
        aggregate_performance(tmp_path / "absent")


def test_invalid_filters_raise() -> None:
    with pytest.raises(InvalidPerformanceFilter):
        PerformanceFilters(mode="bogus").validate()
    with pytest.raises(InvalidPerformanceFilter):
        PerformanceFilters(eligibility="bogus").validate()
    with pytest.raises(InvalidPerformanceFilter):
        PerformanceFilters(from_day=date(2026, 6, 19), to_day=date(2026, 6, 18)).validate()


def test_empty_dir_yields_zeroed_report(tmp_path: Path) -> None:
    journal = tmp_path / "journal"
    journal.mkdir()
    report = aggregate_performance(journal)
    assert report.headline["predictions"] == 0
    assert report.headline["resolved_trades"] == 0
    assert report.headline["win_rate"] == {"value": None, "numerator": 0, "denominator": 0}
    assert report.series == []
    assert report.funnel["predictions"] == 0
    assert report.anomalies["files_scanned"] == 0
    assert report.oos_comparison is None


def test_resolutions_pricing_and_headline(tmp_path: Path) -> None:
    journal = tmp_path / "journal"
    bundle = _bundle_dir(tmp_path / "models")
    _write(
        journal,
        "2026-06-18.jsonl",
        [
            _prediction("p1"),
            _outcome("p1", resolution_type="tp_hit", correct=True),
            _prediction("p2", predicted_class="trap_reversal", direction="short"),
            _outcome(
                "p2",
                resolution_type="sl_hit",
                predicted_class="trap_reversal",
                actual_class="aggressive_blowthrough",
                correct=False,
                ts_utc="2026-06-18T09:00:00+00:00",
            ),
            _prediction("p3"),
            _outcome("p3", resolution_type="session_end"),  # retired type -> bucketed
            _prediction("p4"),
            _drop("p4", reason="flatten"),
        ],
    )
    report = aggregate_performance(journal, bundle_dir=bundle)
    headline = report.headline
    assert headline["predictions"] == 4
    assert headline["resolved_trades"] == 2  # session_end excluded, counted below
    assert report.anomalies["scoped"]["unknown_resolution_trades"] == 1
    assert headline["win_rate"] == {"value": 0.5, "numerator": 1, "denominator": 2}
    assert headline["class_accuracy"] == {"value": 0.5, "numerator": 1, "denominator": 2}
    # +15 (tp) - 15 (sl) at the fallback contract's tp/sl
    assert headline["net_points"] == {"value": 0.0, "n_priced": 2}
    assert headline["profit_factor"]["value"] == 1.0
    assert headline["avg_win_pts"] == {"value": 15.0, "n": 1}
    assert headline["avg_loss_pts"] == {"value": 15.0, "n": 1}
    assert headline["point_value"] == 20.0
    assert headline["direction"]["long"]["wins"] == 1
    assert headline["direction"]["short"]["expectancy_pts"] == {"value": -15.0, "n": 1}
    assert report.breakdowns["drop_reasons"] == {"flatten": 1}
    assert report.pricing["sources"]["fallback_contract"] >= 2
    # per-class precision/recall carry denominators
    trap = report.breakdowns["per_class"]["trap_reversal"]
    assert trap["precision"] == {"value": 0.0, "numerator": 0, "denominator": 1}
    tradeable = report.breakdowns["per_class"]["tradeable_reversal"]
    assert tradeable["precision"] == {"value": 1.0, "numerator": 1, "denominator": 1}
    assert tradeable["recall"]["denominator"] == 1


def test_pricing_source_ladder(tmp_path: Path) -> None:
    journal = tmp_path / "journal"
    models_root = tmp_path / "models"
    _bundle_dir(models_root, name=BUNDLE, tp=10.0, sl=5.0)
    fallback = _bundle_dir(tmp_path / "fallback_bundle", name="FALLBACK", tp=15.0, sl=15.0)
    _write(
        journal,
        "2026-06-18.jsonl",
        [
            # row-level values win over everything
            _prediction("p1"),
            _outcome("p1", tp_points=7.0, sl_points=3.0, resolution_type="tp_hit"),
            # bundle contract from models_root (tp 10)
            _prediction("p2"),
            _outcome("p2", ts_utc="2026-06-18T09:00:00+00:00"),
            # unknown bundle + fallback contract (tp 15)
            _prediction("p3", bundle_id="GHOST"),
            _outcome("p3", bundle_id="GHOST", ts_utc="2026-06-18T10:00:00+00:00"),
        ],
    )
    with_fallback = aggregate_performance(journal, models_root=models_root, bundle_dir=fallback)
    assert with_fallback.headline["net_points"] == {"value": 7.0 + 10.0 + 15.0, "n_priced": 3}
    assert with_fallback.pricing["sources"] == {
        "row": 1,
        "bundle_contract": 1,
        "fallback_contract": 1,
    }
    # without any contract source, unknown-bundle rows land in the unpriced bucket
    without = aggregate_performance(journal, models_root=models_root)
    assert without.headline["priced_trades"] == 2
    assert without.headline["unpriced_trades"] == 1
    assert without.anomalies["scoped"]["unpriced_trades"] == 1
    assert without.headline["resolved_trades"] == 3  # bucketed, not dropped


def test_mode_mixing_and_filters(tmp_path: Path) -> None:
    journal = tmp_path / "journal"
    _write(
        journal,
        "2026-06-18.jsonl",
        [
            _prediction("p1", mode="replay"),
            _outcome("p1", mode="replay"),
            _prediction("p2", mode="live", session="london", is_eligible=False),
            _outcome("p2", mode="live", ts_utc="2026-06-18T09:00:00+00:00"),
        ],
    )
    replay = aggregate_performance(journal, filters=PerformanceFilters(mode="replay"))
    assert replay.headline["predictions"] == 1
    assert replay.headline["resolved_trades"] == 1
    london = aggregate_performance(journal, filters=PerformanceFilters(session="london"))
    assert london.headline["resolved_trades"] == 1
    assert london.breakdowns["per_session"].keys() == {"london"}
    eligible = aggregate_performance(journal, filters=PerformanceFilters(eligibility="eligible"))
    assert eligible.headline["predictions"] == 1
    assert eligible.headline["resolved_trades"] == 1
    ineligible = aggregate_performance(
        journal, filters=PerformanceFilters(eligibility="ineligible")
    )
    assert ineligible.headline["resolved_trades"] == 1
    assert ineligible.headline["eligible_trades"] == 0
    bundled = aggregate_performance(journal, filters=PerformanceFilters(bundle_id="OTHER"))
    assert bundled.headline["predictions"] == 0
    assert bundled.headline["resolved_trades"] == 0


def test_multi_day_series_boundary_and_day_win_pct(tmp_path: Path) -> None:
    journal = tmp_path / "journal"
    bundle = _bundle_dir(tmp_path / "models")
    # 17:59 ET prediction belongs to trading day 06-17; its 18:05 ET outcome to 06-18.
    _write(
        journal,
        "2026-06-17.jsonl",
        [
            _prediction("p1", ts_utc="2026-06-17T21:59:00+00:00"),
            _prediction("p0", ts_utc="2026-06-17T15:00:00+00:00"),
            _outcome("p0", ts_utc="2026-06-17T15:30:00+00:00", resolution_type="sl_hit"),
        ],
    )
    _write(
        journal,
        "2026-06-18.jsonl",
        [_outcome("p1", ts_utc="2026-06-17T22:05:00+00:00", resolution_type="tp_hit")],
    )
    report = aggregate_performance(journal, bundle_dir=bundle)
    days = {row["trading_day"]: row for row in report.series}
    assert set(days) == {"2026-06-17", "2026-06-18"}
    assert days["2026-06-17"]["net_points"] == -15.0
    assert days["2026-06-17"]["predictions"] == 2
    assert days["2026-06-18"]["net_points"] == 15.0
    assert days["2026-06-18"]["predictions"] == 0
    assert days["2026-06-18"]["cumulative_net_points"] == 0.0
    assert report.headline["day_win_pct"] == {"value": 0.5, "numerator": 1, "denominator": 2}
    # date filter on the boundary: only the outcome's trading day selects the trade
    later = aggregate_performance(
        journal,
        bundle_dir=bundle,
        filters=PerformanceFilters(from_day=date(2026, 6, 18)),
    )
    assert later.headline["resolved_trades"] == 1
    assert later.headline["predictions"] == 0
    # cohort funnel still follows the 06-17 prediction to its 06-18 fate
    earlier = aggregate_performance(
        journal,
        bundle_dir=bundle,
        filters=PerformanceFilters(to_day=date(2026, 6, 17)),
    )
    assert earlier.headline["resolved_trades"] == 1  # p0 only
    assert earlier.funnel["predictions"] == 2
    assert earlier.funnel["resolved"] == 2  # p1's out-of-window outcome counts as its fate


def test_malformed_duplicates_orphans_conflicts(tmp_path: Path) -> None:
    journal = tmp_path / "journal"
    _write(
        journal,
        "2026-06-18.jsonl",
        [
            '{"type":"prediction","prediction_id":"p1",',  # torn tail line
            '"just a string"',  # valid JSON, not an object
            json.dumps(
                {"type": "mystery", "prediction_id": "px", "ts_utc": "2026-06-18T08:00:00+00:00"}
            ),
            json.dumps({"type": "prediction", "ts_utc": "2026-06-18T08:00:00+00:00"}),  # no id
            json.dumps({"type": "prediction", "prediction_id": "p2"}),  # no ts -> undated
            _prediction("p3"),
            _prediction("p3"),  # duplicate prediction id
            _outcome("p3"),
            _outcome("p3", resolution_type="sl_hit"),  # duplicate outcome; first wins
            _outcome("orphan-1"),  # no prediction row
            _drop("orphan-2"),  # no prediction row
            _prediction("p4"),
            _outcome("p4", ts_utc="2026-06-18T09:00:00+00:00"),
            _drop("p4"),  # conflict: outcome AND drop
        ],
    )
    report = aggregate_performance(journal)
    anomalies = report.anomalies
    assert anomalies["malformed_lines"] == 2
    assert anomalies["unknown_type_rows"] == 1
    assert anomalies["rows_missing_ids"] == 1
    assert anomalies["undated_rows"] == 1
    assert anomalies["duplicate_prediction_rows"] == 1
    assert anomalies["duplicate_outcome_rows"] == 1
    assert anomalies["orphan_outcomes"] == 1
    assert anomalies["orphan_drops"] == 1
    assert anomalies["outcome_drop_conflicts"] == 1
    # first outcome wins: p3 stays tp_hit; conflict drop is excluded from reasons
    assert report.headline["win_rate"]["numerator"] == 3  # p3, orphan-1, p4
    assert report.breakdowns["drop_reasons"] == {"no_fill": 1}  # orphan-2 only
    # orphan outcome surfaces under unknown session/direction, never dropped
    assert report.breakdowns["per_session"]["unknown"]["resolved"] == 1
    # session/eligibility filters cannot classify orphans; they are excluded + counted
    gated = aggregate_performance(journal, filters=PerformanceFilters(eligibility="eligible"))
    assert gated.anomalies["scoped"]["excluded_unknown_eligibility"] == 1


def test_profit_factor_none_when_no_losses(tmp_path: Path) -> None:
    journal = tmp_path / "journal"
    bundle = _bundle_dir(tmp_path / "models")
    _write(journal, "2026-06-18.jsonl", [_prediction("p1"), _outcome("p1")])
    report = aggregate_performance(journal, bundle_dir=bundle)
    assert report.headline["profit_factor"]["value"] is None
    assert report.headline["profit_factor"]["gross_loss"] == 0.0
    assert report.headline["day_win_pct"] == {"value": 1.0, "numerator": 1, "denominator": 1}


def test_oos_comparison_section(tmp_path: Path) -> None:
    journal = tmp_path / "journal"
    bundle = _bundle_dir(tmp_path / "models")
    (bundle / "evaluation.json").write_text(
        json.dumps(
            {
                "gated_oos": {
                    "trade_count": 3,
                    "precision": 0.333,
                    "expectancy_15_30_pts": -5.0,
                    "profit_factor_15_30": 0.5,
                    "coverage": 0.073,
                    "confidence_gate": 0.7,
                    "eligible_sessions": ["ny"],
                    "n_samples": 41,
                },
                "quality_gates": {
                    "all_passed": False,
                    "gates": {
                        "Precision >= 0.55": {
                            "passed": False,
                            "value": 0.31,
                            "threshold": 0.55,
                            "operator": ">=",
                        }
                    },
                },
                "oos_three_class_balance": {"tradeable_reversal": 17},
            }
        ),
        encoding="utf-8",
    )
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    pq.write_table(
        pa.table(
            {
                "label": ["tradeable_reversal", "trap_reversal"],
                "pred_label": ["trap_reversal", "trap_reversal"],
            }
        ),
        bundle / "oos_predictions.parquet",
    )
    _write(
        journal,
        "2026-06-18.jsonl",
        [
            _prediction("p1"),
            _outcome("p1", actual_class="tradeable_reversal"),
            _prediction("p2", is_eligible=False),
            _outcome(
                "p2",
                resolution_type="sl_hit",
                actual_class="trap_reversal",
                correct=False,
                ts_utc="2026-06-18T09:00:00+00:00",
            ),
        ],
    )
    report = aggregate_performance(journal, bundle_dir=bundle)
    comparison = report.oos_comparison
    assert comparison is not None
    assert comparison["bundle_id"] == bundle.name
    assert comparison["oos"]["available"] is True
    assert comparison["oos"]["gated"]["trade_count"] == 3
    assert comparison["oos"]["gated"]["expectancy_pts"] == -5.0
    assert comparison["oos"]["quality_gates"]["all_passed"] is False
    assert comparison["oos"]["class_distribution"] == {
        "actual": {"tradeable_reversal": 1, "trap_reversal": 1},
        "predicted": {"trap_reversal": 2},
        "n": 2,
    }
    assert comparison["oos"]["mfe_mae"] is None  # not recorded in OOS artifacts
    journal_side = comparison["journal"]
    assert journal_side["eligible_trades"] == 1
    assert journal_side["gated_hit_rate"]["numerator"] == 1
    assert journal_side["gated_hit_rate"]["denominator"] == 1
    assert journal_side["gated_expectancy_pts"] == {
        "value": 15.0,
        "n": 1,
        "pricing_rule": "journal proxy at the contract's own tp/sl",
    }
    assert journal_side["class_distribution"]["n"] == 2
    assert journal_side["mfe_mae"]["mfe_pts"]["n"] == 2


def test_undecodable_bytes_are_salvaged_and_counted(tmp_path: Path) -> None:
    # Verify fix (major): a single non-UTF-8 byte previously raised
    # UnicodeDecodeError out of the aggregator (a permanent 500 at the
    # endpoint). Bad bytes must degrade to malformed lines + a flagged file,
    # and healthy rows in the same file stay readable.
    journal = tmp_path / "journal"
    _write(journal, "2026-06-18.jsonl", [_prediction("p1"), _outcome("p1")])
    with (journal / "2026-06-18.jsonl").open("ab") as handle:
        handle.write(b"\xff\xfe garbage line\n")
    report = aggregate_performance(journal)
    assert report.anomalies["decode_error_files"] == 1
    assert report.anomalies["malformed_lines"] == 1
    assert report.headline["resolved_trades"] == 1  # healthy rows salvaged


def test_unreadable_file_is_counted_not_silent(tmp_path: Path) -> None:
    # Verify fix (major): an OSError-unreadable file previously vanished with
    # only a server log line while files_scanned claimed it was read. A
    # directory named *.jsonl matches the glob and raises OSError on read.
    journal = tmp_path / "journal"
    _write(journal, "2026-06-18.jsonl", [_prediction("p1"), _outcome("p1")])
    (journal / "2026-06-19.jsonl").mkdir()
    report = aggregate_performance(journal)
    assert report.anomalies["files_scanned"] == 2
    assert report.anomalies["unreadable_files"] == 1
    assert report.headline["resolved_trades"] == 1


def test_malformed_quality_gates_degrades_not_crashes(tmp_path: Path) -> None:
    # Verify fix (major): a non-dict quality_gates.gates in evaluation.json
    # previously raised AttributeError out of aggregate_performance, turning
    # the WHOLE report into a 500. It must degrade the OOS section only.
    journal = tmp_path / "journal"
    bundle = _bundle_dir(tmp_path / "models")
    _write(journal, "2026-06-18.jsonl", [_prediction("p1"), _outcome("p1")])
    for bad_gates in (["oops"], None, "oops"):
        (bundle / "evaluation.json").write_text(
            json.dumps({"quality_gates": {"all_passed": False, "gates": bad_gates}}),
            encoding="utf-8",
        )
        report = aggregate_performance(journal, bundle_dir=bundle)
        assert report.headline["resolved_trades"] == 1
        comparison = report.oos_comparison
        assert comparison is not None
        assert comparison["oos"]["quality_gates"] == {"all_passed": False, "gates": {}}


def test_payload_shape(tmp_path: Path) -> None:
    journal = tmp_path / "journal"
    journal.mkdir()
    payload = aggregate_performance(journal).to_payload()
    assert set(payload) == {
        "applied_filters",
        "headline",
        "series",
        "breakdowns",
        "funnel",
        "anomalies",
        "pricing",
        "oos_comparison",
    }
    assert set(payload["applied_filters"]) == {
        "mode",
        "from",
        "to",
        "bundle_id",
        "session",
        "eligibility",
    }
