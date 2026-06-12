"""W1 P3a: the historical parquet adapter is a thin shim over the SC source.

Normalization internals (ns decode, ordering, windowing, front-month, TOB dedup)
are covered by Strategy-Core's own tests; these pin the SHIM contract: SC events
convert into Trade-Lab domain events, warnings map into the domain vocabulary,
day-mode forwards to the canonical trading-day composition, and the legacy
per-row no-dedup quote path is gone.
"""

import logging
from datetime import UTC, date, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from trade_lab.adapters.historical_parquet import HistoricalParquetAdapter, HistoricalParquetSource
from trade_lab.domain.data_quality import DataQualityCode, DataQualityWarning
from trade_lab.domain.events import TopOfBookEvent, TradeEvent, TradeSide


def _write_table(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def _trades(events: list) -> list[TradeEvent]:
    return [event for event in events if isinstance(event, TradeEvent)]


def _quotes(events: list) -> list[TopOfBookEvent]:
    return [event for event in events if isinstance(event, TopOfBookEvent)]


def _warnings(events: list) -> list[DataQualityWarning]:
    return [event for event in events if isinstance(event, DataQualityWarning)]


def test_source_alias_is_the_adapter() -> None:
    assert HistoricalParquetSource is HistoricalParquetAdapter


def test_trades_scan_converts_sc_events_to_domain_events(tmp_path: Path) -> None:
    path = tmp_path / "trades.parquet"
    _write_table(
        path,
        [
            {"ts_event": datetime(2026, 2, 22, 14, 0, tzinfo=UTC), "price": 17000.25, "size": 2, "side": "B", "sequence": 1},
            {"ts_event": datetime(2026, 2, 22, 14, 1, tzinfo=UTC), "price": 17000.50, "size": 3, "side": "A", "sequence": 2},
            {"ts_event": datetime(2026, 2, 22, 14, 2, tzinfo=UTC), "price": 17000.75, "size": 1, "side": "N", "sequence": 3},
        ],
    )
    events = list(
        HistoricalParquetAdapter().scan([path], requested_symbol="NQ.c.0", schema="trades")
    )
    trades = _trades(events)
    assert [trade.price_ticks for trade in trades] == [68001, 68002, 68003]
    assert [trade.side for trade in trades] == [TradeSide.BUY, TradeSide.SELL, TradeSide.UNKNOWN]
    assert all(trade.requested_symbol == "NQ.c.0" for trade in trades)
    assert all(trade.source_schema == "trades" for trade in trades)


def test_mbp10_quotes_are_level0_deduped(tmp_path: Path) -> None:
    """The per-row no-dedup quote path is deleted: identical TOB rows emit once."""
    base = datetime(2026, 2, 22, 14, 0, tzinfo=UTC)
    path = tmp_path / "mbp10.parquet"
    _write_table(
        path,
        [
            {"ts_event": base, "action": "A", "price": 17000.0, "size": 1, "side": "B", "bid_px_00": 17000.0, "ask_px_00": 17000.5, "bid_sz_00": 5, "ask_sz_00": 5, "sequence": 1},
            {"ts_event": base.replace(second=1), "action": "A", "price": 17000.0, "size": 1, "side": "B", "bid_px_00": 17000.0, "ask_px_00": 17000.5, "bid_sz_00": 5, "ask_sz_00": 5, "sequence": 2},
            {"ts_event": base.replace(second=2), "action": "T", "price": 17000.5, "size": 2, "side": "B", "bid_px_00": 17000.0, "ask_px_00": 17000.5, "bid_sz_00": 5, "ask_sz_00": 5, "sequence": 3},
            {"ts_event": base.replace(second=3), "action": "A", "price": 17000.25, "size": 1, "side": "B", "bid_px_00": 17000.25, "ask_px_00": 17000.5, "bid_sz_00": 5, "ask_sz_00": 5, "sequence": 4},
        ],
    )
    events = list(
        HistoricalParquetAdapter().scan([path], requested_symbol="NQ.c.0", schema="mbp-10")
    )
    assert len(_trades(events)) == 1
    assert len(_quotes(events)) == 2  # initial state + the px change; dupes suppressed


def test_naive_timestamp_becomes_domain_invalid_timestamp_warning(tmp_path: Path) -> None:
    path = tmp_path / "trades.parquet"
    _write_table(
        path,
        [{"ts_event": datetime(2026, 2, 22, 14, 0), "price": 17000.0, "size": 1, "side": "B", "sequence": 1}],
    )
    events = list(
        HistoricalParquetAdapter(dataset_label="unit-label").scan(
            [path], requested_symbol="NQ.c.0", schema="trades"
        )
    )
    assert not _trades(events)
    warnings = _warnings(events)
    assert warnings
    assert warnings[0].code is DataQualityCode.INVALID_TIMESTAMP
    assert isinstance(warnings[0], DataQualityWarning)


def test_trading_day_mode_composes_canonical_window(tmp_path: Path) -> None:
    # Trading day 2026-02-18 (EST): [2026-02-17 23:00 UTC, 2026-02-18 23:00 UTC),
    # partitioned at UTC midnight across the two per-date files.
    root = tmp_path / "NQ"
    _write_table(
        root / "2026-02-17" / "trades.parquet",
        [
            {"ts_event": datetime(2026, 2, 17, 22, 0, tzinfo=UTC), "price": 16990.0, "size": 1, "side": "B", "sequence": 1},
            {"ts_event": datetime(2026, 2, 17, 23, 30, tzinfo=UTC), "price": 16991.0, "size": 1, "side": "B", "sequence": 2},
        ],
    )
    _write_table(
        root / "2026-02-18" / "trades.parquet",
        [
            {"ts_event": datetime(2026, 2, 18, 10, 0, tzinfo=UTC), "price": 16992.0, "size": 1, "side": "B", "sequence": 3},
            {"ts_event": datetime(2026, 2, 18, 23, 30, tzinfo=UTC), "price": 16993.0, "size": 1, "side": "B", "sequence": 4},
        ],
    )
    events = list(
        HistoricalParquetAdapter().scan(
            [],
            requested_symbol="NQ",
            schema="trades",
            trading_day=date(2026, 2, 18),
            symbol_dir=root,
        )
    )
    assert not _warnings(events)
    assert [trade.price_ticks for trade in _trades(events)] == [67964, 67968]


def test_trading_day_mode_missing_prior_day_warns_and_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "NQ"
    _write_table(
        root / "2026-02-18" / "trades.parquet",
        [{"ts_event": datetime(2026, 2, 18, 10, 0, tzinfo=UTC), "price": 17000.0, "size": 1, "side": "B", "sequence": 1}],
    )
    with caplog.at_level(logging.WARNING):
        events = list(
            HistoricalParquetAdapter().scan(
                [],
                requested_symbol="NQ",
                schema="trades",
                trading_day=date(2026, 2, 18),
                symbol_dir=root,
            )
        )
    warnings = _warnings(events)
    assert warnings and warnings[0].code is DataQualityCode.MISSING_PRIOR_DAY_FILE
    assert any("trading-day replay window degraded" in record.message for record in caplog.records)
    assert len(_trades(events)) == 1


def test_trading_day_mode_requires_symbol_dir(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="symbol_dir"):
        list(
            HistoricalParquetAdapter().scan(
                [], requested_symbol="NQ", schema="trades", trading_day=date(2026, 2, 18)
            )
        )


def test_front_month_flag_forwards_to_sc_source(tmp_path: Path) -> None:
    path = tmp_path / "mixed.parquet"
    _write_table(
        path,
        [
            {"ts_event": datetime(2026, 2, 22, 14, 0, tzinfo=UTC), "price": 17000.0, "size": 1, "side": "B", "instrument_id": 1, "raw_symbol": "NQH6", "sequence": 1},
            {"ts_event": datetime(2026, 2, 22, 14, 1, tzinfo=UTC), "price": 17000.0, "size": 1, "side": "B", "instrument_id": 1, "raw_symbol": "NQH6", "sequence": 2},
            {"ts_event": datetime(2026, 2, 22, 14, 2, tzinfo=UTC), "price": 17000.25, "size": 9, "side": "B", "instrument_id": 2, "raw_symbol": "NQH6-NQM6", "sequence": 3},
            {"ts_event": datetime(2026, 2, 22, 14, 3, tzinfo=UTC), "price": 17000.50, "size": 9, "side": "B", "instrument_id": 3, "raw_symbol": "NQM6", "sequence": 4},
        ],
    )
    events = list(
        HistoricalParquetAdapter(front_month_only=True).scan(
            [path], requested_symbol="NQ.c.0", schema="trades"
        )
    )
    trades = _trades(events)
    assert len(trades) == 2
    assert all(trade.price_ticks == 68000 for trade in trades)
