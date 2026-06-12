"""Databento Historical API source for the live warm-start FALLBACK + PDH/PDL seed.

D-P-03: the Historical API exists solely for the live warm-start slice. Two
consumers remain after the Chicago display seed retired (W2 P1e):

* ``dbn_record_streams`` — the warm-start FALLBACK when the live gateway rejects
  the intraday replay-start subscribe: raw DBN records (trades + MBP-1) for
  ``[trading-day 18:00 ET, now)``, fed through the SAME
  ``normalize_provider_message`` path live records take.
* ``ohlcv_frame`` — the tiny prior-trading-day ohlcv-1h request reduced to
  (max high, min low) for ``runtime.load_prior_day_summary`` (W2 P1c), the same
  seed path research and cold replay use.

Importing this module never connects to Databento; a client is built only when a
fetch method runs, and ``record_fetcher``/``ohlcv_fetcher`` can be injected so
tests run without the SDK or network.
"""

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from trade_lab.adapters.databento import DatabentoUnavailableError, is_databento_sdk_available

if TYPE_CHECKING:
    import pandas as pd

# (schema, start, end) -> iterable of DBN records for that schema.
RecordFetcher = Callable[[str, datetime, datetime], Iterable[Any]]
# (start, end) -> ohlcv DataFrame with at least high/low columns.
OhlcvFetcher = Callable[[datetime, datetime], "pd.DataFrame"]

_OHLCV_SCHEMA = "ohlcv-1h"


class DatabentoHistoricalSource:
    """Fetch historical front-month records/summaries for live warm start."""

    def __init__(
        self,
        *,
        api_key: str | None,
        dataset: str,
        requested_symbol: str,
        stype_in: str,
        record_fetcher: RecordFetcher | None = None,
        ohlcv_fetcher: OhlcvFetcher | None = None,
    ) -> None:
        self._api_key = api_key
        self._dataset = dataset
        self._requested_symbol = requested_symbol
        self._stype_in = stype_in
        self._record_fetcher = record_fetcher
        self._ohlcv_fetcher = ohlcv_fetcher
        # The effective (availability-clamped) end of the last record-stream
        # fetch — the live adapter logs the seam gap between it and the live
        # subscribe instant (W2-FIX F2).
        self.last_stream_end: datetime | None = None

    @property
    def available(self) -> bool:
        # Injected fetchers (tests/custom deployments) are always usable; the
        # default fetchers need both the optional SDK and a configured API key.
        if self._record_fetcher is not None or self._ohlcv_fetcher is not None:
            return True
        return self._api_key is not None and is_databento_sdk_available()

    def dbn_record_streams(
        self, *, start: datetime, end: datetime, schemas: tuple[str, ...]
    ) -> tuple[tuple[str, Iterable[Any]], ...]:
        """Per-schema DBN record streams over ``[start, end)`` for warm start."""

        if self._record_fetcher is not None:
            self.last_stream_end = end
            return tuple((schema, self._record_fetcher(schema, start, end)) for schema in schemas)
        client = self._client()
        # Historical data lags real time by minutes; an unclamped end (≈ now)
        # triggers a 422 data_end_after_available_end error.
        end = self._clamp_end_to_available(client, end, schemas[0])
        self.last_stream_end = end
        if end <= start:
            return tuple((schema, ()) for schema in schemas)
        return tuple(
            (
                schema,
                client.timeseries.get_range(
                    dataset=self._dataset,
                    schema=schema,
                    symbols=[self._requested_symbol],
                    stype_in=self._stype_in,
                    start=start,
                    end=end,
                ),
            )
            for schema in schemas
        )

    def ohlcv_frame(self, *, start: datetime, end: datetime) -> "pd.DataFrame":
        """Hourly ohlcv bars over ``[start, end)`` (the prior-day summary input)."""

        if self._ohlcv_fetcher is not None:
            return self._ohlcv_fetcher(start, end)
        client = self._client()
        store = client.timeseries.get_range(
            dataset=self._dataset,
            schema=_OHLCV_SCHEMA,
            symbols=[self._requested_symbol],
            stype_in=self._stype_in,
            start=start,
            end=end,
        )
        frame: Any = store.to_df()
        return frame

    def _client(self) -> Any:
        if self._api_key is None:
            raise DatabentoUnavailableError(
                "Databento API key is not configured for historical fetches"
            )
        if not is_databento_sdk_available():
            raise DatabentoUnavailableError(
                "Databento SDK is not installed for historical fetches"
            )
        import databento

        return databento.Historical(self._api_key)

    def _clamp_end_to_available(self, client: Any, end: datetime, schema: str) -> datetime:
        import pandas as pd

        try:
            available = client.metadata.get_dataset_range(self._dataset)
        except Exception:
            return end
        raw_end = None
        schema_ranges = available.get("schema") if isinstance(available, dict) else None
        if isinstance(schema_ranges, dict) and isinstance(schema_ranges.get(schema), dict):
            raw_end = schema_ranges[schema].get("end")
        if raw_end is None and isinstance(available, dict):
            raw_end = available.get("end")
        if raw_end is None:
            return end
        available_end = pd.Timestamp(raw_end).to_pydatetime()
        if available_end.tzinfo is None:
            available_end = available_end.replace(tzinfo=UTC)
        return min(end, available_end)
