"""Historical parquet scans as a thin shim over the canonical Strategy-Core source.

W1 P3a: all historical-parquet normalization — row->event mapping, integer-ns
timestamp decode, windowing, front-month isolation, multi-file merge ordering and
top-of-book L1 dedup — lives in
``strategy_core.data.databento_parquet.DatabentoParquetSource``, THE reader both
research and serving consume. This adapter only converts the SC neutral events into
Trade-Lab domain events and maps data-quality warnings into the domain vocabulary.

The legacy per-row mbp-10 normalization is deleted: it emitted one quote per row
with NO level-0 dedup and decoded ns timestamps through the lossy float
``fromtimestamp`` path that Strategy-Core explicitly forbids.
"""

import logging
from collections.abc import Iterable, Iterator
from datetime import date, datetime
from pathlib import Path

from strategy_core.data.databento_parquet import DatabentoParquetSource as _ScParquetSource
from strategy_core.data.events import DataQualityWarning as ScDataQualityWarning
from strategy_core.types import Quote as ScQuote
from strategy_core.types import Trade as ScTrade

from trade_lab.domain.data_quality import DataQualityCode, DataQualitySeverity, DataQualityWarning
from trade_lab.domain.events import TopOfBookEvent, TradeEvent, TradeSide

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 65_536
DEFAULT_SOURCE_LABEL = "historical-parquet"

#: Databento aggressor-side encoding (verified in strategy_core.constants):
#: 'B' = buy aggressor, 'A' = sell aggressor; anything else is unknown.
_SIDE_FROM_SC = {"B": TradeSide.BUY, "A": TradeSide.SELL}


class HistoricalParquetAdapter:
    """Thin Trade-Lab event shim over ``DatabentoParquetSource``."""

    def __init__(
        self,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        dataset_label: str | None = None,
        front_month_only: bool = False,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.batch_size = batch_size
        self.dataset_label = dataset_label
        self.front_month_only = front_month_only

    def scan(
        self,
        paths: Iterable[Path],
        *,
        requested_symbol: str,
        schema: str,
        start_ts_utc: datetime | None = None,
        end_ts_utc: datetime | None = None,
        trading_day: date | None = None,
        symbol_dir: Path | None = None,
    ) -> Iterator[TradeEvent | TopOfBookEvent | DataQualityWarning]:
        """Yield Trade-Lab domain events from the canonical SC parquet source.

        With ``trading_day`` set (date-discovered replay sources), the scan is the
        canonical [prev-day 18:00 ET, trading-day 18:00 ET) two-file composition;
        a missing prior-day file degrades to single-file with a logged warning
        (surfaced on the event stream too). Without it, the explicit ``paths`` +
        global window mode is preserved for non-date catalog ids.
        """

        if trading_day is not None:
            if symbol_dir is None:
                raise ValueError("symbol_dir is required for trading-day scans")
            source = _ScParquetSource.for_trading_day(
                symbol_dir,
                trading_day,
                requested_symbol=requested_symbol,
                front_month_only=self.front_month_only,
                batch_size=self.batch_size,
            )
            for warning in source.pending_warnings:
                logger.warning(
                    "trading-day replay window degraded for %s: %s",
                    trading_day.isoformat(),
                    warning.message,
                )
        else:
            source = _ScParquetSource(
                paths=tuple(Path(path) for path in paths),
                requested_symbol=requested_symbol,
                schema=schema,
                start_ts_utc=start_ts_utc,
                end_ts_utc=end_ts_utc,
                front_month_only=self.front_month_only,
                batch_size=self.batch_size,
            )
        for item in source.events():
            if isinstance(item, ScTrade):
                yield TradeEvent(
                    event_ts_utc=item.event_ts_utc,
                    receive_ts_utc=None,
                    instrument_id=None,
                    requested_symbol=requested_symbol,
                    raw_symbol=None,
                    price_ticks=item.price_ticks,
                    size=item.size,
                    side=self._side(item.side),
                    source_schema=schema,
                )
            elif isinstance(item, ScQuote):
                yield TopOfBookEvent(
                    event_ts_utc=item.event_ts_utc,
                    instrument_id=None,
                    bid_price_ticks=item.bid_price_ticks,
                    bid_size=item.bid_size,
                    ask_price_ticks=item.ask_price_ticks,
                    ask_size=item.ask_size,
                    source_schema=schema,
                )
            elif isinstance(item, ScDataQualityWarning):
                yield self._convert_warning(item)

    @staticmethod
    def _side(value: str | None) -> TradeSide:
        return _SIDE_FROM_SC.get((value or "").upper(), TradeSide.UNKNOWN)

    def _convert_warning(self, warning: ScDataQualityWarning) -> DataQualityWarning:
        try:
            code = DataQualityCode(warning.code.value)
        except ValueError:
            code = DataQualityCode.INVALID_RECORD
        try:
            severity = DataQualitySeverity(warning.severity.value)
        except ValueError:
            severity = DataQualitySeverity.WARNING
        # Always the generic label: SC's redaction yields "<path>"/filenames, and the
        # catalog's dataset_label embeds schema/date fragments — neither may leak into
        # operator-facing warning sources (pinned by the catalog warning tests).
        return DataQualityWarning(
            code=code,
            message=warning.message,
            severity=severity,
            source=DEFAULT_SOURCE_LABEL,
            event_ts_utc=warning.event_ts_utc,
            metadata=dict(warning.metadata),
        )


#: The catalog-facing name; kept so replay wiring reads as "a source", not "an adapter".
HistoricalParquetSource = HistoricalParquetAdapter
