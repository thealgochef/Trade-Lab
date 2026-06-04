"""Databento Historical API source for live-mode warm-up seeding.

Live streaming only has access to L0/L1 (trades + MBP-1), so the warm-up that
pre-fills the chart with the last few sessions uses the same L0 trade prints from
the Historical API. The continuous front-month symbol resolves to a single outright
contract server-side, so — unlike the local MBP-10 dumps — there is no spread or
back-month contamination to filter here.

Trades are returned as a pandas DataFrame (``DBNStore.to_df``) so the seed service can
build tick bars vectorized; per-record Python normalization is ~100x slower and made
the warm-up effectively never finish. Importing this module never connects to
Databento; the client is built only when ``trades_frame`` is called, and a
``frame_fetcher`` can be injected so tests run without the SDK or network.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from trade_lab.adapters.databento import DatabentoUnavailableError, is_databento_sdk_available

if TYPE_CHECKING:
    import pandas as pd

# (start, end) -> trades DataFrame with at least ts_event, price, size columns.
FrameFetcher = Callable[[datetime, datetime], "pd.DataFrame"]

_SEED_TRADE_SCHEMA = "trades"


class DatabentoHistoricalSource:
    """Fetch historical front-month trade prints (as a DataFrame) for warm-up seeding."""

    def __init__(
        self,
        *,
        api_key: str | None,
        dataset: str,
        requested_symbol: str,
        stype_in: str,
        frame_fetcher: FrameFetcher | None = None,
    ) -> None:
        self._api_key = api_key
        self._dataset = dataset
        self._requested_symbol = requested_symbol
        self._stype_in = stype_in
        self._has_custom_fetcher = frame_fetcher is not None
        self._frame_fetcher = frame_fetcher or self._default_frame_fetcher

    @property
    def available(self) -> bool:
        # An injected fetcher (tests/custom deployments) is always usable; the default
        # fetcher needs both the optional SDK and a configured API key.
        if self._has_custom_fetcher:
            return True
        return self._api_key is not None and is_databento_sdk_available()

    def trades_frame(self, *, start: datetime, end: datetime) -> "pd.DataFrame":
        """Return front-month trade prints over ``[start, end)`` as a DataFrame."""

        return self._frame_fetcher(start, end)

    def _default_frame_fetcher(self, start: datetime, end: datetime) -> "pd.DataFrame":
        if self._api_key is None:
            raise DatabentoUnavailableError("Databento API key is not configured for seeding")
        if not is_databento_sdk_available():
            raise DatabentoUnavailableError("Databento SDK is not installed for seeding")
        import databento
        import pandas as pd

        client = databento.Historical(self._api_key)
        # Historical data lags real time by minutes, so `end` (≈ now) is routinely past the
        # available range and otherwise triggers a 422 data_end_after_available_end error.
        end = self._clamp_end_to_available(client, end)
        if end <= start:
            return pd.DataFrame(columns=["ts_event", "price", "size"])
        store = client.timeseries.get_range(
            dataset=self._dataset,
            schema=_SEED_TRADE_SCHEMA,
            symbols=[self._requested_symbol],
            stype_in=self._stype_in,
            start=start,
            end=end,
        )
        frame: Any = store.to_df()
        return frame

    def _clamp_end_to_available(self, client: Any, end: datetime) -> datetime:
        import pandas as pd

        try:
            available = client.metadata.get_dataset_range(self._dataset)
        except Exception:
            return end
        raw_end = None
        schema_ranges = available.get("schema") if isinstance(available, dict) else None
        if isinstance(schema_ranges, dict) and isinstance(
            schema_ranges.get(_SEED_TRADE_SCHEMA), dict
        ):
            raw_end = schema_ranges[_SEED_TRADE_SCHEMA].get("end")
        if raw_end is None and isinstance(available, dict):
            raw_end = available.get("end")
        if raw_end is None:
            return end
        available_end = pd.Timestamp(raw_end).to_pydatetime()
        if available_end.tzinfo is None:
            available_end = available_end.replace(tzinfo=UTC)
        return min(end, available_end)
