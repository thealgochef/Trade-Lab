"""Databento live adapter foundation.

The intended live schemas are trades, MBP-1/CMBP-1 top-of-book, definition,
status, and statistics. Only trades become ``TradeEvent`` and increment tick
bars; quotes/context are normalized for feed state only so high-volume quote
traffic cannot create fake bars or touches. Importing this module never connects
to Databento; ``start()`` is called only by the opt-in live controller.
"""

import asyncio
import heapq
import importlib.util
import logging
import re
from collections.abc import AsyncIterator, Callable, Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, NamedTuple

from trade_lab.domain.data_quality import (
    DataQualityCode,
    DataQualitySeverity,
    DataQualityWarning,
)
from trade_lab.domain.events import (
    DailyStatisticEvent,
    InstrumentDefinitionEvent,
    MarketEvent,
    MarketStatus,
    MarketStatusEvent,
    TopOfBookEvent,
    TradeEvent,
    TradeSide,
)
from trade_lab.domain.feed import FeedConnectionState, FeedStatus
from trade_lab.domain.prices import NQ_TICK_SIZE, price_to_ticks
from trade_lab.domain.trading_day import most_recent_session_open_utc

if TYPE_CHECKING:
    from trade_lab.adapters.databento_historical import DatabentoHistoricalSource


class DatabentoUnavailableError(RuntimeError):
    """Raised when live Databento is requested without the optional SDK."""


logger = logging.getLogger(__name__)
_SECRET_LABEL_RE = re.compile(r"(?i)(secret|token|password|api[_-]?key)\s*[:=]\s*[^\s,;]+")
_AUTH_HEADER_RE = re.compile(r"(?i)\bauthorization\s*[:=]\s*(?:bearer|basic)?\s*[^\s,;]+")
_AUTH_CREDENTIAL_RE = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s,;]+")
_POSIX_PATH_RE = re.compile(r"/(?:[^\s,;]+/)+[^\s,;]+")
_TRADE_SCHEMA_ALIASES = {"trade", "trades"}
_QUOTE_SCHEMA_ALIASES = {"mbp-1", "cmbp-1", "bbo", "tbbo", "cbbo", "tcbbo"}
_QUOTE_MESSAGE_ALIASES = {"bbomsg", "mbp1msg", "cmbp1msg", "tbbomsg", "cbbomsg", "tcbbomsg"}
_CONTEXT_SCHEMAS = {"definition", "status", "statistics"}


def is_databento_sdk_available() -> bool:
    """Return whether the optional Databento SDK appears importable.

    This uses import metadata only; it never creates a client or connects.
    """

    return importlib.util.find_spec("databento") is not None


class _QueuedProviderMessage(NamedTuple):
    schema: str | None
    message: Any


class _ProviderControlAction(NamedTuple):
    warning: DataQualityWarning | None = None
    drop: bool = False


class _DatabentoSdkFacade:
    """Tiny SDK boundary so tests can use fakes without Databento/network calls."""

    def __init__(self, sdk: Any) -> None:
        self._sdk = sdk

    def create_live_client(self, api_key: str) -> Any:
        live_type = getattr(self._sdk, "Live", None)
        if live_type is None:
            raise DatabentoUnavailableError(
                "Databento SDK does not expose a Live client compatible with this adapter. "
                "Upgrade the databento package on the backend host."
            )
        try:
            return live_type(key=api_key)
        except TypeError:
            try:
                return live_type(api_key)
            except TypeError as positional_exc:
                raise DatabentoUnavailableError(
                    "Databento Live client constructor signature is incompatible with this "
                    "adapter. Upgrade the databento package on the backend host."
                ) from positional_exc

    def add_callback(self, client: Any, callback: Callable[..., None]) -> None:
        if not hasattr(client, "add_callback"):
            raise DatabentoUnavailableError(
                "Databento Live client does not expose add_callback. Upgrade the databento SDK."
            )
        client.add_callback(callback)

    def subscribe(
        self,
        client: Any,
        *,
        dataset: str,
        schema: str,
        symbol: str,
        stype_in: str,
        start: datetime | None = None,
    ) -> None:
        if not hasattr(client, "subscribe"):
            raise DatabentoUnavailableError(
                "Databento Live client does not expose subscribe. Upgrade the databento SDK."
            )
        # W2 P1b: ``start`` engages the gateway's intraday replay (warm start). It is
        # only forwarded when set so fakes/older clients keep the narrow signature.
        if start is None:
            client.subscribe(dataset=dataset, schema=schema, symbols=[symbol], stype_in=stype_in)
        else:
            client.subscribe(
                dataset=dataset,
                schema=schema,
                symbols=[symbol],
                stype_in=stype_in,
                start=start,
            )

    def start(self, client: Any) -> None:
        if hasattr(client, "start"):
            client.start()
            return
        if hasattr(client, "run"):
            client.run()
            return
        raise DatabentoUnavailableError(
            "Databento Live client does not expose start/run. Upgrade the databento SDK."
        )

    def stop(self, client: Any) -> None:
        for name in ("stop", "close"):
            method = getattr(client, name, None)
            if method is not None:
                method()
                return


class DatabentoMarketDataFeed:
    def __init__(
        self,
        *,
        api_key: str,
        requested_symbol: str,
        dataset: str,
        stype_in: str = "continuous",
        trade_schema: str = "trades",
        quote_schema: str = "mbp-1",
        context_schemas: tuple[str, ...] = ("definition", "status", "statistics"),
        queue_maxsize: int = 10_000,
        sdk_module: Any | None = None,
        sdk_facade: _DatabentoSdkFacade | None = None,
        intraday_replay: bool = False,
        historical_source: "DatabentoHistoricalSource | None" = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Databento API key must be configured in backend environment")
        if queue_maxsize < 1:
            raise ValueError("Databento adapter queue_maxsize must be positive")
        if sdk_facade is None:
            if sdk_module is None:
                try:
                    import databento as sdk_module  # type: ignore[import-not-found]
                except ImportError as exc:
                    raise DatabentoUnavailableError(
                        "Databento SDK is not installed. Install the optional databento package "
                        "on the backend host before using live market data."
                    ) from exc
            sdk_facade = _DatabentoSdkFacade(sdk_module)
        self._sdk_facade = sdk_facade
        self._api_key = api_key
        self.requested_symbol = requested_symbol
        self.dataset = dataset
        self.stype_in = stype_in
        self.trade_schema = trade_schema
        self.quote_schema = quote_schema
        self.context_schemas = tuple(context_schemas)
        self._started = False
        self._client: Any = None
        self._queue: asyncio.Queue[_QueuedProviderMessage] = asyncio.Queue(maxsize=queue_maxsize)
        self._overflow_count = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._intraday_replay = intraday_replay
        self._historical_source = historical_source
        self._now = now_provider or (lambda: datetime.now(UTC))
        # W2 P1b FALLBACK: per-schema historical record streams staged by start()
        # when the gateway rejects the replay-start subscribe; drained by events()
        # BEFORE the live subscription so the queue can never overflow on history.
        self._warm_start_streams: tuple[tuple[str, Iterable[Any]], ...] | None = None

    async def start(self) -> None:
        if self._started:
            return
        self._warm_start_streams = None
        if self._intraday_replay:
            replay_start = most_recent_session_open_utc(self._now())
            try:
                self._connect(replay_start=replay_start)
                self._started = True
                return
            except Exception as exc:
                # W2 P1b: the gateway/entitlement rejected the replay-start subscribe
                # at runtime — fall back to the Historical API for the same window.
                logger.warning(
                    "Databento live replay-start subscribe was rejected "
                    "(exception_type=%s); falling back to the Historical API warm start",
                    type(exc).__name__,
                )
                self._teardown_client()
                self._warm_start_streams = await self._fetch_warm_start_streams(replay_start)
                if self._warm_start_streams is not None:
                    # The live subscribe happens in events() AFTER the historical
                    # records drain, so warm-start can never overflow the live queue.
                    self._loop = asyncio.get_running_loop()
                    self._started = True
                    return
        self._connect(replay_start=None)
        self._started = True

    def _connect(self, *, replay_start: datetime | None) -> None:
        client: Any = None
        try:
            client = self._sdk_facade.create_live_client(self._api_key)
            self._client = client
            self._loop = asyncio.get_running_loop()
            self._sdk_facade.add_callback(client, self._provider_callback)
            replayable = {self.trade_schema.lower(), self.quote_schema.lower()}
            for schema in _unique_schemas(
                (self.trade_schema, self.quote_schema, *self.context_schemas)
            ):
                self._sdk_facade.subscribe(
                    client,
                    dataset=self.dataset,
                    schema=schema,
                    symbol=self.requested_symbol,
                    stype_in=self.stype_in,
                    start=replay_start if schema in replayable else None,
                )
            self._sdk_facade.start(client)
        except Exception:
            if client is not None:
                try:
                    self._sdk_facade.stop(client)
                except Exception as stop_exc:  # pragma: no cover - defensive logging
                    logger.warning(
                        "Databento client cleanup after failed start raised: exception_type=%s",
                        type(stop_exc).__name__,
                    )
            self._client = None
            self._loop = None
            self._started = False
            raise

    def _teardown_client(self) -> None:
        if self._client is not None:
            try:
                self._sdk_facade.stop(self._client)
            except Exception as stop_exc:  # pragma: no cover - defensive logging
                logger.warning(
                    "Databento client cleanup after rejected replay-start raised: "
                    "exception_type=%s",
                    type(stop_exc).__name__,
                )
        self._client = None
        self._loop = None

    async def _fetch_warm_start_streams(
        self, replay_start: datetime
    ) -> tuple[tuple[str, Iterable[Any]], ...] | None:
        source = self._historical_source
        if source is None or not source.available:
            logger.warning(
                "Databento warm-start fallback unavailable: Historical API access is "
                "not configured; subscribing live without intraday history"
            )
            return None
        try:
            return await asyncio.to_thread(
                source.dbn_record_streams,
                start=replay_start,
                end=self._now(),
                schemas=(self.trade_schema, self.quote_schema),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Databento warm-start fallback fetch failed (exception_type=%s); "
                "subscribing live without intraday history",
                type(exc).__name__,
            )
            return None

    def _log_warm_start_seam_gap(self) -> None:
        # W2-FIX F2: the Historical slice's end is clamped to the dataset's
        # available end, which lags real time by minutes — the bars between that
        # clamp and the live subscribe are never seen. Make the gap visible.
        slice_end = getattr(self._historical_source, "last_stream_end", None)
        if slice_end is None:
            return
        gap_seconds = (self._now() - slice_end).total_seconds()
        logger.warning(
            "warm-start fallback slice ends %.1f s before live subscribe "
            "(historical availability lag)",
            gap_seconds,
        )

    async def stop(self) -> None:
        self._started = False
        loop = self._loop
        self._loop = None
        if self._client is not None:
            self._sdk_facade.stop(self._client)
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(lambda: None)
        self._client = None

    async def events(self) -> AsyncIterator[MarketEvent | DataQualityWarning | FeedStatus]:
        yield FeedStatus(
            state=FeedConnectionState.CONNECTED
            if self._started
            else FeedConnectionState.DISCONNECTED,
            mode="live",
            requested_symbol=self.requested_symbol,
            dataset=self.dataset,
            schema=self.trade_schema,
            last_message="Databento live adapter consuming provider callback queue.",
            metadata={"schemas": list(_unique_schemas(self._schemas))},
        )
        warm_streams = self._warm_start_streams
        self._warm_start_streams = None
        if warm_streams is not None:
            async for item in self._drain_warm_start_streams(warm_streams):
                yield item
            if not self._started:
                return
            self._log_warm_start_seam_gap()
            # Spec order: fetch -> feed -> subscribe live from now. A subscribe
            # failure here propagates and surfaces as a live-feed failure.
            self._connect(replay_start=None)
        while self._started or not self._queue.empty():
            await asyncio.sleep(0)
            warning = self._take_overflow_warning()
            if warning is not None:
                yield warning
                continue
            try:
                queued = await asyncio.wait_for(self._queue.get(), timeout=0.25)
            except TimeoutError:
                continue
            if isinstance(queued.message, BaseException):
                yield _warning(
                    DataQualityCode.UNSUPPORTED_SCHEMA,
                    "Databento provider callback reported an exception",
                    schema=queued.schema,
                    detail=_redact(str(queued.message), (self._api_key,)),
                )
                continue
            control = _classify_provider_control_message(
                queued.message, schema=queued.schema, secrets=(self._api_key,)
            )
            if control.drop:
                if control.warning is not None:
                    yield control.warning
                continue
            schema = _normalize_provider_schema(queued.schema, self.quote_schema)
            try:
                schema = schema or _infer_schema(queued.message, self.quote_schema)
                yield normalize_provider_message(
                    queued.message, requested_symbol=self.requested_symbol, schema=schema
                )
            except Exception as exc:
                yield _warning(
                    _code_for_normalization_error(exc),
                    "Databento provider message could not be normalized safely",
                    schema=schema,
                    detail=_redact(str(exc), (self._api_key,)),
                )

    async def _drain_warm_start_streams(
        self, streams: tuple[tuple[str, Iterable[Any]], ...]
    ) -> AsyncIterator[MarketEvent | DataQualityWarning]:
        """Yield normalized historical records (merged by ``ts_event``) for warm start.

        Records flow through the SAME ``normalize_provider_message`` path live
        records take, so warm-start events cannot diverge from live ones. The loop
        yields control periodically so a long intraday drain never starves the loop.
        """

        merged = heapq.merge(
            *(_tag_records(schema, records) for schema, records in streams),
            key=lambda item: _record_ts_event(item[1]),
        )
        count = 0
        for schema, record in merged:
            if not self._started:
                return
            normalized_schema = _normalize_provider_schema(schema, self.quote_schema) or schema
            try:
                yield normalize_provider_message(
                    record, requested_symbol=self.requested_symbol, schema=normalized_schema
                )
            except Exception as exc:
                yield _warning(
                    _code_for_normalization_error(exc),
                    "Databento warm-start record could not be normalized safely",
                    schema=_safe_provider_schema(normalized_schema),
                    detail=_redact(str(exc), (self._api_key,)),
                )
            count += 1
            if count % 500 == 0:
                await asyncio.sleep(0)
        logger.info("Databento warm-start fallback replayed %d historical records", count)

    @property
    def _schemas(self) -> tuple[str, ...]:
        return (self.trade_schema, self.quote_schema, *self.context_schemas)

    def _provider_callback(self, *args: Any, **kwargs: Any) -> None:
        # Databento invokes callbacks from SDK-managed threads. Keep this function
        # tiny and thread-safe: enqueue provider records only. The async iterator
        # later normalizes them and feeds ApplicationRuntime on the event loop.
        schema = _optional_str(kwargs, "schema") if kwargs else None
        message: Any
        if "record" in kwargs:
            message = kwargs["record"]
        elif "message" in kwargs:
            message = kwargs["message"]
        elif args:
            message = args[0]
        else:
            message = kwargs
        self._enqueue(schema, message)

    def _enqueue(self, schema: str | None, message: Any) -> None:
        if not self._started:
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._enqueue_on_loop, schema, message)

    def _enqueue_on_loop(self, schema: str | None, message: Any) -> None:
        if not self._started:
            return
        try:
            self._queue.put_nowait(_QueuedProviderMessage(schema, message))
        except asyncio.QueueFull:
            # Preserve callback arrival order already in the queue and drop newest;
            # this bounds memory under provider bursts. events() emits a warning.
            self._overflow_count += 1

    def _take_overflow_warning(self) -> DataQualityWarning | None:
        count = self._overflow_count
        self._overflow_count = 0
        if count == 0:
            return None
        return _warning(
            DataQualityCode.BACKPRESSURE_DROP,
            "Databento adapter queue overflow; newest provider messages were dropped",
            schema=None,
            dropped=count,
        )


def normalize_provider_message(
    message: Any,
    *,
    requested_symbol: str,
    schema: str,
) -> MarketEvent:
    """Normalize a Databento-like record/object to a canonical event.

    This accepts mappings or simple objects so unit tests can use fakes without
    importing the Databento SDK or making network calls.
    """

    lowered = schema.lower()
    if lowered in _TRADE_SCHEMA_ALIASES:
        return TradeEvent(
            event_ts_utc=_timestamp(message),
            receive_ts_utc=_optional_timestamp(message, "receive_ts_utc", "ts_recv"),
            instrument_id=_optional_int(message, "instrument_id", "instrument_id"),
            requested_symbol=requested_symbol,
            raw_symbol=_optional_str(message, "raw_symbol", "symbol"),
            price_ticks=_price_ticks(message, "price_ticks", "price", "px"),
            size=_int(message, "size", "qty", default=1),
            side=_side(message),
            source_schema="trades",
        )
    if lowered in _QUOTE_SCHEMA_ALIASES:
        quote_source = _top_of_book_source(message)
        return TopOfBookEvent(
            event_ts_utc=_timestamp(message),
            instrument_id=_optional_int(message, "instrument_id", "instrument_id"),
            bid_price_ticks=_optional_price_ticks(
                quote_source, "bid_price_ticks", "bid_px", "bid_price"
            ),
            bid_size=_optional_int(quote_source, "bid_size", "bid_sz", "bid_qty", "bid_quantity"),
            ask_price_ticks=_optional_price_ticks(
                quote_source, "ask_price_ticks", "ask_px", "ask_price"
            ),
            ask_size=_optional_int(quote_source, "ask_size", "ask_sz", "ask_qty", "ask_quantity"),
            source_schema=schema,
        )
    if lowered == "definition":
        return InstrumentDefinitionEvent(
            event_ts_utc=_timestamp(message),
            instrument_id=_int(message, "instrument_id", default=0),
            requested_symbol=requested_symbol,
            raw_symbol=_str(message, "raw_symbol", "symbol", default=requested_symbol),
            tick_size=Decimal(str(_get(message, "tick_size", default=NQ_TICK_SIZE))),
        )
    if lowered == "status":
        return MarketStatusEvent(
            event_ts_utc=_timestamp(message),
            instrument_id=_optional_int(message, "instrument_id"),
            status=_market_status(_optional_str(message, "status") or "unknown"),
            reason=_optional_str(message, "reason"),
        )
    if lowered == "statistics":
        return DailyStatisticEvent(
            event_ts_utc=_timestamp(message),
            instrument_id=_optional_int(message, "instrument_id"),
            statistic_type=_str(message, "statistic_type", "stat_type", default="unknown"),
            price_ticks=_optional_price_ticks(message, "price_ticks", "price"),
            source_schema="statistics",
        )
    raise ValueError(f"unsupported Databento schema: {schema}")


def _get(message: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(message, dict) and name in message:
            return message[name]
        if hasattr(message, name):
            return getattr(message, name)
    return default


def _top_of_book_source(message: Any) -> Any:
    if (
        _get(
            message,
            "bid_px",
            "bid_price",
            "bid_price_ticks",
            "ask_px",
            "ask_price",
            "ask_price_ticks",
        )
        is not None
    ):
        return message
    levels = _get(message, "levels")
    if levels is None:
        return message
    try:
        return levels[0]
    except (IndexError, KeyError, TypeError):
        return message


def _tag_records(schema: str, records: Iterable[Any]) -> Iterable[tuple[str, Any]]:
    # A real function (not a genexp) so each stream binds ITS schema eagerly.
    for record in records:
        yield (schema, record)


_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=UTC)


def _record_ts_event(record: Any) -> int:
    value = getattr(record, "ts_event", None)
    if value is None:
        header = getattr(record, "hdr", None)
        value = getattr(header, "ts_event", 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _timestamp(message: Any) -> datetime:
    value = _get(message, "event_ts_utc", "ts_event", "timestamp")
    if value is None:
        raise ValueError("provider message missing event timestamp")
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, int):
        # W2 P1a: ns since epoch decoded on the floor-µs INTEGER path. The float
        # fromtimestamp(ns / 1e9) route loses sub-µs precision near the float53
        # boundary and is deleted (same recipe as Strategy-Core's decoders).
        return _EPOCH_UTC + timedelta(microseconds=value // 1_000)
    text = str(value).replace("Z", "+00:00")
    return datetime.fromisoformat(text).astimezone(UTC)


def _optional_timestamp(message: Any, *names: str) -> datetime | None:
    value = _get(message, *names)
    if value is None:
        return None
    return _timestamp({"event_ts_utc": value})


def _int(message: Any, *names: str, default: int | None = None) -> int:
    value = _get(message, *names, default=default)
    if value is None:
        raise ValueError(f"provider message missing integer field {names[0]}")
    return int(value)


def _optional_int(message: Any, *names: str) -> int | None:
    value = _get(message, *names)
    return None if value is None else int(value)


def _str(message: Any, *names: str, default: str) -> str:
    return str(_get(message, *names, default=default))


def _optional_str(message: Any, *names: str) -> str | None:
    value = _get(message, *names)
    return None if value is None else str(value)


def _price_ticks(message: Any, *names: str) -> int:
    value = _get(message, *names)
    if value is None:
        raise ValueError("provider message missing price")
    if names[0] == "price_ticks" and _get(message, names[0]) is not None:
        return int(value)
    return price_to_ticks(str(_normalize_price_value(message, value, *names)))


def _optional_price_ticks(message: Any, *names: str) -> int | None:
    value = _get(message, *names)
    if value is None:
        return None
    if names[0].endswith("ticks") and _get(message, names[0]) is not None:
        return int(value)
    return price_to_ticks(str(_normalize_price_value(message, value, *names)))


def _normalize_price_value(message: Any, value: Any, *names: str) -> Any:
    pretty = _pretty_price_value(message, *names)
    if pretty is not None:
        return pretty
    if isinstance(value, int) and abs(value) >= 1_000_000_000:
        return Decimal(value) / Decimal(1_000_000_000)
    return value


def _pretty_price_value(message: Any, *names: str) -> Any:
    candidates: list[str] = []
    for name in names:
        candidates.append(f"pretty_{name}")
        if "_px" in name:
            candidates.append(name.replace("_px", "_pretty_px"))
        if name in {"price", "px"}:
            candidates.extend(("pretty_px", "pretty_price"))
        elif name.startswith("bid"):
            candidates.extend(
                ("pretty_bid_px", "pretty_bid_price", "bid_pretty_px", "bid_pretty_price")
            )
        elif name.startswith("ask"):
            candidates.extend(
                ("pretty_ask_px", "pretty_ask_price", "ask_pretty_px", "ask_pretty_price")
            )
    for candidate in candidates:
        value = _get(message, candidate)
        if value is not None:
            return value
    return None


def _side(message: Any) -> TradeSide:
    # W2: Databento's single-letter aggressor codes are 'B' (bid aggressor = buy)
    # and 'A' (ask aggressor = SELL) — the canonical mapping Strategy-Core's W1
    # parquet shim ratified. 'A' previously fell through to UNKNOWN here, starving
    # side-aware order-flow features of every sell print on the live path.
    # Databento emits single-letter codes only; the words "ask"/"bid" are not
    # provider values and contradicted the letter canon, so they map to UNKNOWN.
    value = (_optional_str(message, "side", "aggressor_side") or "unknown").lower()
    if value in {"buy", "b"}:
        return TradeSide.BUY
    if value in {"sell", "s", "a"}:
        return TradeSide.SELL
    return TradeSide.UNKNOWN


def _market_status(value: str) -> MarketStatus:
    normalized = value.lower()
    return MarketStatus(normalized) if normalized in set(MarketStatus) else MarketStatus.UNKNOWN


def _unique_schemas(schemas: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for schema in schemas:
        lowered = schema.lower()
        if lowered not in seen:
            seen.add(lowered)
            result.append(lowered)
    return tuple(result)


def _infer_schema(message: Any, configured_quote_schema: str) -> str:
    explicit = _optional_str(message, "schema", "source_schema", "record_type")
    if explicit:
        normalized = _normalize_provider_schema(explicit, configured_quote_schema)
        if normalized is not None:
            return normalized
    type_name = type(message).__name__
    normalized_type = type_name.lower()
    if type_name in {"TradeMsg"} or "trade" in normalized_type:
        return "trades"
    if (
        type_name in {"MBP1Msg", "CMBP1Msg", "BBOMsg", "TBBOMsg", "CBBOMsg", "TCBBOMsg"}
        or "bbo" in normalized_type
        or "mbp" in normalized_type
    ):
        return configured_quote_schema
    if (
        type_name == "InstrumentDefMsg"
        or "definition" in normalized_type
        or "instrumentdef" in normalized_type
    ):
        return "definition"
    if type_name == "StatusMsg" or "status" in normalized_type:
        return "status"
    if type_name == "StatMsg" or "stat" in normalized_type:
        return "statistics"
    quote_source = _top_of_book_source(message)
    if _get(quote_source, "bid_px", "bid_price", "ask_px", "ask_price") is not None:
        return configured_quote_schema
    if _get(message, "price", "px", "size", "qty") is not None:
        return "trades"
    raise ValueError("unsupported Databento provider record type")


def _code_for_normalization_error(exc: Exception) -> DataQualityCode:
    text = str(exc).lower()
    if "timestamp" in text or "isoformat" in text:
        return DataQualityCode.INVALID_TIMESTAMP
    if "price" in text or "tick" in text:
        return DataQualityCode.INVALID_PRICE
    if "schema" in text or "record type" in text:
        return DataQualityCode.UNSUPPORTED_SCHEMA
    return DataQualityCode.UNSUPPORTED_SCHEMA


def _normalize_provider_schema(value: str | None, configured_quote_schema: str) -> str | None:
    if value is None:
        return None
    lowered = value.lower()
    if lowered in _TRADE_SCHEMA_ALIASES:
        return "trades"
    if lowered in _QUOTE_SCHEMA_ALIASES:
        return lowered
    if lowered in _QUOTE_MESSAGE_ALIASES:
        return configured_quote_schema
    if lowered in _CONTEXT_SCHEMAS:
        return lowered
    return None


def _classify_provider_control_message(
    message: Any, *, schema: str | None, secrets: tuple[str, ...]
) -> _ProviderControlAction:
    type_name = type(message).__name__
    if type_name == "SystemMsg":
        return _ProviderControlAction(drop=True)
    if type_name == "SymbolMappingMsg":
        return _ProviderControlAction(drop=True)
    if type_name == "ErrorMsg":
        detail = _redact(_provider_error_detail(message), secrets)
        return _ProviderControlAction(
            warning=_warning(
                DataQualityCode.PROVIDER_ERROR,
                "Databento provider reported an error",
                schema=_safe_provider_schema(schema or _optional_str(message, "schema")),
                detail=detail,
            ),
            drop=True,
        )
    return _ProviderControlAction()


def _provider_error_detail(message: Any) -> str:
    parts: list[str] = []
    for name in ("code", "err", "error", "message", "detail"):
        value = _get(message, name)
        if value is not None:
            parts.append(f"{name}={value}")
    return "; ".join(parts)[:500] or "provider error"


def _safe_provider_schema(value: str | None) -> str | None:
    if value is None:
        return None
    lowered = value.lower()
    safe_schemas = _TRADE_SCHEMA_ALIASES | _QUOTE_SCHEMA_ALIASES | _CONTEXT_SCHEMAS
    return lowered if lowered in safe_schemas else None


def _warning(
    code: DataQualityCode,
    message: str,
    *,
    schema: str | None,
    detail: str | None = None,
    dropped: int | None = None,
) -> DataQualityWarning:
    metadata: dict[str, Any] = {}
    if schema is not None:
        metadata["schema"] = schema
    if detail:
        metadata["detail"] = detail
    if dropped is not None:
        metadata["dropped"] = dropped
    return DataQualityWarning(
        code=code,
        message=message,
        severity=DataQualitySeverity.WARNING,
        source="databento",
        metadata=metadata,
    )


def _redact(message: str, secrets: tuple[str, ...]) -> str:
    redacted = _WINDOWS_PATH_RE.sub("<path>", _POSIX_PATH_RE.sub("<path>", message))
    redacted = _AUTH_HEADER_RE.sub("authorization=<redacted>", redacted)
    redacted = _AUTH_CREDENTIAL_RE.sub("<redacted>", redacted)
    redacted = _SECRET_LABEL_RE.sub("<redacted>", redacted)
    for secret in secrets:
        if not secret:
            continue
        redacted = redacted.replace(secret, "<redacted>")
        for length in range(len(secret), 7, -1):
            redacted = redacted.replace(secret[:length], "<redacted>")
    return redacted
