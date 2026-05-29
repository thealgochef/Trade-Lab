from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from trade_lab.adapters.historical_parquet import HistoricalParquetAdapter, HistoricalParquetSource
from trade_lab.api.dto import warning_to_dto
from trade_lab.domain.data_quality import DataQualityCode, DataQualityWarning
from trade_lab.domain.events import TopOfBookEvent, TradeEvent
from trade_lab.services.runtime import ApplicationRuntime

SCHEMA_LIKE_SOURCE_FRAGMENTS = (
    "historical:nq",
    "2026-02-22",
    "trade",
    "trades",
    "mbp",
    "schema",
)


def _write_table(path: Path, rows: list[dict[str, object]]) -> None:
    pq.write_table(pa.Table.from_pylist(rows), path)


def _assert_generic_historical_warning_source(source: str | None) -> None:
    assert source == "historical-parquet"
    source_text = source.lower()
    for fragment in SCHEMA_LIKE_SOURCE_FRAGMENTS:
        assert fragment not in source_text


def test_synthetic_trades_parquet_normalizes_live_compatible_columns(tmp_path: Path) -> None:
    path = tmp_path / "trades.parquet"
    _write_table(
        path,
        [
            {
                "ts_event": 1_735_689_600_000_000_000,
                "price": "17000.25",
                "size": 2,
                "instrument_id": 123,
                "raw_symbol": "NQH6",
                "side": "buy",
            },
            {
                "ts_event": 1_735_689_600_100_000_000,
                "price": "17000.30",
                "size": 1,
                "instrument_id": 123,
            },
        ],
    )

    results = list(
        HistoricalParquetAdapter().scan([path], requested_symbol="NQ.c.0", schema="trades")
    )

    first_trade = next(item for item in results if isinstance(item, TradeEvent))
    assert first_trade.price_ticks == 68_001
    assert first_trade.requested_symbol == "NQ.c.0"
    assert any(
        isinstance(item, DataQualityWarning) and item.code == DataQualityCode.INVALID_PRICE
        for item in results
    )


def test_historical_parquet_source_alias_emits_canonical_events_runtime_accepts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "source_alias.parquet"
    _write_table(
        path,
        [
            {"ts_event": 1_735_689_600_000_000_000, "price": "17000.00", "size": 1},
            {"ts_event": 1_735_689_600_100_000_000, "price": "17000.25", "size": 1},
        ],
    )
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0", tick_timeframes=(2,), observation_duration_seconds=300
    )

    for item in HistoricalParquetSource().scan([path], requested_symbol="NQ.c.0", schema="trades"):
        assert isinstance(item, TradeEvent)
        runtime.process_market_event(item)

    snapshot = runtime.snapshot()
    assert len(snapshot.recent_closed_bars) == 1


def test_trade_scan_filters_by_utc_time_window(tmp_path: Path) -> None:
    path = tmp_path / "trades.parquet"
    _write_table(
        path,
        [
            {"ts_event": 1_735_689_599_000_000_000, "price": "17000.00", "size": 1},
            {"ts_event": 1_735_689_600_000_000_000, "price": "17000.25", "size": 1},
            {"ts_event": 1_735_689_601_000_000_000, "price": "17000.50", "size": 1},
        ],
    )

    results = list(
        HistoricalParquetAdapter().scan(
            [path],
            requested_symbol="NQ.c.0",
            schema="trade",
            start_ts_utc=datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC),
            end_ts_utc=datetime(2025, 1, 1, 0, 0, 1, tzinfo=UTC),
        )
    )

    assert [event.price_ticks for event in results if isinstance(event, TradeEvent)] == [
        68_001,
    ]


def test_trade_scan_merges_multiple_paths_by_event_timestamp(tmp_path: Path) -> None:
    later_path = tmp_path / "later.parquet"
    earlier_path = tmp_path / "earlier.parquet"
    _write_table(
        later_path,
        [{"ts_event": 1_735_689_601_000_000_000, "price": "17000.50", "size": 1}],
    )
    _write_table(
        earlier_path,
        [{"ts_event": 1_735_689_600_000_000_000, "price": "17000.00", "size": 1}],
    )

    results = list(
        HistoricalParquetAdapter().scan(
            [later_path, earlier_path], requested_symbol="NQ.c.0", schema="trades"
        )
    )

    assert [event.price_ticks for event in results if isinstance(event, TradeEvent)] == [
        68_000,
        68_002,
    ]


def test_trade_scan_sorts_events_within_bounded_parquet_batches(tmp_path: Path) -> None:
    path = tmp_path / "unsorted.parquet"
    _write_table(
        path,
        [
            {"ts_event": 1_735_689_602_000_000_000, "price": "17000.50", "size": 1},
            {"ts_event": 1_735_689_600_000_000_000, "price": "17000.00", "size": 1},
            {"ts_event": 1_735_689_601_000_000_000, "price": "17000.25", "size": 1},
        ],
    )

    results = list(
        HistoricalParquetAdapter(batch_size=10).scan(
            [path], requested_symbol="NQ.c.0", schema="trades"
        )
    )

    assert [event.price_ticks for event in results if isinstance(event, TradeEvent)] == [
        68_000,
        68_001,
        68_002,
    ]


def test_trade_scan_does_not_claim_global_sort_across_parquet_batches(tmp_path: Path) -> None:
    path = tmp_path / "cross_batch_regression.parquet"
    _write_table(
        path,
        [
            {"ts_event": 1_735_689_601_000_000_000, "price": "17000.25", "size": 1},
            {"ts_event": 1_735_689_600_000_000_000, "price": "17000.00", "size": 1},
        ],
    )

    results = list(
        HistoricalParquetAdapter(batch_size=1).scan(
            [path], requested_symbol="NQ.c.0", schema="trades"
        )
    )

    # The adapter only sorts within each bounded batch; replay rejects timestamp
    # regressions instead of forcing an unbounded per-file global sort here.
    assert [event.price_ticks for event in results if isinstance(event, TradeEvent)] == [
        68_001,
        68_000,
    ]


def test_batch_warnings_are_emitted_before_events_to_preserve_max_events_guard(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mixed_batch.parquet"
    _write_table(
        path,
        [
            {"ts_event": 1_735_689_600_000_000_000, "price": "17000.00", "size": 1},
            {"ts_event": 1_735_689_600_100_000_000, "price": "17000.10", "size": 1},
        ],
    )

    results = list(
        HistoricalParquetAdapter(batch_size=10).scan(
            [path], requested_symbol="NQ.c.0", schema="trades"
        )
    )

    assert [type(item) for item in results] == [DataQualityWarning, TradeEvent]
    assert results[0].code == DataQualityCode.INVALID_PRICE


def test_warning_heavy_stream_yields_first_warning_without_reading_next_batch(
    monkeypatch, tmp_path: Path
) -> None:
    yielded_batches = 0

    class FakeParquetFile:
        schema_arrow = SimpleNamespace(names=["ts_event", "price", "size"])

        def __init__(self, path: Path) -> None:
            self.path = path

        def iter_batches(self, *, columns: list[str], batch_size: int):
            nonlocal yielded_batches
            _ = (columns, batch_size)
            yielded_batches += 1
            yield pa.RecordBatch.from_pylist(
                [
                    {
                        "ts_event": 1_735_689_600_000_000_000,
                        "price": "17000.10",
                        "size": 1,
                    }
                ]
            )
            yielded_batches += 1
            yield pa.RecordBatch.from_pylist(
                [
                    {
                        "ts_event": 1_735_689_601_000_000_000,
                        "price": "17000.00",
                        "size": 1,
                    }
                ]
            )

    monkeypatch.setattr(pq, "ParquetFile", FakeParquetFile)

    iterator = HistoricalParquetAdapter(batch_size=1).scan(
        [tmp_path / "warning_heavy.parquet"], requested_symbol="NQ.c.0", schema="trades"
    )

    first = next(iterator)
    assert isinstance(first, DataQualityWarning)
    assert first.code == DataQualityCode.INVALID_PRICE
    assert yielded_batches == 1


def test_missing_required_columns_emit_warning_without_events(tmp_path: Path) -> None:
    path = tmp_path / "missing.parquet"
    _write_table(path, [{"ts_event": 1_735_689_600_000_000_000, "size": 1}])

    results = list(
        HistoricalParquetAdapter().scan([path], requested_symbol="NQ.c.0", schema="trades")
    )

    assert len(results) == 1
    assert isinstance(results[0], DataQualityWarning)
    assert results[0].code == DataQualityCode.MISSING_REQUIRED_COLUMN
    warning_text = (
        f"{results[0].message} {results[0].metadata} "
        f"{warning_to_dto(results[0]).model_dump()}"
    )
    assert "ts_event" not in warning_text
    assert "price" not in warning_text
    assert "size" not in warning_text


def test_missing_bbo_columns_emit_error_warning(tmp_path: Path) -> None:
    path = tmp_path / "missing_bbo.parquet"
    _write_table(path, [{"ts_event": 1_735_689_600_000_000_000, "bid_price": "17000.00"}])

    results = list(HistoricalParquetAdapter().scan([path], requested_symbol="NQ.c.0", schema="bbo"))

    assert len(results) == 1
    assert isinstance(results[0], DataQualityWarning)
    assert results[0].code == DataQualityCode.MISSING_REQUIRED_COLUMN
    assert results[0].severity.value == "error"


def test_mbp10_depth_columns_are_ignored_and_top_of_book_is_normalized(tmp_path: Path) -> None:
    path = tmp_path / "mbp.parquet"
    _write_table(
        path,
        [
            {
                "ts_event": 1_735_689_600_000_000_000,
                "bid_price": "17000.00",
                "bid_size": 5,
                "ask_price": "17000.25",
                "ask_size": 6,
                "bid_px_01": "16999.75",
            }
        ],
    )

    results = list(
        HistoricalParquetAdapter().scan([path], requested_symbol="NQ.c.0", schema="mbp-1")
    )

    assert isinstance(results[0], DataQualityWarning)
    assert results[0].code == DataQualityCode.HISTORICAL_ONLY_FIELD_IGNORED
    assert results[0].source == "historical-parquet"
    assert results[0].metadata == {"ignored_column_count": 1}
    assert isinstance(results[1], TopOfBookEvent)
    assert results[1].bid_price_ticks == 68_000
    assert results[1].ask_price_ticks == 68_001


def test_top_of_book_alias_columns_normalize_to_canonical_event(tmp_path: Path) -> None:
    path = tmp_path / "bbo.parquet"
    _write_table(
        path,
        [
            {
                "ts_event": 1_735_689_600_000_000_000,
                "instrument_id": 123,
                "bid_px": "17000.00",
                "bid_size": 5,
                "ask_px": "17000.25",
                "ask_size": 6,
            }
        ],
    )

    results = list(
        HistoricalParquetAdapter().scan([path], requested_symbol="NQ.c.0", schema="cbbo")
    )

    assert len(results) == 1
    assert isinstance(results[0], TopOfBookEvent)
    assert results[0].instrument_id == 123
    assert results[0].source_schema == "cbbo"


def test_invalid_non_tick_aligned_price_warns_and_skips_record(tmp_path: Path) -> None:
    path = tmp_path / "invalid_price.parquet"
    _write_table(path, [{"ts_event": 1_735_689_600_000_000_000, "price": "17000.10", "size": 1}])

    results = list(
        HistoricalParquetAdapter().scan([path], requested_symbol="NQ.c.0", schema="trades")
    )

    assert len(results) == 1
    assert isinstance(results[0], DataQualityWarning)
    assert results[0].code == DataQualityCode.INVALID_PRICE


@pytest.mark.parametrize(
    ("invalid_size", "raw_values"),
    [
        ("noninteger-volume-abc", ("noninteger-volume-abc",)),
        (-987_654_321, ("-987654321",)),
        (True, ("true",)),
        (0, ()),
    ],
)
def test_invalid_trade_sizes_emit_generic_warning_without_leaking_field_or_raw_values(
    tmp_path: Path, invalid_size: object, raw_values: tuple[str, ...]
) -> None:
    path = tmp_path / "invalid_size.parquet"
    _write_table(
        path,
        [{"ts_event": 1_735_689_600_000_000_000, "price": "17000.00", "size": invalid_size}],
    )

    results = list(
        HistoricalParquetAdapter().scan([path], requested_symbol="NQ.c.0", schema="trades")
    )

    assert len(results) == 1
    warning = results[0]
    assert isinstance(warning, DataQualityWarning)
    assert warning.code == DataQualityCode.INVALID_RECORD
    _assert_generic_historical_warning_source(warning_to_dto(warning).source)
    assert warning.message == "invalid historical parquet record"
    assert warning.metadata == {}
    assert not any(isinstance(item, TradeEvent) for item in results)
    warning_text = (
        f"{warning.message} {dict(warning.metadata)} "
        f"{warning_to_dto(warning).model_dump()}"
    ).lower()
    assert "invalid_record" in warning_text
    assert "unsupported_schema" not in warning_text
    assert "schema" not in warning_text
    assert "column" not in warning_text
    assert "field" not in warning_text
    assert "trade" not in warning_text
    assert "size" not in warning_text
    for raw_value in raw_values:
        assert raw_value not in warning_text


@pytest.mark.parametrize(
    ("schema", "row", "expected_field", "raw_values"),
    [
        (
            "trades",
            {"ts_event": "SECRET://C:/Users/admin/key", "price": "17000.00", "size": 1},
            "ts_event",
            ("SECRET://C:/Users/admin/key", "C:/Users/admin/key"),
        ),
        (
            "trades",
            {"ts_event": 1_735_689_600_000_000_000, "price": "SECRET:/tmp/model.bin", "size": 1},
            "price",
            ("SECRET:/tmp/model.bin", "/tmp/model.bin"),
        ),
        (
            "trades",
            {"ts_event": 123.456, "price": "17000.00", "size": 1},
            "ts_event",
            ("123.456",),
        ),
        (
            "trades",
            {"ts_event": 1_735_689_600_000_000_000, "price": "17000.10", "size": 1},
            "price",
            ("17000.10",),
        ),
        (
            "trades",
            {"ts_event": 1_735_689_600_000_000_000, "price": 17_000_100_000_000, "size": 1},
            "price",
            ("17000100000000",),
        ),
        (
            "bbo",
            {
                "ts_event": 1_735_689_600_000_000_000,
                "bid_price": "SECRET://hidden/bid",
                "ask_price": "17000.25",
            },
            "bid",
            ("SECRET://hidden/bid", "hidden/bid"),
        ),
        (
            "mbp-10",
            {
                "ts_event": 1_735_689_600_000_000_000,
                "action": "T",
                "price": "SECRET://hidden/trade-price",
                "size": 1,
            },
            "price",
            ("SECRET://hidden/trade-price", "hidden/trade-price"),
        ),
        (
            "mbp-10",
            {
                "ts_event": 1_735_689_600_000_000_000,
                "bid_px_00": "16999.75",
                "ask_px_00": "SECRET://hidden/ask",
            },
            "ask",
            ("SECRET://hidden/ask", "hidden/ask"),
        ),
    ],
)
def test_invalid_historical_values_are_redacted_from_warnings_and_dtos(
    tmp_path: Path,
    schema: str,
    row: dict[str, object],
    expected_field: str,
    raw_values: tuple[str, ...],
) -> None:
    path = tmp_path / f"invalid_{schema}.parquet"
    _write_table(path, [row])

    warnings = [
        item
        for item in HistoricalParquetAdapter().scan(
            [path], requested_symbol="NQ.c.0", schema=schema
        )
        if isinstance(item, DataQualityWarning)
    ]

    assert len(warnings) == 1
    warning = warnings[0]
    assert warning.code in {DataQualityCode.INVALID_PRICE, DataQualityCode.INVALID_TIMESTAMP}
    warning_text = (
        f"{warning.message} {dict(warning.metadata)} "
        f"{warning_to_dto(warning).model_dump()}"
    )
    if expected_field == "price":
        assert "field price" not in warning_text
    else:
        assert expected_field not in warning_text
    for raw_value in raw_values:
        assert raw_value not in warning_text


def test_invalid_timestamp_and_price_warning_dtos_do_not_expose_column_names(
    tmp_path: Path,
) -> None:
    timestamp_path = tmp_path / "invalid_timestamp.parquet"
    price_path = tmp_path / "invalid_price.parquet"
    _write_table(timestamp_path, [{"ts_event": "not-a-timestamp", "price": "17000.00", "size": 1}])
    _write_table(
        price_path,
        [{"ts_event": 1_735_689_600_000_000_000, "price": "17000.10", "size": 1}],
    )

    warnings = [
        *[
            item
            for item in HistoricalParquetAdapter().scan(
                [timestamp_path], requested_symbol="NQ.c.0", schema="trades"
            )
            if isinstance(item, DataQualityWarning)
        ],
        *[
            item
            for item in HistoricalParquetAdapter().scan(
                [price_path], requested_symbol="NQ.c.0", schema="trades"
            )
            if isinstance(item, DataQualityWarning)
        ],
    ]

    assert [warning.message for warning in warnings] == [
        "invalid historical parquet timestamp",
        "invalid historical parquet price",
    ]
    for warning in warnings:
        warning_text = (
            f"{warning.message} {warning.metadata} {warning_to_dto(warning).model_dump()}"
        )
        assert "ts_event" not in warning_text
        assert "field price" not in warning_text
        assert warning.metadata == {}


def test_naive_and_invalid_timestamps_warn_and_skip_records(tmp_path: Path) -> None:
    naive_path = tmp_path / "naive.parquet"
    invalid_path = tmp_path / "invalid.parquet"
    _write_table(
        naive_path, [{"ts_event": datetime(2025, 1, 1), "price": "17000.00", "size": 1}]
    )
    _write_table(invalid_path, [{"ts_event": "not-a-timestamp", "price": "17000.00", "size": 1}])

    naive_results = list(
        HistoricalParquetAdapter().scan([naive_path], requested_symbol="NQ.c.0", schema="trades")
    )
    invalid_results = list(
        HistoricalParquetAdapter().scan([invalid_path], requested_symbol="NQ.c.0", schema="trades")
    )

    assert [item.code for item in naive_results if isinstance(item, DataQualityWarning)] == [
        DataQualityCode.INVALID_TIMESTAMP
    ]
    assert [item.code for item in invalid_results if isinstance(item, DataQualityWarning)] == [
        DataQualityCode.INVALID_TIMESTAMP
    ]


def test_mbp10_trade_actions_produce_trades_and_book_updates_do_not_create_candles(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mbp10.parquet"
    _write_table(
        path,
        [
            {
                "ts_event": 1_735_689_600_000_000_000,
                "action": "T",
                "price": "17000.00",
                "size": 1,
                "side": b"B",
                "bid_px_00": "16999.75",
                "bid_sz_00": 5,
                "ask_px_00": "17000.25",
                "ask_sz_00": 6,
                "bid_px_01": "16999.50",
            },
            {
                "ts_event": 1_735_689_600_100_000_000,
                "action": "A",
                "price": "17000.25",
                "size": 99,
                "bid_px_00": "17000.00",
                "bid_sz_00": 7,
                "ask_px_00": "17000.50",
                "ask_sz_00": 8,
            },
            {
                "ts_event": 1_735_689_600_200_000_000,
                "action": b"Trade",
                "price": "17000.25",
                "size": 1,
            },
        ],
    )
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0", tick_timeframes=(2,), observation_duration_seconds=300
    )

    results = list(
        HistoricalParquetAdapter().scan([path], requested_symbol="NQ.c.0", schema="mbp-10")
    )
    for item in results:
        if not isinstance(item, DataQualityWarning):
            runtime.process_market_event(item)

    trades = [item for item in results if isinstance(item, TradeEvent)]
    top_of_book = [item for item in results if isinstance(item, TopOfBookEvent)]
    assert [trade.price_ticks for trade in trades] == [68_000, 68_001]
    assert len(top_of_book) == 2
    assert top_of_book[0].bid_price_ticks == 67_999
    assert top_of_book[0].bid_size == 5
    assert len(runtime.snapshot().recent_closed_bars) == 1


def test_mbp10_level_zero_bid_ask_aliases_are_projected(tmp_path: Path) -> None:
    path = tmp_path / "mbp10_aliases.parquet"
    _write_table(
        path,
        [
            {
                "ts_event": 1_735_689_600_000_000_000,
                "action": "M",
                "bid_px_00": 17_000_000_000_000,
                "bid_sz_00": 3,
                "ask_px_00": 17_000_250_000_000,
                "ask_sz_00": 4,
            }
        ],
    )

    results = list(
        HistoricalParquetAdapter().scan([path], requested_symbol="NQ.c.0", schema="mbp-10")
    )

    top_of_book = [item for item in results if isinstance(item, TopOfBookEvent)]
    assert len(top_of_book) == 1
    assert top_of_book[0].bid_price_ticks == 68_000
    assert top_of_book[0].bid_size == 3
    assert top_of_book[0].ask_price_ticks == 68_001
    assert top_of_book[0].ask_size == 4


def test_unsupported_schema_emits_error_without_events(tmp_path: Path) -> None:
    path = tmp_path / "unsupported.parquet"
    _write_table(path, [{"ts_event": 1_735_689_600_000_000_000, "price": "17000.00", "size": 1}])

    results = list(
        HistoricalParquetAdapter().scan([path], requested_symbol="NQ.c.0", schema="mbo")
    )

    assert len(results) == 1
    assert isinstance(results[0], DataQualityWarning)
    assert results[0].code == DataQualityCode.UNSUPPORTED_SCHEMA
    assert results[0].severity.value == "error"


def test_selected_columns_only_are_read_in_batches(monkeypatch, tmp_path: Path) -> None:
    observed_columns: list[list[str]] = []
    observed_batch_sizes: list[int] = []

    class FakeParquetFile:
        schema_arrow = SimpleNamespace(
            names=["ts_event", "price", "size", "instrument_id", "huge_historical_blob"]
        )

        def __init__(self, path: Path) -> None:
            self.path = path

        def iter_batches(self, *, columns: list[str], batch_size: int):
            observed_columns.append(columns)
            observed_batch_sizes.append(batch_size)
            yield pa.RecordBatch.from_pylist(
                [
                    {
                        "ts_event": 1_735_689_600_000_000_000,
                        "price": "17000.00",
                        "size": 1,
                        "instrument_id": 123,
                    }
                ]
            )

    monkeypatch.setattr(pq, "ParquetFile", FakeParquetFile)

    results = list(
        HistoricalParquetAdapter(batch_size=7).scan(
            [tmp_path / "synthetic.parquet"], requested_symbol="NQ.c.0", schema="trades"
        )
    )

    assert isinstance(results[0], TradeEvent)
    assert observed_columns == [["ts_event", "price", "size", "instrument_id"]]
    assert observed_batch_sizes == [7]
    assert "huge_historical_blob" not in observed_columns[0]


def test_mbp10_scan_reads_only_live_projection_without_requiring_deeper_depth(
    monkeypatch, tmp_path: Path
) -> None:
    observed_columns: list[list[str]] = []

    class FakeParquetFile:
        schema_arrow = SimpleNamespace(
            names=[
                "ts_event",
                "action",
                "price",
                "size",
                "bid_px_00",
                "bid_sz_00",
                "ask_px_00",
                "ask_sz_00",
                "bid_px_01",
                "bid_sz_01",
                "ask_px_09",
                "ask_sz_09",
                "publisher_specific_depth_blob",
            ]
        )

        def __init__(self, path: Path) -> None:
            self.path = path

        def iter_batches(self, *, columns: list[str], batch_size: int):
            _ = batch_size
            observed_columns.append(columns)
            yield pa.RecordBatch.from_pylist(
                [
                    {
                        "ts_event": 1_735_689_600_000_000_000,
                        "action": "T",
                        "price": "17000.00",
                        "size": 1,
                        "bid_px_00": "16999.75",
                        "bid_sz_00": 5,
                        "ask_px_00": "17000.25",
                        "ask_sz_00": 6,
                    }
                ]
            )

    monkeypatch.setattr(pq, "ParquetFile", FakeParquetFile)

    results = list(
        HistoricalParquetAdapter().scan(
            [tmp_path / "mbp10.parquet"], requested_symbol="NQ.c.0", schema="mbp-10"
        )
    )

    assert [type(item) for item in results] == [
        DataQualityWarning,
        TradeEvent,
        TopOfBookEvent,
    ]
    assert observed_columns == [
        [
            "ts_event",
            "action",
            "price",
            "size",
            "bid_px_00",
            "bid_sz_00",
            "ask_px_00",
            "ask_sz_00",
        ]
    ]
    selected = set(observed_columns[0])
    assert "bid_px_01" not in selected
    assert "bid_sz_01" not in selected
    assert "ask_px_09" not in selected
    assert "ask_sz_09" not in selected
    assert "publisher_specific_depth_blob" not in selected


def test_warning_sources_do_not_expose_full_local_paths(tmp_path: Path) -> None:
    path = tmp_path / "invalid_price.parquet"
    _write_table(path, [{"ts_event": 1_735_689_600_000_000_000, "price": "17000.10", "size": 1}])

    results = list(
        HistoricalParquetAdapter().scan([path], requested_symbol="NQ.c.0", schema="trades")
    )

    warning = results[0]
    assert isinstance(warning, DataQualityWarning)
    assert warning.source == "historical-parquet"
    assert str(tmp_path) not in str(warning.source)
    assert str(tmp_path) not in str(warning.metadata)


def test_warning_source_does_not_expose_secret_like_filename_when_label_absent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "api_key=abc..private.path.trades.parquet"
    _write_table(path, [{"ts_event": 1_735_689_600_000_000_000, "price": "17000.10", "size": 1}])

    results = list(
        HistoricalParquetAdapter().scan([path], requested_symbol="NQ.c.0", schema="trades")
    )

    warning = results[0]
    assert isinstance(warning, DataQualityWarning)
    dto = warning_to_dto(warning).model_dump()
    warning_text = f"{warning.source} {dto}"
    assert warning.source == "historical-parquet"
    assert "api_key" not in warning_text
    assert "abc" not in warning_text
    assert path.name not in warning_text


def test_historical_only_warning_metadata_is_count_only(tmp_path: Path) -> None:
    path = tmp_path / "wide_mbp.parquet"
    row = {
        "ts_event": 1_735_689_600_000_000_000,
        "bid_price": "17000.00",
        "ask_price": "17000.25",
    }
    row.update({f"bid_px_{index:02d}": "16999.75" for index in range(5)})
    _write_table(path, [row])

    results = list(
        HistoricalParquetAdapter(ignored_column_sample_size=2).scan(
            [path], requested_symbol="NQ.c.0", schema="mbp-1"
        )
    )

    warning = results[0]
    assert isinstance(warning, DataQualityWarning)
    assert warning.metadata["ignored_column_count"] == 4
    assert "columns" not in warning.metadata


def test_dataset_label_is_redacted_when_path_or_secret_like(tmp_path: Path) -> None:
    path = tmp_path / "invalid_price.parquet"
    _write_table(path, [{"ts_event": 1_735_689_600_000_000_000, "price": "17000.10", "size": 1}])

    results = list(
        HistoricalParquetAdapter(dataset_label="C:/Users/gonza/api_key=SECRET/raw.parquet").scan(
            [path], requested_symbol="NQ.c.0", schema="trades"
        )
    )

    warning = results[0]
    assert isinstance(warning, DataQualityWarning)
    dto = warning_to_dto(warning).model_dump()
    warning_text = f"{warning} {dto}".lower()
    assert warning.source == "historical-parquet"
    assert "api_key" not in warning_text
    assert "secret" not in warning_text
    assert "raw.parquet" not in warning_text


@pytest.mark.parametrize(
    "dataset_label",
    [
        "historical:nq:2026-02-22:mbp-10",
        "historical:nq:2026-02-22:trades",
    ],
)
def test_dataset_label_is_not_used_as_warning_source(
    tmp_path: Path, dataset_label: str
) -> None:
    path = tmp_path / "invalid_price.parquet"
    _write_table(path, [{"ts_event": 1_735_689_600_000_000_000, "price": "17000.10", "size": 1}])

    results = list(
        HistoricalParquetAdapter(dataset_label=dataset_label).scan(
            [path], requested_symbol="NQ.c.0", schema="trades"
        )
    )

    warning = results[0]
    assert isinstance(warning, DataQualityWarning)
    _assert_generic_historical_warning_source(warning_to_dto(warning).source)


def test_historical_only_warning_does_not_expose_secret_like_column_names(tmp_path: Path) -> None:
    path = tmp_path / "wide_mbp.parquet"
    _write_table(
        path,
        [
            {
                "ts_event": 1_735_689_600_000_000_000,
                "bid_price": "17000.00",
                "ask_price": "17000.25",
                "bid_px_01_token_SECRET": "16999.75",
            }
        ],
    )

    results = list(
        HistoricalParquetAdapter().scan([path], requested_symbol="NQ.c.0", schema="mbp-1")
    )

    warning = results[0]
    assert isinstance(warning, DataQualityWarning)
    dto = warning_to_dto(warning).model_dump()
    warning_text = f"{warning} {dto}".lower()
    assert warning.metadata == {"ignored_column_count": 1}
    assert "token" not in warning_text
    assert "secret" not in warning_text
    assert "bid_px_01_token_secret" not in warning_text


def test_adapter_has_no_real_data_path_hardcoding() -> None:
    adapter = HistoricalParquetAdapter()

    assert not hasattr(adapter, "data_path")
    with pytest.raises(FileNotFoundError):
        list(
            adapter.scan(
                [Path("synthetic_missing.parquet")],
                requested_symbol="NQ.c.0",
                schema="trades",
            )
        )
