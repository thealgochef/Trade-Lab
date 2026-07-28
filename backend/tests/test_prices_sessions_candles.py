from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType
from zoneinfo import ZoneInfo

import pytest

from trade_lab.domain.events import (
    DailyStatisticEvent,
    MarketStatus,
    MarketStatusEvent,
    TopOfBookEvent,
    TradeEvent,
)
from trade_lab.domain.prices import PriceError, price_to_ticks, ticks_to_price


def trade(ts: datetime, price_ticks: int = 100, size: int = 1) -> TradeEvent:
    return TradeEvent(ts, None, 1, "NQ.c.0", "NQM6", price_ticks, size)


@pytest.mark.parametrize(
    ("price", "ticks"),
    [
        (Decimal("0"), 0),
        (Decimal("0.25"), 1),
        (Decimal("17000.00"), 68_000),
        (Decimal("17000.25"), 68_001),
        ("17000.50", 68_002),
        (17001, 68_004),
        (Decimal("-0.25"), -1),
    ],
)
def test_valid_nq_prices_convert_exactly_to_integer_ticks_and_back(
    price: Decimal | int | str, ticks: int
) -> None:
    assert price_to_ticks(price) == ticks
    assert price_to_ticks(ticks_to_price(ticks)) == ticks


@pytest.mark.parametrize("price", [Decimal("17000.10"), "17000.125", Decimal("0.01")])
def test_non_tick_aligned_prices_are_rejected_not_rounded(price: Decimal | str) -> None:
    with pytest.raises(PriceError, match="not divisible"):
        price_to_ticks(price)


@pytest.mark.parametrize("price", [17000.25, 0.25, 17000.10])
def test_float_prices_are_rejected_to_avoid_binary_precision_surprises(price: float) -> None:
    with pytest.raises(PriceError, match="floats are not accepted"):
        price_to_ticks(price)  # type: ignore[arg-type]


def test_trade_event_is_immutable_slotted_and_carries_integer_ticks() -> None:
    event = TradeEvent(
        datetime(2026, 1, 5, 0, 0, tzinfo=UTC),
        datetime(2026, 1, 5, 0, 0, 1, tzinfo=UTC),
        1,
        "NQ.c.0",
        "NQM6",
        68_001,
        3,
        metadata={"source": "unit"},
    )

    assert event.price_ticks == 68_001
    assert event.price == Decimal("17000.25")
    assert not hasattr(event, "__dict__")
    assert isinstance(event.metadata, MappingProxyType)
    with pytest.raises(FrozenInstanceError):
        event.price_ticks = 68_002  # type: ignore[misc]
    with pytest.raises(TypeError):
        event.metadata["source"] = "changed"  # type: ignore[index]


def test_event_timestamps_must_be_timezone_aware_and_are_normalized_to_utc() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        trade(datetime(2026, 1, 5, 0, 0), 100)

    central_timestamp = datetime(2026, 1, 4, 18, 0, tzinfo=ZoneInfo("America/Chicago"))
    event = trade(central_timestamp, 100)
    assert event.event_ts_utc == datetime(2026, 1, 5, 0, 0, tzinfo=UTC)


def test_trade_event_rejects_non_positive_sizes() -> None:
    with pytest.raises(ValueError, match="positive"):
        trade(datetime(2026, 1, 5, 0, 0, tzinfo=UTC), 100, 0)


@pytest.mark.parametrize("price_ticks", [100.0, True, "100"])
def test_trade_event_rejects_invalid_tick_field_types(price_ticks: object) -> None:
    with pytest.raises(ValueError, match="price_ticks"):
        trade(datetime(2026, 1, 5, 0, 0, tzinfo=UTC), price_ticks)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("bid_price_ticks", "ask_price_ticks"),
    [
        (99.0, 101),
        (99, False),
        ("99", 101),
    ],
)
def test_top_of_book_event_rejects_invalid_tick_field_types(
    bid_price_ticks: object, ask_price_ticks: object
) -> None:
    with pytest.raises(ValueError, match="price_ticks"):
        TopOfBookEvent(
            datetime(2026, 1, 5, 0, 0, tzinfo=UTC),
            1,
            bid_price_ticks,  # type: ignore[arg-type]
            1,
            ask_price_ticks,  # type: ignore[arg-type]
            2,
        )


@pytest.mark.parametrize("price_ticks", [100.0, True, "100"])
def test_daily_statistic_event_rejects_invalid_tick_field_types(price_ticks: object) -> None:
    with pytest.raises(ValueError, match="price_ticks"):
        DailyStatisticEvent(
            datetime(2026, 1, 5, 0, 0, tzinfo=UTC),
            1,
            "settlement",
            price_ticks,  # type: ignore[arg-type]
        )


def test_non_trade_events_do_not_look_like_tick_bar_inputs() -> None:
    quote = TopOfBookEvent(datetime(2026, 1, 5, 0, 0, tzinfo=UTC), 1, 99, 1, 101, 2)
    status = MarketStatusEvent(datetime(2026, 1, 5, 0, 0, tzinfo=UTC), 1, MarketStatus.OPEN)
    statistic = DailyStatisticEvent(datetime(2026, 1, 5, 0, 0, tzinfo=UTC), 1, "settlement", 100)

    for event in (quote, status, statistic):
        assert not isinstance(event, TradeEvent)
        assert not hasattr(event, "size")
