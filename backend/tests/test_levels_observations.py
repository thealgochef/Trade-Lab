from datetime import UTC, date, datetime, timedelta

import pytest

from trade_lab.domain.events import TradeEvent
from trade_lab.domain.levels import LevelKind, SessionLevelEngine
from trade_lab.domain.observations import ObservationEngine, ObservationStatus
from trade_lab.domain.sessions import SessionName


def trade(ts: datetime, price_ticks: int) -> TradeEvent:
    return TradeEvent(ts, None, 1, "NQ.c.0", "NQM6", price_ticks, 1)


def level_prices(update) -> dict[LevelKind, int]:
    return {level.kind: level.price_ticks for level in update.display_levels}


def test_partial_prior_day_high_and_low_do_not_roll_into_pdh_pdl() -> None:
    engine = SessionLevelEngine()
    engine.process_trade(trade(datetime(2026, 1, 5, 0, 0, tzinfo=UTC), 100))
    engine.process_trade(trade(datetime(2026, 1, 5, 8, 0, tzinfo=UTC), 90))
    engine.process_trade(trade(datetime(2026, 1, 5, 14, 0, tzinfo=UTC), 110))

    update = engine.process_trade(trade(datetime(2026, 1, 6, 0, 0, tzinfo=UTC), 105))

    prices = level_prices(update)
    assert LevelKind.PDH not in prices
    assert LevelKind.PDL not in prices
    assert level_prices(update)[LevelKind.ASIA_HIGH] == 105
    assert level_prices(update)[LevelKind.ASIA_LOW] == 105


def test_explicit_finalized_prior_day_rolls_into_pdh_pdl() -> None:
    engine = SessionLevelEngine()
    engine.process_trade(trade(datetime(2026, 1, 5, 0, 0, tzinfo=UTC), 100))
    engine.process_trade(trade(datetime(2026, 1, 5, 8, 0, tzinfo=UTC), 90))
    engine.process_trade(trade(datetime(2026, 1, 5, 14, 0, tzinfo=UTC), 110))
    engine.finalize_trading_day(date(2026, 1, 5))

    update = engine.process_trade(trade(datetime(2026, 1, 6, 0, 0, tzinfo=UTC), 105))

    assert level_prices(update)[LevelKind.PDH] == 110
    assert level_prices(update)[LevelKind.PDL] == 90


def test_loaded_prior_day_summary_rolls_into_pdh_pdl() -> None:
    engine = SessionLevelEngine()
    engine.load_prior_day_summary(date(2026, 1, 5), high_ticks=120, low_ticks=80)

    update = engine.process_trade(trade(datetime(2026, 1, 6, 0, 0, tzinfo=UTC), 105))

    assert level_prices(update)[LevelKind.PDH] == 120
    assert level_prices(update)[LevelKind.PDL] == 80


def test_finalized_friday_summary_rolls_into_monday_pdh_pdl_after_weekend() -> None:
    engine = SessionLevelEngine()
    engine.process_trade(trade(datetime(2026, 1, 9, 0, 0, tzinfo=UTC), 100))
    engine.process_trade(trade(datetime(2026, 1, 9, 8, 0, tzinfo=UTC), 90))
    engine.process_trade(trade(datetime(2026, 1, 9, 14, 0, tzinfo=UTC), 110))
    engine.finalize_trading_day(date(2026, 1, 9))

    update = engine.process_trade(trade(datetime(2026, 1, 12, 0, 0, tzinfo=UTC), 105))

    prices = level_prices(update)
    assert prices[LevelKind.PDH] == 110
    assert prices[LevelKind.PDL] == 90


def test_loaded_friday_summary_rolls_into_sunday_evening_monday_trading_day() -> None:
    engine = SessionLevelEngine()
    engine.load_prior_day_summary(date(2026, 1, 9), high_ticks=120, low_ticks=80)

    update = engine.process_trade(trade(datetime(2026, 1, 12, 0, 0, tzinfo=UTC), 105))

    prices = level_prices(update)
    assert prices[LevelKind.PDH] == 120
    assert prices[LevelKind.PDL] == 80
    assert all(level.trading_day == date(2026, 1, 12) for level in update.display_levels)


def test_developing_session_levels_update_live_and_are_display_only() -> None:
    engine = SessionLevelEngine()

    first = engine.process_trade(trade(datetime(2026, 1, 5, 0, 0, tzinfo=UTC), 100))
    second = engine.process_trade(trade(datetime(2026, 1, 5, 0, 1, tzinfo=UTC), 105))

    assert first.touches == ()
    assert second.touches == ()
    assert level_prices(second)[LevelKind.ASIA_HIGH] == 105
    assert level_prices(second)[LevelKind.ASIA_LOW] == 100
    assert all(level.is_developing for level in second.display_levels)
    assert all(not level.is_eligible for level in second.display_levels)


def test_completed_asia_levels_are_touch_eligible_in_london_once_per_session() -> None:
    engine = SessionLevelEngine()
    engine.process_trade(trade(datetime(2026, 1, 5, 0, 0, tzinfo=UTC), 100))
    engine.process_trade(trade(datetime(2026, 1, 5, 1, 0, tzinfo=UTC), 105))

    first_london = engine.process_trade(trade(datetime(2026, 1, 5, 8, 0, tzinfo=UTC), 105))
    assert len(first_london.touches) == 1
    assert first_london.touches[0].level_kind == LevelKind.ASIA_HIGH
    assert first_london.touches[0].session == SessionName.LONDON
    assert first_london.touches[0].sequence_in_session == 1

    second_london = engine.process_trade(trade(datetime(2026, 1, 5, 8, 1, tzinfo=UTC), 105))
    assert second_london.touches == ()


def test_exact_tick_equality_is_required_for_touches() -> None:
    engine = SessionLevelEngine()
    engine.process_trade(trade(datetime(2026, 1, 5, 0, 0, tzinfo=UTC), 100))
    engine.process_trade(trade(datetime(2026, 1, 5, 1, 0, tzinfo=UTC), 105))

    near_miss = engine.process_trade(trade(datetime(2026, 1, 5, 8, 0, tzinfo=UTC), 104))
    exact = engine.process_trade(trade(datetime(2026, 1, 5, 8, 1, tzinfo=UTC), 105))

    assert near_miss.touches == ()
    assert [touch.level_kind for touch in exact.touches] == [LevelKind.ASIA_HIGH]


def test_same_level_can_touch_once_in_each_later_session_without_cutoff() -> None:
    engine = SessionLevelEngine()
    engine.process_trade(trade(datetime(2026, 1, 5, 0, 0, tzinfo=UTC), 100))
    engine.process_trade(trade(datetime(2026, 1, 5, 1, 0, tzinfo=UTC), 105))

    london = engine.process_trade(trade(datetime(2026, 1, 5, 8, 0, tzinfo=UTC), 105))
    ny = engine.process_trade(trade(datetime(2026, 1, 5, 21, 59, tzinfo=UTC), 105))

    assert [touch.level_kind for touch in london.touches] == [LevelKind.ASIA_HIGH]
    assert LevelKind.ASIA_HIGH in [touch.level_kind for touch in ny.touches]
    assert ny.touches[0].session == SessionName.NY


def test_pdh_and_pdl_are_touch_eligible_in_all_open_sessions_on_new_day() -> None:
    engine = SessionLevelEngine()
    engine.process_trade(trade(datetime(2026, 1, 5, 0, 0, tzinfo=UTC), 100))
    engine.process_trade(trade(datetime(2026, 1, 5, 8, 0, tzinfo=UTC), 90))
    engine.process_trade(trade(datetime(2026, 1, 5, 14, 0, tzinfo=UTC), 110))
    engine.finalize_trading_day(date(2026, 1, 5))

    asia = engine.process_trade(trade(datetime(2026, 1, 6, 0, 0, tzinfo=UTC), 110))
    london = engine.process_trade(trade(datetime(2026, 1, 6, 8, 0, tzinfo=UTC), 90))
    ny = engine.process_trade(trade(datetime(2026, 1, 6, 14, 0, tzinfo=UTC), 110))

    assert [touch.level_kind for touch in asia.touches] == [LevelKind.PDH]
    assert [touch.level_kind for touch in london.touches] == [LevelKind.PDL]
    assert LevelKind.PDH in [touch.level_kind for touch in ny.touches]


def test_rollover_resets_session_ranges_and_touch_state_deterministically() -> None:
    engine = SessionLevelEngine()
    engine.process_trade(trade(datetime(2026, 1, 5, 0, 0, tzinfo=UTC), 100))
    engine.process_trade(trade(datetime(2026, 1, 5, 1, 0, tzinfo=UTC), 105))
    engine.process_trade(trade(datetime(2026, 1, 5, 8, 0, tzinfo=UTC), 105))
    engine.finalize_trading_day(date(2026, 1, 5))

    new_day = engine.process_trade(trade(datetime(2026, 1, 6, 0, 0, tzinfo=UTC), 101))

    prices = level_prices(new_day)
    assert prices[LevelKind.PDH] == 105
    assert prices[LevelKind.PDL] == 100
    assert prices[LevelKind.ASIA_HIGH] == 101
    assert prices[LevelKind.ASIA_LOW] == 101
    assert LevelKind.LONDON_HIGH not in prices
    assert new_day.touches == ()


def test_display_levels_are_not_eligible_during_closed_session() -> None:
    engine = SessionLevelEngine()
    engine.process_trade(trade(datetime(2026, 1, 5, 0, 0, tzinfo=UTC), 100))
    engine.process_trade(trade(datetime(2026, 1, 5, 8, 0, tzinfo=UTC), 105))

    update = engine.process_trade(trade(datetime(2026, 1, 5, 22, 0, tzinfo=UTC), 105))

    assert update.display_levels
    assert all(not level.is_eligible for level in update.display_levels)


def test_observation_starts_from_touch_with_default_five_minute_window() -> None:
    levels = SessionLevelEngine()
    levels.process_trade(trade(datetime(2026, 1, 5, 0, 0, tzinfo=UTC), 100))
    touch = levels.process_trade(trade(datetime(2026, 1, 5, 8, 0, tzinfo=UTC), 100)).touches[0]

    obs = ObservationEngine().start_from_touch(touch)

    assert obs.status == ObservationStatus.ACTIVE
    assert obs.originating_touch_id == touch.touch_id
    assert obs.scheduled_end_ts_utc == touch.event_ts_utc + timedelta(minutes=5)
    assert obs.level_kind == touch.level_kind
    assert obs.level_price_ticks == touch.level_price_ticks


def test_observation_duration_is_configurable_and_expiry_changes_status() -> None:
    levels = SessionLevelEngine()
    levels.process_trade(trade(datetime(2026, 1, 5, 0, 0, tzinfo=UTC), 100))
    touch = levels.process_trade(trade(datetime(2026, 1, 5, 8, 0, tzinfo=UTC), 100)).touches[0]
    observations = ObservationEngine(timedelta(seconds=30))
    obs = observations.start_from_touch(touch)

    assert observations.refresh(obs.scheduled_end_ts_utc - timedelta(microseconds=1)) == ()
    assert observations.active()[0].status == ObservationStatus.ACTIVE

    expired = observations.refresh(obs.scheduled_end_ts_utc)

    assert len(expired) == 1
    assert expired[0].observation_id == obs.observation_id
    assert expired[0].status == ObservationStatus.EXPIRED
    assert observations.active() == ()


def test_observation_engine_rejects_invalid_duration_and_naive_refresh_time() -> None:
    with pytest.raises(ValueError, match="positive"):
        ObservationEngine(timedelta(0))

    with pytest.raises(ValueError, match="timezone-aware"):
        ObservationEngine().refresh(datetime(2026, 1, 5, 0, 0))
