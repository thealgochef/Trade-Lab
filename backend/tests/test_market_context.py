from datetime import UTC, datetime, timedelta

import pytest

from trade_lab.domain.events import TopOfBookEvent, TradeEvent, TradeSide
from trade_lab.domain.market_context import (
    DEFAULT_RETENTION_MINUTES,
    MarketContextBuffer,
)
from trade_lab.services.runtime import ApplicationRuntime

BASE = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)


def _ts(seconds: float) -> datetime:
    return BASE + timedelta(seconds=seconds)


def _trade_event(seconds: float, *, price_ticks: int = 68_000) -> TradeEvent:
    return TradeEvent(
        event_ts_utc=_ts(seconds),
        receive_ts_utc=None,
        instrument_id=1,
        requested_symbol="NQ.c.0",
        raw_symbol="NQM6",
        price_ticks=price_ticks,
        size=1,
        source_schema="trades",
    )


def _quote_event(seconds: float) -> TopOfBookEvent:
    return TopOfBookEvent(
        event_ts_utc=_ts(seconds),
        instrument_id=1,
        bid_price_ticks=67_999,
        bid_size=1,
        ask_price_ticks=68_001,
        ask_size=1,
        source_schema="mbp-10",
    )


def _runtime() -> ApplicationRuntime:
    return ApplicationRuntime(
        requested_symbol="NQ.c.0",
        tick_timeframes=(2,),
        observation_duration_seconds=300,
    )


def test_default_retention_is_at_least_thirty_five_minutes() -> None:
    buffer = MarketContextBuffer()
    assert buffer.retention >= timedelta(minutes=DEFAULT_RETENTION_MINUTES)
    assert buffer.retention >= timedelta(minutes=30)


def test_default_retention_covers_approach_plus_interaction_window() -> None:
    # Inference fires at observation completion (touch + 5m interaction), yet approach
    # features reach back to touch - 30m. Retention must therefore be >= 45m so the
    # start of the approach window is still buffered when the prediction runs.
    buffer = MarketContextBuffer()
    assert buffer.retention >= timedelta(minutes=45)
    assert DEFAULT_RETENTION_MINUTES >= 45


def test_approach_window_start_survives_until_observation_completion() -> None:
    # Timeline (seconds from BASE): touch at t=1800 (30m in), approach starts at t=0,
    # observation completes at touch + interaction (1800 + 300 = 2100). At completion
    # time the newest event drives eviction; the t=0 approach-start records must remain.
    buffer = MarketContextBuffer()
    approach_window = timedelta(minutes=30)
    interaction_window = timedelta(minutes=5)
    touch = approach_window  # 1800s after the approach start at t=0

    # Approach-start records at the very edge of the pre-touch window.
    buffer.append_trade(_ts(0), 68_000, 5, TradeSide.BUY)
    buffer.append_quote(_ts(0), 67_999, 68_001)
    # Some activity through the approach + interaction window.
    buffer.append_trade(_ts(touch.total_seconds()), 68_010, 1, TradeSide.SELL)

    completion = touch + interaction_window
    # The final event at completion time drives eviction.
    buffer.append_quote(_ts(completion.total_seconds()), 67_998, 68_002)

    approach_start = _ts(0)
    approach_end = _ts(touch.total_seconds())
    surviving_trades = buffer.trades_in_window(approach_start, approach_end)
    surviving_quotes = buffer.quotes_in_window(approach_start, approach_end)

    assert any(trade.event_ts_utc == _ts(0) for trade in surviving_trades)
    assert any(quote.event_ts_utc == _ts(0) for quote in surviving_quotes)


def test_runtime_buffer_retains_approach_start_at_observation_completion() -> None:
    # End-to-end through the runtime hot path: a quote/trade at the approach-window
    # start is still present after a trade lands at the observation-completion time.
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0",
        tick_timeframes=(2,),
        observation_duration_seconds=300,
    )
    runtime.process_market_event(_quote_event(0))
    runtime.process_market_event(_trade_event(0, price_ticks=68_000))
    # 35 minutes later (30m approach + 5m interaction) a final trade arrives.
    runtime.process_market_event(_trade_event(35 * 60, price_ticks=68_001))

    buffer = runtime.market_context
    assert buffer.trades_in_window(_ts(0), _ts(1))  # approach-start trade survived
    assert buffer.quotes_in_window(_ts(0), _ts(1))  # approach-start quote survived


def test_invalid_retention_and_max_elements_are_rejected() -> None:
    with pytest.raises(ValueError, match="retention"):
        MarketContextBuffer(retention=timedelta(0))
    with pytest.raises(ValueError, match="max_elements"):
        MarketContextBuffer(max_elements=0)


def test_naive_timestamp_is_rejected() -> None:
    buffer = MarketContextBuffer()
    with pytest.raises(ValueError, match="timezone-aware"):
        buffer.append_trade(datetime(2026, 1, 5, 0, 0), 68_000, 1, TradeSide.BUY)


def test_time_eviction_drops_records_older_than_retention() -> None:
    buffer = MarketContextBuffer(retention=timedelta(minutes=10))
    buffer.append_trade(_ts(0), 68_000, 1, TradeSide.BUY)
    buffer.append_quote(_ts(0), 67_999, 68_001)
    # Advance newest timestamp 11 minutes past the first record (> 10m retention).
    buffer.append_trade(_ts(660), 68_010, 2, TradeSide.SELL)

    assert buffer.trade_count == 1
    assert buffer.quote_count == 0
    assert buffer.trades_in_window(_ts(0), _ts(700))[0].price_ticks == 68_010


def test_count_bound_evicts_oldest_combined_elements() -> None:
    buffer = MarketContextBuffer(retention=timedelta(hours=24), max_elements=3)
    buffer.append_trade(_ts(0), 68_000, 1, TradeSide.BUY)
    buffer.append_quote(_ts(1), 67_999, 68_001)
    buffer.append_trade(_ts(2), 68_002, 1, TradeSide.BUY)
    buffer.append_trade(_ts(3), 68_003, 1, TradeSide.BUY)

    assert buffer.trade_count + buffer.quote_count == 3
    # Oldest record (the t=0 trade) was evicted; the t=1 quote survived.
    remaining = buffer.trades_in_window(_ts(0), _ts(10))
    assert {trade.price_ticks for trade in remaining} == {68_002, 68_003}
    assert buffer.quote_count == 1


def test_window_slices_are_end_exclusive() -> None:
    buffer = MarketContextBuffer()
    for i in range(5):
        buffer.append_trade(_ts(i), 68_000 + i, 1, TradeSide.BUY)
        buffer.append_quote(_ts(i), 67_999 + i, 68_001 + i)

    trades = buffer.trades_in_window(_ts(1), _ts(3))
    quotes = buffer.quotes_in_window(_ts(1), _ts(3))

    assert [trade.event_ts_utc for trade in trades] == [_ts(1), _ts(2)]
    assert [quote.event_ts_utc for quote in quotes] == [_ts(1), _ts(2)]


def test_mid_price_uses_latest_quote() -> None:
    buffer = MarketContextBuffer()
    assert buffer.latest_mid_price_ticks() is None
    buffer.append_quote(_ts(0), 67_998, 68_002)
    buffer.append_quote(_ts(1), 67_999, 68_001)

    assert buffer.latest_mid_price_ticks() == pytest.approx(68_000.0)


def test_one_sided_quote_is_dropped() -> None:
    buffer = MarketContextBuffer()
    buffer.append_quote(_ts(0), 67_999, None)
    buffer.append_quote(_ts(1), None, 68_001)

    assert buffer.quote_count == 0
    assert buffer.latest_mid_price_ticks() is None


def test_reset_clears_all_context() -> None:
    buffer = MarketContextBuffer()
    buffer.append_trade(_ts(0), 68_000, 1, TradeSide.BUY)
    buffer.append_quote(_ts(0), 67_999, 68_001)

    buffer.reset()

    assert buffer.trade_count == 0
    assert buffer.quote_count == 0
    assert buffer.latest_mid_price_ticks() is None


def test_runtime_routes_only_trades_and_top_of_book_into_buffer() -> None:
    runtime = _runtime()
    # An mbp-10-style stream: depth is projected to TOB + trades before it reaches the
    # runtime, so the buffer can only ever see best bid/ask and trades.
    runtime.process_market_event(_quote_event(0))
    runtime.process_market_event(_trade_event(1, price_ticks=68_000))
    runtime.process_market_event(_quote_event(2))
    runtime.process_market_event(_trade_event(3, price_ticks=68_001))

    buffer = runtime.market_context
    assert buffer.trade_count == 2
    assert buffer.quote_count == 2
    # Structural L1/L0 floor: retained records expose no depth-capable fields.
    sample_trade = buffer.trades_in_window(_ts(0), _ts(10))[0]
    sample_quote = buffer.quotes_in_window(_ts(0), _ts(10))[0]
    trade_fields = set(sample_trade.__slots__)
    quote_fields = set(sample_quote.__slots__)
    assert trade_fields == {"event_ts_utc", "price_ticks", "size", "side"}
    assert quote_fields == {"event_ts_utc", "bid_price_ticks", "ask_price_ticks"}
    assert not any("level" in name or "depth" in name for name in trade_fields | quote_fields)


def test_runtime_reset_clears_market_context_buffer() -> None:
    runtime = _runtime()
    runtime.process_market_event(_trade_event(0))
    runtime.process_market_event(_quote_event(1))
    assert runtime.market_context.trade_count == 1

    runtime.reset()

    assert runtime.market_context.trade_count == 0
    assert runtime.market_context.quote_count == 0


def test_quote_routing_does_not_change_existing_runtime_outputs() -> None:
    """Appending quotes to the buffer must not alter any RuntimeUpdate output."""

    runtime = _runtime()
    quote_update = runtime.process_market_event(_quote_event(0))

    # TopOfBook still updates feed status only — no bars/touches/observations.
    assert quote_update.current_bars == ()
    assert quote_update.closed_bars == ()
    assert quote_update.touches == ()
    assert quote_update.observations == ()
    assert quote_update.feed_status is not None
    assert quote_update.feed_status.schema == "mbp-10"

    first = runtime.process_market_event(_trade_event(1, price_ticks=68_000))
    second = runtime.process_market_event(_trade_event(2, price_ticks=68_001))

    # Bar behaviour is unchanged by the buffer side effect.
    assert len(first.current_bars) == 1
    assert len(second.closed_bars) == 1
    assert runtime.snapshot().recent_closed_bars == second.closed_bars
