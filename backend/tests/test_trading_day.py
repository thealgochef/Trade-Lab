"""W2 P1: ET trading-day clock helpers (18:00 America/New_York roll)."""

from datetime import UTC, date, datetime

from trade_lab.domain.trading_day import (
    most_recent_session_open_utc,
    prior_trading_day,
    trading_day_bounds_utc,
    trading_day_for,
    trading_day_start_utc,
)

# 2026-06-10 is a Wednesday; EDT (UTC-4) applies on every date used here.


def test_trading_day_rolls_at_1800_et() -> None:
    before_roll = datetime(2026, 6, 10, 21, 59, tzinfo=UTC)  # 17:59 ET Wed
    at_roll = datetime(2026, 6, 10, 22, 0, tzinfo=UTC)  # 18:00 ET Wed
    assert trading_day_for(before_roll) == date(2026, 6, 10)
    assert trading_day_for(at_roll) == date(2026, 6, 11)


def test_trading_day_start_is_prior_civil_day_1800_et() -> None:
    assert trading_day_start_utc(date(2026, 6, 11)) == datetime(2026, 6, 10, 22, 0, tzinfo=UTC)


def test_most_recent_session_open_spans_the_evening_and_the_next_morning() -> None:
    evening = datetime(2026, 6, 10, 23, 30, tzinfo=UTC)  # 19:30 ET Wed -> Thu trading day
    morning = datetime(2026, 6, 11, 14, 30, tzinfo=UTC)  # 10:30 ET Thu -> same trading day
    expected_open = datetime(2026, 6, 10, 22, 0, tzinfo=UTC)
    assert most_recent_session_open_utc(evening) == expected_open
    assert most_recent_session_open_utc(morning) == expected_open


def test_prior_trading_day_skips_the_weekend() -> None:
    assert prior_trading_day(date(2026, 6, 8)) == date(2026, 6, 5)  # Monday -> Friday
    assert prior_trading_day(date(2026, 6, 9)) == date(2026, 6, 8)  # Tuesday -> Monday


def test_trading_day_bounds_are_half_open_1800_to_1800_et() -> None:
    start, end = trading_day_bounds_utc(date(2026, 6, 8))  # Monday
    assert start == datetime(2026, 6, 7, 22, 0, tzinfo=UTC)  # Sunday 18:00 ET
    assert end == datetime(2026, 6, 8, 22, 0, tzinfo=UTC)  # Monday 18:00 ET
