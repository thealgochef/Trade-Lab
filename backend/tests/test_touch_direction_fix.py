"""Regression coverage for the audit follow-up fixes in the Strategy-Core adapter.

audit #NN-1 (primary): the authoritative ``Touch.direction`` Strategy-Core resolves on
the MERGED ZONE side must be carried through the touch -> observation path, NOT
re-derived downstream from ``level_kind`` (= ``zone.names[0]``, the lowest-priced
constituent), which inverts direction for a mixed-side merged zone.

Also covers audit #5 (decision bar pinned to min display timeframe) and audit #7
(trade side encoded with canonical databento codes B/A/N).
"""

from datetime import UTC, date, datetime

from strategy_core.types import Direction as CoreDirection
from strategy_core.types import Touch as CoreTouch

from trade_lab.domain.events import TradeEvent, TradeSide
from trade_lab.domain.levels import LevelDirection, LevelKind
from trade_lab.domain.observations import ObservationEngine
from trade_lab.services.inference.inference_engine import _level_side
from trade_lab.services.strategy_core_service import (
    StrategyCoreService,
    _trade_to_core,
)


def _mixed_side_touch() -> CoreTouch:
    """A merged-zone touch whose authoritative direction contradicts ``names[0]``.

    ``level_type='asia_high'`` is a HIGH-side level, so the old level_kind-derived path
    (high -> short) would say SHORT. The merged zone's side resolved to LOW, so
    Strategy-Core's authoritative ``direction`` is LONG. This is exactly the inversion
    the fix prevents.
    """

    return CoreTouch(
        bar_ts_utc=datetime(2026, 1, 6, 14, tzinfo=UTC),
        representative_price=17000.0,
        direction=CoreDirection.LONG,
        level_type="asia_high",
        trading_day=date(2026, 1, 6),
    )


def test_touch_event_carries_authoritative_direction_not_level_kind_derived() -> None:
    service = StrategyCoreService(requested_symbol="NQ.c.0", tick_timeframes=(147,))
    touch_event = service._touch_to_trade_lab(_mixed_side_touch(), "ny")

    # level_kind is the high-side constituent: the buggy path would derive SHORT.
    assert touch_event.level_kind == LevelKind.ASIA_HIGH
    assert _level_side(touch_event.level_kind.value) == "high"
    # The carried authoritative direction is LONG, NOT the level_kind-derived SHORT.
    assert touch_event.direction == LevelDirection.LONG
    assert touch_event.direction != LevelDirection.SHORT


def test_observation_carries_authoritative_direction_from_touch() -> None:
    service = StrategyCoreService(requested_symbol="NQ.c.0", tick_timeframes=(147,))
    touch_event = service._touch_to_trade_lab(_mixed_side_touch(), "ny")

    observation = ObservationEngine().start_from_touch(touch_event)

    # Observation (what inference consumes) preserves the authoritative direction.
    assert observation.direction == LevelDirection.LONG
    assert observation.direction != LevelDirection.SHORT


def test_simple_high_side_touch_still_maps_to_short() -> None:
    # Control: a non-merged high-side touch (direction agrees with level_kind) is SHORT.
    service = StrategyCoreService(requested_symbol="NQ.c.0", tick_timeframes=(147,))
    touch = CoreTouch(
        bar_ts_utc=datetime(2026, 1, 6, 14, tzinfo=UTC),
        representative_price=17050.0,
        direction=CoreDirection.SHORT,
        level_type="asia_high",
        trading_day=date(2026, 1, 6),
    )

    touch_event = service._touch_to_trade_lab(touch, "ny")

    assert touch_event.direction == LevelDirection.SHORT


def test_decision_timeframe_pinned_to_min_display_timeframe() -> None:
    # audit #5: the decision bar is pinned explicitly to the smallest display timeframe
    # so a future smaller display timeframe cannot silently shrink it.
    service = StrategyCoreService(requested_symbol="NQ.c.0", tick_timeframes=(147, 987, 2000))
    assert service._runtime.decision_timeframe == 147


def test_trade_side_uses_canonical_databento_codes() -> None:
    # audit #7: B (buy) / A (sell) / N (none/unknown), not first-letter slicing (S/U).
    def _core_side(side: TradeSide) -> str | None:
        event = TradeEvent(
            datetime(2026, 1, 6, 14, tzinfo=UTC),
            None,
            1,
            "NQ.c.0",
            "NQM6",
            68000,
            1,
            side,
        )
        return _trade_to_core(event).side

    assert _core_side(TradeSide.BUY) == "B"
    assert _core_side(TradeSide.SELL) == "A"
    assert _core_side(TradeSide.UNKNOWN) == "N"
