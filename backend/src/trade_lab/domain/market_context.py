"""Rolling L1/L0 market-context buffer for pre-touch feature construction.

This bounded ring buffer retains the only two record kinds inference may read: raw
trades and best bid/ask quotes (level-1 / level-0). There is structurally no field
capable of holding L2/L3 depth, so no downstream feature can read book depth even by
accident. The buffer is owned by :class:`ApplicationRuntime` so live and replay feed
identical context by construction.
"""

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from trade_lab.domain.events import TradeSide

# Inference fires when an observation COMPLETES, i.e. ~interaction_window after
# the touch, yet approach features still need data back to touch - approach_window.
# So at completion time the buffer must still hold the start of the approach
# window: retention >= approach_window + interaction_window + margin. W1 P3b: the
# EFFECTIVE retention is contract-driven at activation (see ApplicationRuntime);
# this default is the no-model baseline.
DEFAULT_RETENTION_MINUTES = 45
# W1 P3b: SAFETY CEILING only — never the operative bound under a sane retention.
# Time-based eviction governs; this cap exists so a malformed stream cannot grow
# memory without limit. Measured: a 120-minute approach window over deduped L1 +
# trades is ~3.4M elements, so 6M covers the largest admissible contract window
# with headroom (the old 200k cap evicted the feature windows during NY RTH).
DEFAULT_MAX_ELEMENTS = 6_000_000


@dataclass(frozen=True, slots=True)
class BufferedTrade:
    """A trade retained for order-flow features; carries no depth."""

    event_ts_utc: datetime
    price_ticks: int
    size: int
    side: TradeSide


@dataclass(frozen=True, slots=True)
class BufferedQuote:
    """A best bid/ask snapshot; level-1 only, no depth ladder."""

    event_ts_utc: datetime
    bid_price_ticks: int
    ask_price_ticks: int


class MarketContextBuffer:
    """Time- and count-bounded ring buffer of trades and best bid/ask quotes.

    Appends are O(1); window slices are O(window). Eviction is driven by the newest
    appended timestamp so replay (which never sees wall-clock time) evicts identically
    to live.
    """

    def __init__(
        self,
        *,
        retention: timedelta = timedelta(minutes=DEFAULT_RETENTION_MINUTES),
        max_elements: int = DEFAULT_MAX_ELEMENTS,
    ) -> None:
        if retention.total_seconds() <= 0:
            raise ValueError("retention must be positive")
        if max_elements <= 0:
            raise ValueError("max_elements must be positive")
        self.retention = retention
        self.max_elements = max_elements
        self._trades: deque[BufferedTrade] = deque()
        self._quotes: deque[BufferedQuote] = deque()
        self._latest_ts: datetime | None = None

    def append_trade(
        self, event_ts_utc: datetime, price_ticks: int, size: int, side: TradeSide
    ) -> None:
        """Append a trade; only price/size/side are retained (no depth)."""

        ts = _ensure_utc(event_ts_utc)
        self._trades.append(BufferedTrade(ts, price_ticks, size, side))
        self._advance(ts)

    def append_quote(
        self, event_ts_utc: datetime, bid_price_ticks: int | None, ask_price_ticks: int | None
    ) -> None:
        """Append a best bid/ask snapshot.

        Quotes missing either side are dropped: a one-sided book yields no usable
        mid-price and the L1 features require both touches of the spread.
        """

        if bid_price_ticks is None or ask_price_ticks is None:
            return
        ts = _ensure_utc(event_ts_utc)
        self._quotes.append(BufferedQuote(ts, bid_price_ticks, ask_price_ticks))
        self._advance(ts)

    def trades_in_window(
        self, start_ts_utc: datetime, end_ts_utc: datetime
    ) -> tuple[BufferedTrade, ...]:
        """Trades with ``start <= ts < end`` (end-exclusive, matching replay slices)."""

        start = _ensure_utc(start_ts_utc)
        end = _ensure_utc(end_ts_utc)
        return tuple(
            trade for trade in self._trades if start <= trade.event_ts_utc < end
        )

    def quotes_in_window(
        self, start_ts_utc: datetime, end_ts_utc: datetime
    ) -> tuple[BufferedQuote, ...]:
        """Quotes with ``start <= ts < end`` (end-exclusive)."""

        start = _ensure_utc(start_ts_utc)
        end = _ensure_utc(end_ts_utc)
        return tuple(
            quote for quote in self._quotes if start <= quote.event_ts_utc < end
        )

    def latest_mid_price_ticks(self) -> float | None:
        """Mid-price (in ticks) of the most recent quote, or ``None`` if no quotes."""

        if not self._quotes:
            return None
        quote = self._quotes[-1]
        return (quote.bid_price_ticks + quote.ask_price_ticks) / 2

    def set_retention(self, retention: timedelta) -> None:
        """Re-bound the time window (W1 P3b: contract-driven at model activation).

        Shrinking re-evicts immediately so the buffer never serves a window wider
        than the active contract is entitled to.
        """

        if retention.total_seconds() <= 0:
            raise ValueError("retention must be positive")
        self.retention = retention
        self._evict()

    def reset(self) -> None:
        """Drop all retained context. Called when the runtime resets for a new replay."""

        self._trades.clear()
        self._quotes.clear()
        self._latest_ts = None

    @property
    def trade_count(self) -> int:
        return len(self._trades)

    @property
    def quote_count(self) -> int:
        return len(self._quotes)

    def _advance(self, ts: datetime) -> None:
        if self._latest_ts is None or ts > self._latest_ts:
            self._latest_ts = ts
        self._evict()

    def _evict(self) -> None:
        if self._latest_ts is not None:
            cutoff = self._latest_ts - self.retention
            while self._trades and self._trades[0].event_ts_utc < cutoff:
                self._trades.popleft()
            while self._quotes and self._quotes[0].event_ts_utc < cutoff:
                self._quotes.popleft()
        # Count bound: trim oldest across the combined element budget so neither deque
        # alone can pin memory. Drop from whichever is older to preserve recency.
        while len(self._trades) + len(self._quotes) > self.max_elements:
            if self._trades and (
                not self._quotes
                or self._trades[0].event_ts_utc <= self._quotes[0].event_ts_utc
            ):
                self._trades.popleft()
            else:
                self._quotes.popleft()


def _ensure_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError("market-context timestamps must be timezone-aware UTC datetimes")
    return ts.astimezone(UTC)
