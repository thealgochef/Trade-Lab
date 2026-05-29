"""Read synthetic/live-compatible Parquet records into canonical events.

The adapter projects selected columns only and ignores historical-only depth
fields. That keeps replay/backfill scans aligned with the future live schema and
avoids accidentally adding MBP-10-only runtime features.
"""

from collections.abc import Iterable, Iterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from heapq import heappop, heappush
from itertools import count
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from trade_lab.domain.data_quality import DataQualityCode, DataQualitySeverity, DataQualityWarning
from trade_lab.domain.events import TopOfBookEvent, TradeEvent, TradeSide
from trade_lab.domain.prices import PriceError, price_to_ticks

TRADE_REQUIRED = ("ts_event", "price", "size")
TRADE_REQUIRED_SET = frozenset(TRADE_REQUIRED)
MBP10_TRADE_REQUIRED_SET = frozenset(("ts_event", "action", "price", "size"))
TOB_REQUIRED_ALIASES = (
    ("ts_event",),
    ("bid_price", "bid_px", "bid"),
    ("ask_price", "ask_px", "ask"),
)
COMMON_OPTIONAL = ("ts_recv", "instrument_id", "raw_symbol", "symbol", "side")
TRADE_SELECTED = tuple(TRADE_REQUIRED) + COMMON_OPTIONAL
MBP10_SELECTED = (
    "ts_event",
    "ts_recv",
    "instrument_id",
    "raw_symbol",
    "symbol",
    "action",
    "side",
    "price",
    "size",
    "bid_price",
    "bid_px",
    "bid",
    "bid_px_00",
    "bid_size",
    "bid_sz",
    "bid_sz_00",
    "ask_price",
    "ask_px",
    "ask",
    "ask_px_00",
    "ask_size",
    "ask_sz",
    "ask_sz_00",
)
TOB_SELECTED = (
    "ts_event",
    "instrument_id",
    "bid_price",
    "bid_px",
    "bid",
    "bid_size",
    "ask_price",
    "ask_px",
    "ask",
    "ask_size",
)
HISTORICAL_ONLY_PREFIXES = ("bid_px_", "ask_px_", "bid_sz_", "ask_sz_", "bid_ct_", "ask_ct_")
DEFAULT_BATCH_SIZE = 65_536
DEFAULT_IGNORED_COLUMN_SAMPLE_SIZE = 20
DEFAULT_SOURCE_LABEL = "historical-parquet"


class HistoricalParquetAdapter:
    """Safe foundation for tests and future replay scans; no hardcoded data path."""

    def __init__(
        self,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        dataset_label: str | None = None,
        ignored_column_sample_size: int = DEFAULT_IGNORED_COLUMN_SAMPLE_SIZE,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if ignored_column_sample_size < 0:
            raise ValueError("ignored_column_sample_size must be non-negative")
        self.batch_size = batch_size
        self.dataset_label = dataset_label
        self.ignored_column_sample_size = ignored_column_sample_size

    def scan(
        self,
        paths: Iterable[Path],
        *,
        requested_symbol: str,
        schema: str,
        start_ts_utc: datetime | None = None,
        end_ts_utc: datetime | None = None,
    ) -> Iterator[TradeEvent | TopOfBookEvent | DataQualityWarning]:
        schema_key = schema.lower()
        streams = [
            iter(
                self._scan_file(
                    Path(path),
                    requested_symbol=requested_symbol,
                    schema=schema_key,
                    start_ts_utc=start_ts_utc,
                    end_ts_utc=end_ts_utc,
                )
            )
            for path in paths
        ]
        if not streams:
            return

        tie_breaker = count()
        heap: list[tuple[datetime, int, int, TradeEvent | TopOfBookEvent]] = []
        primed = [False] * len(streams)
        exhausted = [False] * len(streams)

        def all_streams_ready() -> bool:
            return all(
                is_primed or is_exhausted
                for is_primed, is_exhausted in zip(primed, exhausted, strict=True)
            )

        while not all_streams_ready():
            for stream_index, stream in enumerate(streams):
                if primed[stream_index] or exhausted[stream_index]:
                    continue
                try:
                    item = next(stream)
                except StopIteration:
                    exhausted[stream_index] = True
                    continue
                if isinstance(item, DataQualityWarning):
                    yield item
                    break
                heappush(heap, (item.event_ts_utc, next(tie_breaker), stream_index, item))
                primed[stream_index] = True
                break

        while heap:
            _, _, stream_index, item = heappop(heap)
            yield item
            primed[stream_index] = False
            while not all_streams_ready():
                try:
                    next_item = next(streams[stream_index])
                except StopIteration:
                    exhausted[stream_index] = True
                    break
                if isinstance(next_item, DataQualityWarning):
                    yield next_item
                    continue
                heappush(heap, (next_item.event_ts_utc, next(tie_breaker), stream_index, next_item))
                primed[stream_index] = True
                break

    def _scan_file(
        self,
        path: Path,
        *,
        requested_symbol: str,
        schema: str,
        start_ts_utc: datetime | None,
        end_ts_utc: datetime | None,
    ) -> Iterator[TradeEvent | TopOfBookEvent | DataQualityWarning]:
        parquet = pq.ParquetFile(path)
        names = set(parquet.schema_arrow.names)
        source = self._safe_source(path)
        start_utc = start_ts_utc.astimezone(UTC) if start_ts_utc is not None else None
        end_utc = end_ts_utc.astimezone(UTC) if end_ts_utc is not None else None
        yield from self._historical_only_warnings(source, names)

        is_mbp10 = schema in {"mbp-10", "mbp10", "cmbp-10", "cmbp10"}
        if schema in {"trades", "trade"}:
            missing = TRADE_REQUIRED_SET - names
            selected = [name for name in TRADE_SELECTED if name in names]
            normalizer = self._normalize_trade
        elif is_mbp10:
            missing = self._missing_mbp10_columns(names)
            selected = [name for name in MBP10_SELECTED if name in names]
            normalizer = self._normalize_mbp10
        elif schema in {"mbp-1", "cmbp-1", "bbo", "cbbo"}:
            missing = self._missing_tob_columns(names)
            selected = [name for name in TOB_SELECTED if name in names]
            normalizer = self._normalize_top_of_book
        else:
            yield self._warning(
                DataQualityCode.UNSUPPORTED_SCHEMA,
                "unsupported historical parquet schema",
                source=source,
                severity=DataQualitySeverity.ERROR,
            )
            return

        if missing:
            yield self._warning(
                DataQualityCode.MISSING_REQUIRED_COLUMN,
                "missing required historical parquet fields",
                source=source,
                severity=DataQualitySeverity.ERROR,
            )
            return

        for batch in parquet.iter_batches(columns=selected, batch_size=self.batch_size):
            batch_events: list[TradeEvent | TopOfBookEvent] = []
            batch_warnings: list[DataQualityWarning] = []
            for row in batch.to_pylist():
                normalized = normalizer(
                    row,
                    requested_symbol=requested_symbol,
                    schema=schema,
                    source=source,
                )
                normalized_items = normalized if isinstance(normalized, tuple) else (normalized,)
                for item in normalized_items:
                    if item is None:
                        continue
                    if isinstance(item, DataQualityWarning):
                        batch_warnings.append(item)
                        continue
                    ts = item.event_ts_utc
                    if start_utc is not None and ts < start_utc:
                        continue
                    if end_utc is not None and ts >= end_utc:
                        continue
                    batch_events.append(item)
            # Emit row data-quality warnings before market events from the same
            # bounded batch. This preserves replay max_events as an early guard
            # for warning-heavy files while keeping only intra-batch event sorting.
            yield from batch_warnings
            yield from sorted(batch_events, key=lambda event: event.event_ts_utc)

    def _normalize_trade(
        self, row: dict[str, Any], *, requested_symbol: str, schema: str, source: str
    ) -> TradeEvent | DataQualityWarning:
        ts = self._timestamp(row.get("ts_event"), field_name="ts_event", source=source)
        if isinstance(ts, DataQualityWarning):
            return ts
        recv = (
            self._timestamp(row.get("ts_recv"), field_name="ts_recv", source=source)
            if row.get("ts_recv")
            else None
        )
        if isinstance(recv, DataQualityWarning):
            return recv
        price_ticks = self._price_ticks(row.get("price"), field_name="price", source=source)
        if isinstance(price_ticks, DataQualityWarning):
            return price_ticks
        size = row.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            return self._warning(
                DataQualityCode.INVALID_RECORD,
                "invalid historical parquet record",
                source,
            )
        return TradeEvent(
            event_ts_utc=ts,
            receive_ts_utc=recv,
            instrument_id=self._optional_int(row.get("instrument_id")),
            requested_symbol=requested_symbol,
            raw_symbol=row.get("raw_symbol") or row.get("symbol"),
            price_ticks=price_ticks,
            size=size,
            side=self._side(row.get("side")),
            source_schema=schema,
        )

    def _normalize_top_of_book(
        self, row: dict[str, Any], *, requested_symbol: str, schema: str, source: str
    ) -> TopOfBookEvent | DataQualityWarning:
        _ = requested_symbol
        ts = self._timestamp(row.get("ts_event"), field_name="ts_event", source=source)
        if isinstance(ts, DataQualityWarning):
            return ts
        bid = self._price_ticks(
            self._first(row, ("bid_price", "bid_px", "bid", "bid_px_00")),
            field_name="bid",
            source=source,
        )
        if isinstance(bid, DataQualityWarning):
            return bid
        ask = self._price_ticks(
            self._first(row, ("ask_price", "ask_px", "ask", "ask_px_00")),
            field_name="ask",
            source=source,
        )
        if isinstance(ask, DataQualityWarning):
            return ask
        return TopOfBookEvent(
            event_ts_utc=ts,
            instrument_id=self._optional_int(row.get("instrument_id")),
            bid_price_ticks=bid,
            bid_size=self._optional_int(self._first(row, ("bid_size", "bid_sz", "bid_sz_00"))),
            ask_price_ticks=ask,
            ask_size=self._optional_int(self._first(row, ("ask_size", "ask_sz", "ask_sz_00"))),
            source_schema=schema,
        )

    def _normalize_mbp10(
        self, row: dict[str, Any], *, requested_symbol: str, schema: str, source: str
    ) -> tuple[TradeEvent | TopOfBookEvent | DataQualityWarning | None, ...]:
        items: list[TradeEvent | TopOfBookEvent | DataQualityWarning] = []
        if self._is_trade_action(row.get("action")):
            trade = self._normalize_trade(
                row, requested_symbol=requested_symbol, schema="mbp-10", source=source
            )
            items.append(trade)

        if self._has_top_of_book(row):
            top_of_book = self._normalize_top_of_book(
                row, requested_symbol=requested_symbol, schema="mbp-10", source=source
            )
            items.append(top_of_book)

        return tuple(items) if items else (None,)

    def _timestamp(
        self, value: Any, *, field_name: str, source: str
    ) -> datetime | DataQualityWarning:
        _ = field_name
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return self._warning(
                    DataQualityCode.INVALID_TIMESTAMP,
                    "invalid historical parquet timestamp",
                    source,
                )
            return value.astimezone(UTC)
        if isinstance(value, int) and not isinstance(value, bool):
            try:
                return datetime.fromtimestamp(value / 1_000_000_000, tz=UTC)
            except (OSError, OverflowError, ValueError):
                return self._warning(
                    DataQualityCode.INVALID_TIMESTAMP,
                    "invalid historical parquet timestamp",
                    source,
                )
        return self._warning(
            DataQualityCode.INVALID_TIMESTAMP,
            "invalid historical parquet timestamp",
            source,
        )

    def _price_ticks(self, value: Any, *, field_name: str, source: str) -> int | DataQualityWarning:
        try:
            if isinstance(value, int) and not isinstance(value, bool):
                if field_name.endswith("ticks"):
                    return value
                if abs(value) >= 1_000_000_000:
                    normalized = Decimal(value) / Decimal(1_000_000_000)
                else:
                    normalized = Decimal(value)
                return price_to_ticks(normalized)
            return price_to_ticks(str(value))
        except (PriceError, ValueError):
            return self._warning(
                DataQualityCode.INVALID_PRICE,
                "invalid historical parquet price",
                source,
            )

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @staticmethod
    def _first(row: dict[str, Any], names: Sequence[str]) -> Any:
        for name in names:
            if row.get(name) is not None:
                return row[name]
        return None

    @staticmethod
    def _is_trade_action(value: Any) -> bool:
        if isinstance(value, bytes):
            try:
                value = value.decode("ascii")
            except UnicodeDecodeError:
                return False
        normalized = str(value).strip().lower()
        return normalized in {"t", "trade"}

    def _has_top_of_book(self, row: dict[str, Any]) -> bool:
        has_bid = self._first(row, ("bid_price", "bid_px", "bid", "bid_px_00")) is not None
        has_ask = self._first(row, ("ask_price", "ask_px", "ask", "ask_px_00")) is not None
        return has_bid and has_ask

    @staticmethod
    def _side(value: Any) -> TradeSide:
        if str(value).lower() in {"buy", "b", "1"}:
            return TradeSide.BUY
        if str(value).lower() in {"sell", "s", "-1"}:
            return TradeSide.SELL
        return TradeSide.UNKNOWN

    def _missing_tob_columns(self, names: set[str]) -> set[str]:
        missing: set[str] = set()
        for aliases in TOB_REQUIRED_ALIASES:
            if not any(alias in names for alias in aliases):
                missing.add("/".join(aliases))
        return missing

    def _missing_mbp10_columns(self, names: set[str]) -> set[str]:
        has_trade_projection = names >= MBP10_TRADE_REQUIRED_SET
        has_top_of_book = not self._missing_mbp10_tob_columns(names)
        if has_trade_projection or has_top_of_book:
            return set()
        missing = MBP10_TRADE_REQUIRED_SET - names
        missing.update(self._missing_mbp10_tob_columns(names))
        return missing

    def _missing_mbp10_tob_columns(self, names: set[str]) -> set[str]:
        missing: set[str] = set()
        aliases_by_field = (
            ("ts_event",),
            ("bid_price", "bid_px", "bid", "bid_px_00"),
            ("ask_price", "ask_px", "ask", "ask_px_00"),
        )
        for aliases in aliases_by_field:
            if not any(alias in names for alias in aliases):
                missing.add("/".join(aliases))
        return missing

    def _historical_only_warnings(
        self, source: str, names: set[str]
    ) -> Iterator[DataQualityWarning]:
        ignored = sorted(name for name in names if self._is_historical_only_column(name))
        if ignored:
            yield self._warning(
                DataQualityCode.HISTORICAL_ONLY_FIELD_IGNORED,
                "ignored historical-only columns outside the live v1 contract",
                source=source,
                metadata={"ignored_column_count": len(ignored)},
                severity=DataQualitySeverity.INFO,
            )

    @staticmethod
    def _is_historical_only_column(name: str) -> bool:
        if name.endswith("_00"):
            return False
        return name.startswith(HISTORICAL_ONLY_PREFIXES) or name.endswith("_10")

    @staticmethod
    def _safe_source(path: Path) -> str:
        _ = path
        return DEFAULT_SOURCE_LABEL

    @staticmethod
    def _warning(
        code: DataQualityCode,
        message: str,
        source: str | None = None,
        *,
        severity: DataQualitySeverity = DataQualitySeverity.WARNING,
        metadata: dict[str, Any] | None = None,
    ) -> DataQualityWarning:
        return DataQualityWarning(
            code=code,
            message=message,
            severity=severity,
            source=source,
            metadata=metadata or {},
        )


# Phase 2C replay services speak in terms of HistoricalMarketDataSource. Keep the
# existing adapter name while exposing the source-oriented alias used by docs/tests.
HistoricalParquetSource = HistoricalParquetAdapter
