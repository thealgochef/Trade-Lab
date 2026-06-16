from datetime import UTC, datetime

from trade_lab.domain.events import TopOfBookEvent, TradeEvent
from trade_lab.domain.levels import LevelKind
from trade_lab.services.strategy_core_service import StrategyCoreService


def test_strategy_core_service_creates_path_free_snapshot() -> None:
    service = StrategyCoreService(requested_symbol="NQ.c.0", tick_timeframes=(2,))
    snapshot = service.snapshot()
    assert snapshot.current_bars == ()
    assert snapshot.display_levels == ()
    assert "/" not in str(snapshot)
    assert "api_key" not in str(snapshot).lower()


def test_strategy_core_service_maps_trade_bars_and_quote_context_to_trade_lab_types() -> None:
    service = StrategyCoreService(requested_symbol="NQ.c.0", tick_timeframes=(2,))
    quote_update = service.process_market_event(
        TopOfBookEvent(
            datetime(2026, 1, 6, 14, tzinfo=UTC),
            1,
            68000,
            1,
            68001,
            1,
            "mbp-1",
        )
    )
    assert quote_update.closed_bars == ()
    first = service.process_market_event(
        TradeEvent(
            datetime(2026, 1, 6, 14, tzinfo=UTC),
            None,
            1,
            "NQ.c.0",
            "NQM6",
            68000,
            1,
        )
    )
    second = service.process_market_event(
        TradeEvent(
            datetime(2026, 1, 6, 14, 1, tzinfo=UTC),
            None,
            1,
            "NQ.c.0",
            "NQM6",
            68004,
            1,
        )
    )
    assert first.current_bars[0].timeframe_ticks == 2
    assert second.closed_bars[0].bar_id == "2t:2026-01-06:0"
    assert service.snapshot().session == "ny"


def test_strategy_core_service_quote_does_not_build_a_snapshot() -> None:
    # RC1: snapshot() is the dominant per-event cost on the live MBP-1 hot path (it
    # rebuilds the full level set twice) and is unused for quotes, whose update carries
    # empty levels/touches. _map_update must skip it for quotes — output unchanged.
    service = StrategyCoreService(requested_symbol="NQ.c.0", tick_timeframes=(2,))
    calls = 0
    original_snapshot = service._runtime.snapshot

    def counting_snapshot():
        nonlocal calls
        calls += 1
        return original_snapshot()

    service._runtime.snapshot = counting_snapshot  # type: ignore[method-assign]
    quote_update = service.process_market_event(
        TopOfBookEvent(datetime(2026, 1, 6, 14, tzinfo=UTC), 1, 68000, 1, 68001, 1, "mbp-1")
    )
    assert calls == 0
    assert quote_update.display_levels == ()
    assert quote_update.touches == ()
    assert quote_update.closed_bars == ()


def test_strategy_core_service_loads_prior_day_summary_and_maps_levels() -> None:
    service = StrategyCoreService(requested_symbol="NQ.c.0", tick_timeframes=(2,))
    service.load_prior_day_summary(datetime(2026, 1, 5, tzinfo=UTC).date(), 68100, 67900)
    update = service.process_market_event(
        TradeEvent(
            datetime(2026, 1, 6, 14, tzinfo=UTC),
            None,
            1,
            "NQ.c.0",
            "NQM6",
            68000,
            1,
        )
    )
    kinds = {level.kind for level in update.display_levels}
    assert {LevelKind.PDH, LevelKind.PDL} <= kinds
