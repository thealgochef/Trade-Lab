"""Contract tests for GET /api/v1/performance (REPORT P3).

Read-only endpoint over the journal directory + model store: 404 clean on a
missing journal dir, 400 on out-of-domain filters/path-like bundle ids,
row-filter-only semantics for well-formed bundle ids absent from the model
store (retired bundles' journal history stays queryable; no OOS panel),
per-request file opens (a row appended between calls shows up on the next
call), torn-tail-line tolerance, and a pinned payload shape with no
path/secret leakage.
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from trade_lab.api.app import create_app
from trade_lab.config import Settings

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


def _make_bundle(models_root: Path, name: str = BUNDLE) -> Path:
    bundle = models_root / name
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "strategy.json").write_text(
        json.dumps(
            {
                "label_policy": {"tp_points": 15.0, "sl_points": 15.0},
                "point_value": 20.0,
                "inference": {"eligible_class": "tradeable_reversal"},
            }
        ),
        encoding="utf-8",
    )
    return bundle


def _client(tmp_path: Path, *, create_journal: bool = True) -> tuple[TestClient, Path]:
    journal = tmp_path / "journal"
    if create_journal:
        journal.mkdir(parents=True, exist_ok=True)
    models_root = tmp_path / "models"
    _make_bundle(models_root)
    settings = Settings(_env_file=None, journal_path=journal, models_path=models_root)
    return TestClient(create_app(settings)), journal


def _append(journal: Path, rows: list) -> None:
    lines = [row if isinstance(row, str) else json.dumps(row) for row in rows]
    with (journal / "2026-06-18.jsonl").open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")


def test_404_when_journal_dir_missing(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, create_journal=False)
    response = client.get("/api/v1/performance")
    assert response.status_code == 404
    assert response.json()["detail"] == "journal directory not found"


def test_empty_journal_returns_zeroed_payload(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    response = client.get("/api/v1/performance")
    assert response.status_code == 200
    payload = response.json()
    assert payload["headline"]["resolved_trades"] == 0
    assert payload["series"] == []
    assert payload["oos_comparison"] is None


def test_payload_shape_and_no_path_leakage(tmp_path: Path) -> None:
    client, journal = _client(tmp_path)
    _append(journal, [_prediction("p1"), _outcome("p1")])
    response = client.get("/api/v1/performance", params={"bundle": BUNDLE})
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "applied_filters",
        "headline",
        "series",
        "breakdowns",
        "funnel",
        "anomalies",
        "pricing",
        "oos_comparison",
        "executions",
    }
    # EXEC P3d: no executions directory yet -> the section is dark, not zeroed.
    assert payload["executions"] is None
    assert payload["applied_filters"] == {
        "mode": "all",
        "from": None,
        "to": None,
        "bundle_id": BUNDLE,
        "session": None,
        "eligibility": "all",
    }
    win_rate = payload["headline"]["win_rate"]
    assert set(win_rate) == {"value", "numerator", "denominator"}
    assert payload["oos_comparison"]["bundle_id"] == BUNDLE
    text = json.dumps(payload).lower()
    assert "c:\\" not in text
    assert "/users/" not in text


def test_filters_flow_through(tmp_path: Path) -> None:
    client, journal = _client(tmp_path)
    _append(
        journal,
        [
            _prediction("p1", mode="replay"),
            _outcome("p1", mode="replay"),
            _prediction("p2"),
            _outcome("p2", ts_utc="2026-06-18T09:00:00+00:00"),
        ],
    )
    live = client.get("/api/v1/performance", params={"mode": "live"}).json()
    assert live["headline"]["resolved_trades"] == 1
    windowed = client.get(
        "/api/v1/performance", params={"from": "2026-06-19", "to": "2026-06-20"}
    ).json()
    assert windowed["headline"]["resolved_trades"] == 0
    assert windowed["applied_filters"]["from"] == "2026-06-19"


def test_appends_between_calls_are_visible(tmp_path: Path) -> None:
    client, journal = _client(tmp_path)
    _append(journal, [_prediction("p1"), _outcome("p1")])
    first = client.get("/api/v1/performance").json()
    assert first["headline"]["resolved_trades"] == 1
    _append(journal, [_prediction("p2"), _outcome("p2", ts_utc="2026-06-18T09:00:00+00:00")])
    second = client.get("/api/v1/performance").json()
    assert second["headline"]["resolved_trades"] == 2


def test_torn_tail_line_is_counted_not_fatal(tmp_path: Path) -> None:
    client, journal = _client(tmp_path)
    _append(journal, [_prediction("p1"), _outcome("p1"), '{"type":"outcome","predi'])
    response = client.get("/api/v1/performance")
    assert response.status_code == 200
    payload = response.json()
    assert payload["headline"]["resolved_trades"] == 1
    assert payload["anomalies"]["malformed_lines"] == 1


def test_executions_summary_rides_the_report(tmp_path: Path) -> None:
    # EXEC P3d: the executions dir sits beside the journal dir; close rows are
    # filtered like the journal and summed in both columns.
    client, journal = _client(tmp_path)
    _append(journal, [_prediction("p1"), _outcome("p1")])
    executions = journal.parent / "executions"
    executions.mkdir(parents=True)
    rows = [
        {
            "type": "open",
            "mode": "live",
            "bundle_id": BUNDLE,
            "ts_utc": "2026-06-18T08:05:00+00:00",
            "prediction_id": "p1",
        },
        {
            "type": "close",
            "mode": "live",
            "bundle_id": BUNDLE,
            "ts_utc": "2026-06-18T08:10:00+00:00",
            "prediction_id": "p1",
            "reason": "tp_hit",
            "session": "ny",
            "points": 15.0,
            "points_conservative": 14.75,
            "dollars": 300.0,
            "dollars_conservative": 295.0,
        },
        {
            "type": "close",
            "mode": "replay",
            "bundle_id": BUNDLE,
            "ts_utc": "2026-06-18T09:10:00+00:00",
            "prediction_id": "p2",
            "reason": "sl_hit",
            "session": "ny",
            "points": -30.0,
            "points_conservative": -30.5,
            "dollars": -600.0,
            "dollars_conservative": -610.0,
        },
        "not json {",
    ]
    with (executions / "2026-06-18.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write((row if isinstance(row, str) else json.dumps(row)) + "\n")

    payload = client.get("/api/v1/performance").json()
    executions_section = payload["executions"]
    assert executions_section["files_scanned"] == 1
    assert executions_section["malformed_lines"] == 1
    assert executions_section["opens_total"] == 1
    assert executions_section["closes_total"] == 2
    realized = executions_section["realized"]
    assert realized["count"] == 2
    assert realized["points"] == -15.0
    assert realized["points_conservative"] == -15.75
    assert realized["wins"] == 1 and realized["losses"] == 1
    assert realized["by_reason"]["tp_hit"]["count"] == 1

    live_only = client.get("/api/v1/performance", params={"mode": "live"}).json()
    live_realized = live_only["executions"]["realized"]
    assert live_realized["count"] == 1
    assert live_realized["points"] == 15.0
    assert live_only["executions"]["closes_outside_filters"] == 1


def test_400_on_bad_filters(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    assert client.get("/api/v1/performance", params={"mode": "bogus"}).status_code == 400
    assert client.get("/api/v1/performance", params={"from": "not-a-date"}).status_code == 400
    assert client.get("/api/v1/performance", params={"eligibility": "maybe"}).status_code == 400
    # EXEC P0a: a session outside the plugin vocabulary can never match a row,
    # so it is a 400 like the other filters — not an all-zeros 200.
    assert client.get("/api/v1/performance", params={"session": "nyse"}).status_code == 400
    assert (
        client.get(
            "/api/v1/performance", params={"from": "2026-06-19", "to": "2026-06-18"}
        ).status_code
        == 400
    )


def test_bundle_id_hygiene(tmp_path: Path) -> None:
    client, journal = _client(tmp_path)
    # Path-like ids are a 400 before any filesystem access.
    assert (
        client.get("/api/v1/performance", params={"bundle": "..\\escape"}).status_code == 400
    )
    assert client.get("/api/v1/performance", params={"bundle": "a/b"}).status_code == 400
    # EXEC P0b: a well-formed id absent from the model store applies the
    # bundle_id row filter with NO bundle dir — retired bundles' journal
    # history stays queryable; the OOS panel is simply absent.
    _append(journal, [_prediction("p1", bundle_id="NO_SUCH"), _outcome("p1", bundle_id="NO_SUCH")])
    response = client.get("/api/v1/performance", params={"bundle": "NO_SUCH"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["applied_filters"]["bundle_id"] == "NO_SUCH"
    assert payload["headline"]["resolved_trades"] == 1
    assert payload["oos_comparison"] is None
    # The row filter still scopes: the retired-bundle rows are invisible under
    # a DIFFERENT bundle id (the on-disk one).
    other = client.get("/api/v1/performance", params={"bundle": BUNDLE}).json()
    assert other["headline"]["resolved_trades"] == 0
