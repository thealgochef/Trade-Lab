"""Databento Historical API source for the live warm-start FALLBACK + PDH/PDL seed.

D-P-03: the Historical API exists solely for the live warm-start slice. Two
consumers remain after the Chicago display seed retired (W2 P1e):

* ``dbn_record_streams`` — the warm-start FALLBACK when the live gateway rejects
  the intraday replay-start subscribe: raw DBN records fed through the SAME
  ``normalize_provider_message`` path live records take. WARM-FIX P4: spans are
  per-schema — trades keep ``[trading-day 18:00 ET, now)`` while the quote schema
  is scoped to the market-context retention window (quotes only survive the
  drain inside that rolling buffer). The retention is read at fetch time; a
  later hot-swap to a wider-retention contract does NOT refetch.
* ``ohlcv_frame`` — the tiny prior-trading-day ohlcv-1h request reduced to
  (max high, min low) for ``runtime.load_prior_day_summary`` (W2 P1c), the same
  seed path research and cold replay use.

Importing this module never connects to Databento; a client is built only when a
fetch method runs, and ``record_fetcher``/``ohlcv_fetcher`` can be injected so
tests run without the SDK or network.
"""

import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from trade_lab.adapters.databento import DatabentoUnavailableError, is_databento_sdk_available

if TYPE_CHECKING:
    import pandas as pd

logger = logging.getLogger(__name__)

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
        self, *, end: datetime, schema_starts: tuple[tuple[str, datetime], ...]
    ) -> tuple[tuple[str, Iterable[Any]], ...]:
        """Per-schema DBN record streams over ``[start_of(schema), end)`` for warm start.

        WARM-FIX P4: each schema carries its own start (trades full-span, quotes
        retention-scoped), and the ``end <= start`` guard plus the availability
        clamp apply PER SCHEMA — a quote window that the availability lag has
        entirely swallowed yields an empty stream while trades still fetch.
        ``heapq.merge`` in the adapter drain tolerates the asymmetric spans (each
        stream is individually sorted; trades simply yield alone until the quote
        head appears).
        """

        if self._record_fetcher is not None:
            self.last_stream_end = end
            return tuple(
                (schema, () if end <= start else self._record_fetcher(schema, start, end))
                for schema, start in schema_starts
            )
        client = self._client()
        # Historical data lags real time by minutes; an unclamped end (≈ now)
        # triggers a 422 data_end_after_available_end error. One metadata call
        # serves every schema's clamp.
        availability = self._dataset_availability(client)
        streams: list[tuple[str, Iterable[Any]]] = []
        effective_ends: list[datetime] = []
        for schema, start in schema_starts:
            schema_end = self._clamped_end(availability, end, schema)
            effective_ends.append(schema_end)
            if schema_end <= start:
                streams.append((schema, ()))
                continue
            streams.append(
                (
                    schema,
                    client.timeseries.get_range(
                        dataset=self._dataset,
                        schema=schema,
                        symbols=[self._requested_symbol],
                        stype_in=self._stype_in,
                        start=start,
                        end=schema_end,
                    ),
                )
            )
        # The seam-gap warning reads the latest end any schema actually used.
        # Verify fix: with per-schema clamps the ends can DIVERGE (e.g. an mbp-1
        # ingestion hiccup lags its availability behind trades') and the shared
        # seam warning would then understate the laggard's hole — surface it.
        self.last_stream_end = max(effective_ends) if effective_ends else end
        if effective_ends:
            newest = max(effective_ends)
            for (schema, _start), schema_end in zip(schema_starts, effective_ends, strict=True):
                lag = (newest - schema_end).total_seconds()
                if lag > 60:
                    logger.warning(
                        "warm-fetch availability for schema %s lags the freshest "
                        "schema by %.0f s; that schema's warm slice ends early",
                        schema,
                        lag,
                    )
        return tuple(streams)

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

    def _dataset_availability(self, client: Any) -> Any:
        try:
            return client.metadata.get_dataset_range(self._dataset)
        except Exception:
            return None

    def _clamped_end(self, availability: Any, end: datetime, schema: str) -> datetime:
        import pandas as pd

        if availability is None:
            return end
        raw_end = None
        schema_ranges = availability.get("schema") if isinstance(availability, dict) else None
        if isinstance(schema_ranges, dict) and isinstance(schema_ranges.get(schema), dict):
            raw_end = schema_ranges[schema].get("end")
        if raw_end is None and isinstance(availability, dict):
            raw_end = availability.get("end")
        if raw_end is None:
            return end
        available_end = pd.Timestamp(raw_end).to_pydatetime()
        if available_end.tzinfo is None:
            available_end = available_end.replace(tzinfo=UTC)
        return min(end, available_end)
