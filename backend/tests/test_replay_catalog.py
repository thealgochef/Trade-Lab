import json
import time
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi.testclient import TestClient

from trade_lab.adapters import replay_catalog
from trade_lab.api.app import create_app
from trade_lab.api.dto import warning_to_dto
from trade_lab.config import Settings
from trade_lab.services.broadcaster import WebSocketBroadcaster
from trade_lab.services.replay import HistoricalReplayService
from trade_lab.services.runtime import ApplicationRuntime


def _write_table(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def _safe_text(payload: object) -> str:
    return json.dumps(payload, default=str).lower()


def _assert_no_temp_path(payload: object, tmp_path: Path) -> None:
    text = _safe_text(payload)
    assert str(tmp_path).lower().replace("\\", "/") not in text.replace("\\", "/")
    assert "c:\\users" not in text
    assert "/users/" not in text


def _assert_generic_historical_warning_source(source: str | None) -> None:
    assert source == "historical-parquet"
    source_text = source.lower()
    for fragment in (
        "historical:nq",
        "2026-02-22",
        "trade",
        "trades",
        "mbp",
        "schema",
    ):
        assert fragment not in source_text


def test_replay_catalog_treats_symlinks_as_non_traversable() -> None:
    class FakeEntry:
        def is_symlink(self) -> bool:
            return True

        def stat(self, *, follow_symlinks: bool = True):  # pragma: no cover - must not be called
            raise AssertionError("symlink stat should not be inspected")

    assert replay_catalog._is_non_traversable_reparse_entry(FakeEntry()) is True


def test_replay_catalog_treats_windows_reparse_points_as_non_traversable(monkeypatch) -> None:
    reparse_attribute = 0x400
    monkeypatch.setattr(
        replay_catalog.stat, "FILE_ATTRIBUTE_REPARSE_POINT", reparse_attribute, raising=False
    )

    class FakeEntry:
        def is_symlink(self) -> bool:
            return False

        def stat(self, *, follow_symlinks: bool = True):
            assert follow_symlinks is False
            return SimpleNamespace(st_file_attributes=reparse_attribute)

    assert replay_catalog._is_non_traversable_reparse_entry(FakeEntry()) is True


def test_replay_catalog_ignores_non_traversable_configured_root(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[Path] = []

    def fake_is_non_traversable(entry: Path) -> bool:
        calls.append(entry)
        return entry == tmp_path.absolute()

    def fail_iter_candidate_files(root: Path, stats):  # pragma: no cover - must not be called
        raise AssertionError(f"non-traversable root should not be scanned: {root}")

    monkeypatch.setattr(
        replay_catalog, "_is_non_traversable_reparse_entry", fake_is_non_traversable
    )
    monkeypatch.setattr(replay_catalog, "_iter_candidate_files", fail_iter_candidate_files)

    catalog = replay_catalog.build_replay_catalog(
        data_path=tmp_path,
        requested_symbol="NQ.c.0",
        instrument_root="NQ",
    )

    assert catalog.historical_available is False
    assert catalog.historical_status == "configured data path is unavailable"
    assert list(catalog.sources) == ["synthetic:nq-demo"]
    assert calls == [tmp_path.absolute()]


def test_replay_catalog_reparse_check_is_noop_when_attribute_absent(monkeypatch) -> None:
    monkeypatch.setattr(replay_catalog.stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0, raising=False)

    class FakeEntry:
        def is_symlink(self) -> bool:
            return False

        def stat(self, *, follow_symlinks: bool = True):
            assert follow_symlinks is False
            return SimpleNamespace()

    assert replay_catalog._is_non_traversable_reparse_entry(FakeEntry()) is False


def test_discovery_returns_only_synthetic_when_data_path_missing_or_unconfigured(
    tmp_path: Path,
) -> None:
    missing = create_app(Settings(_env_file=None, data_path=tmp_path / "missing"))
    unconfigured = create_app(Settings(_env_file=None, data_path=None))

    missing_payload = TestClient(missing).get("/api/v1/replay/sources").json()
    unconfigured_payload = TestClient(unconfigured).get("/api/v1/replay/sources").json()

    assert [source["source_id"] for source in missing_payload["sources"]] == ["synthetic:nq-demo"]
    assert [source["source_id"] for source in unconfigured_payload["sources"]] == [
        "synthetic:nq-demo"
    ]
    assert missing_payload["historical"]["available"] is False
    assert unconfigured_payload["historical"]["available"] is False
    assert missing_payload["historical"]["diagnostics"]["data_path_configured"] is True
    assert missing_payload["historical"]["diagnostics"]["root_available"] is True
    assert missing_payload["historical"]["diagnostics"]["root_exists"] is False
    assert unconfigured_payload["historical"]["diagnostics"]["data_path_configured"] is False
    _assert_no_temp_path(missing_payload, tmp_path)


def test_discovery_finds_supported_fixtures_with_opaque_ids_and_ignores_unsupported_files(
    tmp_path: Path,
) -> None:
    fixture_file_names = [
        "secret_fixture_2026-02-22.trades.parquet",
        "secret_fixture_2026-02-22.mbp-1.parquet",
        "secret_fixture_2026-02-22.bbo.parquet",
        "secret_fixture_2026-02-22.mbp-10.parquet",
        "secret_fixture_2026-02-22.depth-only.mbp-1.parquet",
    ]
    _write_table(
        tmp_path / "NQ" / fixture_file_names[0],
        [{"ts_event": 1_772_064_000_000_000_000, "price": "17000.00", "size": 1}],
    )
    _write_table(
        tmp_path / "NQ" / fixture_file_names[1],
        [
            {
                "ts_event": 1_772_064_000_000_000_000,
                "bid_price": "17000.00",
                "bid_size": 3,
                "ask_price": "17000.25",
                "ask_size": 4,
            }
        ],
    )
    _write_table(
        tmp_path / "NQ" / fixture_file_names[2],
        [
            {
                "ts_event": 1_772_064_000_000_000_000,
                "bid_px": "17000.00",
                "ask_px": "17000.25",
            }
        ],
    )
    _write_table(
        tmp_path / "NQ" / fixture_file_names[3],
        [
            {
                "ts_event": 1_772_064_000_000_000_000,
                "action": "T",
                "price": "17000.00",
                "size": 1,
                "bid_px_00": "16999.75",
                "ask_px_00": "17000.25",
            }
        ],
    )
    _write_table(
        tmp_path / "NQ" / fixture_file_names[4],
        [{"ts_event": 1_772_064_000_000_000_000, "bid_px_01": "17000.00"}],
    )

    payload = TestClient(
        create_app(Settings(_env_file=None, data_path=tmp_path, front_month_symbol="NQ.c.0"))
    ).get("/api/v1/replay/sources").json()

    historical = [source for source in payload["sources"] if source["kind"] == "historical"]
    assert [(source["source_id"], source["schema"]) for source in historical] == [
        ("historical:nq:2026-02-22:bbo", "bbo"),
        ("historical:nq:2026-02-22:mbp-1", "mbp-1"),
        ("historical:nq:2026-02-22:mbp-10", "mbp-10"),
        ("historical:nq:2026-02-22:trades", "trades"),
    ]
    assert all(source["session_label"] == "2026-02-22" for source in historical)
    assert all(source["availability"] == "metadata_only" for source in historical)
    assert [source["label"] for source in historical] == [
        "Historical NQ 2026-02-22 bbo",
        "Historical NQ 2026-02-22 mbp-1",
        "Historical NQ 2026-02-22 mbp-10",
        "Historical NQ 2026-02-22 trades",
    ]
    assert all(
        "/" not in source["source_id"] and "\\" not in source["source_id"]
        for source in historical
    )
    for file_name in fixture_file_names:
        assert file_name.lower() not in _safe_text(payload)
    assert "depth-only" not in _safe_text(payload)
    assert payload["historical"]["diagnostics"]["parquet_files_inspected"] == 5
    assert payload["historical"]["diagnostics"]["skipped_unsupported_names"] == 1
    assert payload["historical"]["diagnostics"]["discovered"] == 4
    _assert_no_temp_path(payload, tmp_path)


def test_unsupported_deeper_book_file_name_containing_trades_is_not_advertised(
    tmp_path: Path,
) -> None:
    _write_table(
        tmp_path / "NQ" / "secret_fixture_2026-02-22.mbp-2.trades.parquet",
        [{"ts_event": 1_772_064_000_000_000_000, "price": "17000.00", "size": 1}],
    )

    payload = TestClient(create_app(Settings(_env_file=None, data_path=tmp_path))).get(
        "/api/v1/replay/sources"
    ).json()

    assert [source["source_id"] for source in payload["sources"]] == ["synthetic:nq-demo"]
    assert "mbp-2" not in _safe_text(payload)
    assert "secret_fixture" not in _safe_text(payload)
    _assert_no_temp_path(payload, tmp_path)


def test_mbp10_filename_variants_are_discoverable_without_paths(tmp_path: Path) -> None:
    for name in (
        "2026-02-22.mbp10.parquet",
        "2026-02-23.mbp_10.parquet",
        "2026-02-24.cmbp-10.parquet",
    ):
        _write_table(
            tmp_path / "NQ" / name,
            [
                {
                    "ts_event": 1_772_064_000_000_000_000,
                    "action": b"T",
                    "price": "17000.00",
                    "size": 1,
                }
            ],
        )
    _write_table(
        tmp_path / "NQ" / "2026-02-25.mbp-9.parquet",
        [{"ts_event": 1_772_064_000_000_000_000, "price": "17000.00", "size": 1}],
    )

    payload = TestClient(create_app(Settings(_env_file=None, data_path=tmp_path))).get(
        "/api/v1/replay/sources"
    ).json()

    historical = [source for source in payload["sources"] if source["kind"] == "historical"]
    assert [(source["source_id"], source["schema"]) for source in historical] == [
        ("historical:nq:2026-02-22:mbp-10", "mbp-10"),
        ("historical:nq:2026-02-23:mbp-10", "mbp-10"),
        ("historical:nq:2026-02-24:mbp-10", "mbp-10"),
    ]
    assert "mbp-9" not in _safe_text(payload)
    _assert_no_temp_path(payload, tmp_path)


def test_mbp10_schema_token_in_parent_directories_is_discoverable_without_path_leakage(
    tmp_path: Path,
) -> None:
    for path in (
        tmp_path / "NQ" / "mbp-10" / "2026-02-22.parquet",
        tmp_path / "NQ" / "2026-02-23" / "mbp10.parquet",
        tmp_path / "NQ" / "mbp10.parquet" / "part-000.parquet",
        tmp_path / "NQ" / "2026-02-24" / "mbp10" / "part-000.parquet",
    ):
        _write_table(
            path,
            [
                {
                    "ts_event": 1_772_064_000_000_000_000,
                    "action": "T",
                    "price": "17000.00",
                    "size": 1,
                }
            ],
        )

    payload = TestClient(create_app(Settings(_env_file=None, data_path=tmp_path))).get(
        "/api/v1/replay/sources"
    ).json()

    historical = [source for source in payload["sources"] if source["kind"] == "historical"]
    assert [(source["source_id"], source["schema"]) for source in historical] == [
        ("historical:nq:2026-02-23:mbp-10", "mbp-10"),
        ("historical:nq:2026-02-22:mbp-10", "mbp-10"),
        ("historical:nq:undated:mbp-10", "mbp-10"),
        ("historical:nq:2026-02-24:mbp-10", "mbp-10"),
    ]
    text = _safe_text(payload)
    assert "part-000" not in text
    assert "mbp10.parquet" not in text
    assert "mbp-10\\" not in text and "mbp-10/" not in text
    assert payload["historical"]["diagnostics"]["parquet_files_inspected"] == 4
    assert payload["historical"]["diagnostics"]["discovered"] == 4
    _assert_no_temp_path(payload, tmp_path)


def test_partitioned_historical_dataset_combines_parts_for_one_source(
    tmp_path: Path,
) -> None:
    first_part = tmp_path / "NQ" / "2026-02-24" / "mbp10" / "part-000.parquet"
    second_part = tmp_path / "NQ" / "2026-02-24" / "mbp10" / "part-001.parquet"
    _write_table(
        first_part,
        [{"ts_event": 1_772_064_000_000_000_000, "action": "T", "price": "17000.00", "size": 1}],
    )
    _write_table(
        second_part,
        [{"ts_event": 1_772_064_001_000_000_000, "action": "T", "price": "17000.25", "size": 1}],
    )

    catalog = replay_catalog.build_replay_catalog(
        data_path=tmp_path,
        requested_symbol="NQ.c.0",
        instrument_root="NQ",
    )

    definition, _source = catalog.sources["historical:nq:2026-02-24:mbp-10"]
    assert definition.paths == tuple(sorted((first_part.resolve(), second_part.resolve())))
    assert catalog.historical_diagnostics["discovered"] == 1
    assert catalog.historical_diagnostics["duplicates"] == 1


def test_partitioned_historical_source_start_replays_all_parts(tmp_path: Path) -> None:
    for index, price in enumerate(("17000.00", "17000.25")):
        _write_table(
            tmp_path / "NQ" / "2026-02-24" / "mbp10" / f"part-{index:03d}.parquet",
            [
                {
                    "ts_event": 1_772_064_000_000_000_000 + index * 1_000_000_000,
                    "action": "T",
                    "price": price,
                    "size": 1,
                }
            ],
        )
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0", tick_timeframes=(2,), observation_duration_seconds=300
    )
    replay = HistoricalReplayService(runtime)
    app = create_app(
        Settings(_env_file=None, data_path=tmp_path, front_month_symbol="NQ.c.0"),
        runtime=runtime,
        replay=replay,
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/replay/start", json={"source_id": "historical:nq:2026-02-24:mbp-10"}
    )
    for _ in range(500):
        if replay.status().state.value == "completed":
            break
        time.sleep(0.001)

    assert response.status_code == 200
    assert replay.status().events_processed == 2
    assert response.json()["source_id"] == "historical:nq:2026-02-24:mbp-10"
    _assert_no_temp_path(response.json(), tmp_path)


def test_date_label_uses_dataset_relative_path_not_absolute_parent_date(tmp_path: Path) -> None:
    data_root = tmp_path / "2026-01-01-parent" / "configured-root"
    _write_table(
        data_root / "NQ" / "2026-02-24" / "mbp10" / "part-000.parquet",
        [{"ts_event": 1_772_064_000_000_000_000, "action": "T", "price": "17000.00", "size": 1}],
    )

    catalog = replay_catalog.build_replay_catalog(
        data_path=data_root,
        requested_symbol="NQ.c.0",
        instrument_root="NQ",
    )

    assert "historical:nq:2026-02-24:mbp-10" in catalog.sources
    assert "historical:nq:2026-01-01:mbp-10" not in catalog.sources
    definition, _source = catalog.sources["historical:nq:2026-02-24:mbp-10"]
    assert definition.session_label == "2026-02-24"
    assert definition.label == "Historical NQ 2026-02-24 mbp-10"


def test_discovery_uses_canonical_root_for_containment(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "NQ"
    _write_table(
        data_root / "2026-02-24.trades.parquet",
        [{"ts_event": 1_772_064_000_000_000_000, "price": "17000.00", "size": 1}],
    )
    noncanonical_root = data_root.parent / ".." / "data" / "NQ"

    catalog = replay_catalog.build_replay_catalog(
        data_path=noncanonical_root,
        requested_symbol="NQ.c.0",
        instrument_root="NQ",
    )

    assert "historical:nq:2026-02-24:trades" in catalog.sources
    assert catalog.historical_diagnostics["outside_root_or_unresolvable"] == 0
    assert catalog.historical_diagnostics["discovered"] == 1


def test_containment_failures_increment_safe_diagnostic(monkeypatch, tmp_path: Path) -> None:
    outside = tmp_path / "outside" / "2026-02-24.trades.parquet"
    _write_table(
        outside,
        [{"ts_event": 1_772_064_000_000_000_000, "price": "17000.00", "size": 1}],
    )
    root = tmp_path / "root"
    root.mkdir()

    def fake_iter_candidate_files(root_path: Path, stats):
        stats.parquet_candidates_seen += 1
        yield outside

    monkeypatch.setattr(replay_catalog, "_iter_candidate_files", fake_iter_candidate_files)

    catalog = replay_catalog.build_replay_catalog(
        data_path=root,
        requested_symbol="NQ.c.0",
        instrument_root="NQ",
    )

    assert catalog.historical_available is False
    assert catalog.historical_diagnostics["parquet_candidates_seen"] == 1
    assert catalog.historical_diagnostics["parquet_files_inspected"] == 1
    assert catalog.historical_diagnostics["outside_root_or_unresolvable"] == 1
    assert catalog.historical_diagnostics["discovered"] == 0
    _assert_no_temp_path(catalog.historical_diagnostics, tmp_path)


def test_unsupported_parquet_noise_before_valid_file_does_not_starve_discovery(
    tmp_path: Path,
) -> None:
    for index in range(300):
        _write_table(
            tmp_path / "NQ" / f"aaa_noise_{index:03d}.vendor-private.parquet",
            [{"ts_event": 1_772_064_000_000_000_000, "price": "17000.00", "size": 1}],
        )
    _write_table(
        tmp_path / "NQ" / "zzz_2026-02-24.trades.parquet",
        [{"ts_event": 1_772_064_000_000_000_000, "price": "17000.00", "size": 1}],
    )

    payload = TestClient(create_app(Settings(_env_file=None, data_path=tmp_path))).get(
        "/api/v1/replay/sources"
    ).json()

    assert "historical:nq:2026-02-24:trades" in [
        source["source_id"] for source in payload["sources"]
    ]
    diagnostics = payload["historical"]["diagnostics"]
    assert diagnostics["parquet_candidates_seen"] == 301
    assert diagnostics["parquet_files_inspected"] == 301
    assert diagnostics["skipped_unsupported_names"] == 300
    assert diagnostics["metadata_reads_attempted"] == 1
    assert diagnostics["discovered"] == 1
    assert diagnostics["traversal_truncated"] is False
    assert "aaa_noise" not in _safe_text(payload)
    assert "zzz_2026" not in _safe_text(payload)
    _assert_no_temp_path(payload, tmp_path)


def test_safe_diagnostics_counts_metadata_failures_missing_columns_and_duplicates(
    tmp_path: Path,
) -> None:
    _write_table(
        tmp_path / "NQ" / "2026-02-22.trades.parquet",
        [{"ts_event": 1_772_064_000_000_000_000, "price": "17000.00", "size": 1}],
    )
    _write_table(
        tmp_path / "NQ" / "duplicate_2026-02-22.trades.parquet",
        [{"ts_event": 1_772_064_000_000_000_000, "price": "17000.00", "size": 1}],
    )
    _write_table(
        tmp_path / "NQ" / "2026-02-23.trades.parquet",
        [{"ts_event": 1_772_064_000_000_000_000, "price": "17000.00"}],
    )
    unreadable = tmp_path / "NQ" / "2026-02-24.trades.parquet"
    unreadable.parent.mkdir(parents=True, exist_ok=True)
    unreadable.write_bytes(b"not parquet")
    _write_table(
        tmp_path / "NQ" / "2026-02-25.vendor-private.parquet",
        [{"ts_event": 1_772_064_000_000_000_000, "price": "17000.00", "size": 1}],
    )

    payload = TestClient(create_app(Settings(_env_file=None, data_path=tmp_path))).get(
        "/api/v1/replay/sources"
    ).json()

    diagnostics = payload["historical"]["diagnostics"]
    assert diagnostics["parquet_files_inspected"] == 5
    assert diagnostics["skipped_unsupported_names"] == 1
    assert diagnostics["unreadable_metadata"] == 1
    assert diagnostics["unsupported_schema_or_required_columns"] == 1
    assert diagnostics["duplicates"] == 1
    assert diagnostics["discovered"] == 1
    assert "duplicate_" not in _safe_text(payload)
    assert "vendor-private" not in _safe_text(payload)
    _assert_no_temp_path(payload, tmp_path)


def test_catalog_hides_unsupported_book_depth_and_depth_only_variants(
    tmp_path: Path,
) -> None:
    unsupported_names = [
        "2026-02-22.mbo.parquet",
        "2026-02-22.mbp-2.parquet",
        "2026-02-22.mbp_3.parquet",
        "2026-02-22.cmbp-4.parquet",
        "2026-02-22.mbp5.parquet",
        "2026-02-22.mbp-9.trades.parquet",
        "2026-02-22.depth-only.mbp-1.parquet",
        "2026-02-22.depth_only.mbp-10.parquet",
    ]
    for name in unsupported_names:
        _write_table(
            tmp_path / "data" / "databento" / "NQ" / name,
            [
                {
                    "ts_event": 1_772_064_000_000_000_000,
                    "action": "T",
                    "price": "17000.00",
                    "size": 1,
                    "bid_px_00": "16999.75",
                    "ask_px_00": "17000.25",
                }
            ],
        )

    payload = TestClient(create_app(Settings(_env_file=None, data_path=tmp_path))).get(
        "/api/v1/replay/sources"
    ).json()

    assert [source["source_id"] for source in payload["sources"]] == ["synthetic:nq-demo"]
    text = _safe_text(payload)
    assert "2026-02-22.mbo" not in text
    assert "2026-02-22.mbp-2" not in text
    assert "2026-02-22.mbp_3" not in text
    assert "2026-02-22.cmbp-4" not in text
    assert "2026-02-22.mbp5" not in text
    assert "2026-02-22.mbp-9" not in text
    assert "depth-only" not in text
    assert "depth_only" not in text
    assert "databento" not in text
    _assert_no_temp_path(payload, tmp_path)


def test_historical_mbp10_ids_and_labels_do_not_expose_databento_path_details(
    tmp_path: Path,
) -> None:
    _write_table(
        tmp_path
        / "data"
        / "databento"
        / "NQ"
        / "private_vendor_export_2026-02-22.mbp-10.parquet",
        [
            {
                "ts_event": 1_772_064_000_000_000_000,
                "action": b"T",
                "price": "17000.00",
                "size": 1,
                "bid_px_00": "16999.75",
                "bid_sz_00": 3,
                "ask_px_00": "17000.25",
                "ask_sz_00": 4,
            }
        ],
    )

    payload = TestClient(create_app(Settings(_env_file=None, data_path=tmp_path))).get(
        "/api/v1/replay/sources"
    ).json()

    historical = [source for source in payload["sources"] if source["kind"] == "historical"]
    assert historical == [
        {
            "source_id": "historical:nq:2026-02-22:mbp-10",
            "label": "Historical NQ 2026-02-22 mbp-10",
            "requested_symbol": "NQ.c.0",
            "schema": "mbp-10",
            "kind": "historical",
            "session_label": "2026-02-22",
            "availability": "metadata_only",
        }
    ]
    text = _safe_text(payload)
    assert "private_vendor_export" not in text
    assert "databento" not in text
    assert "data\\databento\\nq" not in text
    assert "data/databento/nq" not in text
    assert "nq\\" not in text and "nq/" not in text
    _assert_no_temp_path(payload, tmp_path)


def test_discovery_uses_bounded_metadata_only_scan(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []

    class FakeParquetFile:
        def __init__(self, path: Path) -> None:
            calls.append(path.name)
            assert "unsupported" not in path.name
            self.schema_arrow = type(
                "Schema",
                (),
                {"names": ["ts_event", "price", "size", "huge_payload_column"]},
            )()

    for index in range(300):
        suffix = "trades" if index % 2 == 0 else "unsupported"
        path = tmp_path / "NQ" / f"2026-02-{index % 28 + 1:02d}.{index:03d}.{suffix}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not read by discovery")
    monkeypatch.setattr(pq, "ParquetFile", FakeParquetFile)

    payload = TestClient(create_app(Settings(_env_file=None, data_path=tmp_path))).get(
        "/api/v1/replay/sources"
    ).json()

    assert 0 < len(calls) <= replay_catalog._MAX_METADATA_READS
    historical_count = len(
        [source for source in payload["sources"] if source["kind"] == "historical"]
    )
    assert historical_count <= len(calls)
    diagnostics = payload["historical"]["diagnostics"]
    assert diagnostics["parquet_candidates_seen"] == 300
    assert diagnostics["metadata_reads_attempted"] == len(calls)
    assert "huge_payload_column" not in _safe_text(payload)
    _assert_no_temp_path(payload, tmp_path)


def test_directory_traversal_stops_within_configured_bounds(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(replay_catalog, "_MAX_DIRECTORIES_VISITED", 5)
    monkeypatch.setattr(replay_catalog, "_MAX_DIRECTORY_DEPTH", 2)
    for index in range(30):
        directory = tmp_path / f"non_parquet_dir_{index:02d}" / "nested"
        directory.mkdir(parents=True)
        for file_index in range(5):
            (directory / f"noise_{file_index}.txt").write_text("noise", encoding="utf-8")

    payload = TestClient(create_app(Settings(_env_file=None, data_path=tmp_path))).get(
        "/api/v1/replay/sources"
    ).json()

    assert [source["source_id"] for source in payload["sources"]] == ["synthetic:nq-demo"]
    assert payload["historical"]["available"] is False
    assert "truncated by safety limits" in payload["historical"]["status"]
    assert "non_parquet_dir" not in _safe_text(payload)
    _assert_no_temp_path(payload, tmp_path)


def test_large_flat_non_parquet_directory_inspection_is_capped(
    monkeypatch, tmp_path: Path
) -> None:
    max_inspected = 7
    yielded_names: list[str] = []
    original_iterdir = Path.iterdir

    for index in range(50):
        (tmp_path / f"secret_flat_noise_{index:03d}.txt").write_text(
            "noise", encoding="utf-8"
        )

    def counting_iterdir(path: Path):
        for entry in original_iterdir(path):
            if path == tmp_path:
                yielded_names.append(entry.name)
            yield entry

    monkeypatch.setattr(
        replay_catalog, "_MAX_DIRECTORY_ENTRIES_INSPECTED", max_inspected
    )
    monkeypatch.setattr(Path, "iterdir", counting_iterdir)

    payload = TestClient(create_app(Settings(_env_file=None, data_path=tmp_path))).get(
        "/api/v1/replay/sources"
    ).json()

    assert len(yielded_names) == max_inspected
    assert [source["source_id"] for source in payload["sources"]] == ["synthetic:nq-demo"]
    assert payload["historical"]["available"] is False
    assert "truncated by safety limits" in payload["historical"]["status"]
    assert "secret_flat_noise" not in _safe_text(payload)
    _assert_no_temp_path(payload, tmp_path)


def test_start_with_discovered_historical_source_replays_runtime_and_bars(tmp_path: Path) -> None:
    _write_table(
        tmp_path / "NQ" / "2026-02-22.trades.parquet",
        [
            {"ts_event": 1_772_064_000_000_000_000, "price": "17000.00", "size": 1},
            {"ts_event": 1_772_064_001_000_000_000, "price": "17000.25", "size": 1},
        ],
    )
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0", tick_timeframes=(2,), observation_duration_seconds=300
    )
    replay = HistoricalReplayService(runtime)
    broadcaster = WebSocketBroadcaster(runtime)
    app = create_app(
        Settings(_env_file=None, data_path=tmp_path, front_month_symbol="NQ.c.0"),
        runtime=runtime,
        replay=replay,
        broadcaster=broadcaster,
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/replay/start", json={"source_id": "historical:nq:2026-02-22:trades"}
    )
    for _ in range(500):
        if replay.status().state.value == "completed":
            break
        time.sleep(0.001)

    assert response.status_code == 200
    assert replay.status().events_processed == 2
    assert len(runtime.snapshot().recent_closed_bars) == 1
    assert response.json()["source_id"] == "historical:nq:2026-02-22:trades"
    _assert_no_temp_path(response.json(), tmp_path)


def test_start_with_discovered_mbp10_source_replays_trade_actions_only(tmp_path: Path) -> None:
    _write_table(
        tmp_path / "NQ" / "2026-02-22.mbp-10.parquet",
        [
            {
                "ts_event": 1_772_064_000_000_000_000,
                "action": "T",
                "price": "17000.00",
                "size": 1,
                "bid_px_00": "16999.75",
                "ask_px_00": "17000.25",
            },
            {
                "ts_event": 1_772_064_001_000_000_000,
                "action": "A",
                "price": "17000.25",
                "size": 99,
                "bid_px_00": "17000.00",
                "ask_px_00": "17000.50",
            },
            {
                "ts_event": 1_772_064_002_000_000_000,
                "action": b"Trade",
                "price": "17000.25",
                "size": 1,
            },
        ],
    )
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0", tick_timeframes=(2,), observation_duration_seconds=300
    )
    replay = HistoricalReplayService(runtime)
    app = create_app(
        Settings(_env_file=None, data_path=tmp_path, front_month_symbol="NQ.c.0"),
        runtime=runtime,
        replay=replay,
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/replay/start", json={"source_id": "historical:nq:2026-02-22:mbp-10"}
    )
    for _ in range(500):
        if replay.status().state.value == "completed":
            break
        time.sleep(0.001)

    assert response.status_code == 200
    assert replay.status().events_processed == 4
    assert len(runtime.snapshot().recent_closed_bars) == 1
    assert response.json()["source_id"] == "historical:nq:2026-02-22:mbp-10"
    _assert_no_temp_path(response.json(), tmp_path)


def test_unknown_stale_and_path_like_historical_ids_are_rejected(tmp_path: Path) -> None:
    _write_table(
        tmp_path / "NQ" / "2026-02-22.trades.parquet",
        [{"ts_event": 1_772_064_000_000_000_000, "price": "17000.00", "size": 1}],
    )
    client = TestClient(create_app(Settings(_env_file=None, data_path=tmp_path)))

    unknown = client.post(
        "/api/v1/replay/start", json={"source_id": "historical:nq:2026-02-23:trades"}
    )
    stale = client.post(
        "/api/v1/replay/start", json={"source_id": "historical:nq:2026-02-22:bbo"}
    )
    path_like_values = [
        "C:\\Users\\raw.parquet",
        "C:/Users/raw.parquet",
        "/tmp/raw.parquet",
        "historical:..\\raw",
        "historical:../raw",
    ]
    path_like = [
        client.post("/api/v1/replay/start", json={"source_id": value})
        for value in path_like_values
    ]

    assert unknown.status_code == 404
    assert stale.status_code == 404
    assert [response.status_code for response in path_like] == [400] * len(path_like)
    _assert_no_temp_path([unknown.json(), stale.json(), *[r.json() for r in path_like]], tmp_path)


def test_path_containment_ignores_symlink_escape_when_supported(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    root = tmp_path / "root"
    _write_table(
        outside / "2026-02-22.trades.parquet",
        [{"ts_event": 1_772_064_000_000_000_000, "price": "17000.00", "size": 1}],
    )
    root.mkdir()
    link = root / "2026-02-22.trades.parquet"
    try:
        link.symlink_to(outside / "2026-02-22.trades.parquet")
    except (OSError, NotImplementedError):
        return

    payload = TestClient(create_app(Settings(_env_file=None, data_path=root))).get(
        "/api/v1/replay/sources"
    ).json()

    assert [source["source_id"] for source in payload["sources"]] == ["synthetic:nq-demo"]
    _assert_no_temp_path(payload, tmp_path)


def test_invalid_size_warning_source_is_generic_for_catalog_discovered_replay(
    tmp_path: Path,
) -> None:
    _write_table(
        tmp_path / "NQ" / "2026-02-22.trades.parquet",
        [{"ts_event": 1_772_064_000_000_000_000, "price": "17000.00", "size": "bad-size"}],
    )
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0", tick_timeframes=(2,), observation_duration_seconds=300
    )
    replay = HistoricalReplayService(runtime)
    app = create_app(
        Settings(_env_file=None, data_path=tmp_path, front_month_symbol="NQ.c.0"),
        runtime=runtime,
        replay=replay,
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/replay/start", json={"source_id": "historical:nq:2026-02-22:trades"}
    )
    for _ in range(500):
        if replay.status().state.value == "completed":
            break
        time.sleep(0.001)

    assert response.status_code == 200
    assert replay.status().warnings_recorded == 1
    warnings = runtime.snapshot().warnings
    assert len(warnings) == 1
    warning_dto = warning_to_dto(warnings[0])
    _assert_generic_historical_warning_source(warning_dto.source)
    _assert_no_temp_path(warning_dto.model_dump(), tmp_path)
