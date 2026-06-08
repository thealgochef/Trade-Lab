import inspect
from datetime import UTC, datetime

from strategy_core.runtime.state import StrategyRuntime
from strategy_core.types import Trade as CoreTrade

from trade_lab.domain.events import TradeEvent
from trade_lab.services import runtime as runtime_module
from trade_lab.services.runtime import ApplicationRuntime
from trade_lab.services.strategy_core_service import StrategyCoreService


def _core_trade(ts, price_ticks: int) -> CoreTrade:
    return CoreTrade(event_ts_utc=ts, price_ticks=price_ticks, size=1, side="B")


def _trade_lab_trade(ts, price_ticks: int) -> TradeEvent:
    return TradeEvent(
        event_ts_utc=ts,
        receive_ts_utc=None,
        instrument_id=1,
        requested_symbol="NQ.c.0",
        raw_symbol="NQM6",
        price_ticks=price_ticks,
        size=1,
        source_schema="trades",
    )


def test_trade_lab_runtime_touch_matches_strategy_core_direct_runtime() -> None:
    core = StrategyRuntime(
        requested_symbol="NQ.c.0",
        timeframes=(2,),
        decision_timeframe=2,
    )
    trade_lab = ApplicationRuntime(
        requested_symbol="NQ.c.0",
        tick_timeframes=(2,),
        observation_duration_seconds=300,
    )
    # Asia sets a high at 68_020 (5 pts above its 68_000 low, so the two stay in
    # separate zones). The London return bar straddles 68_020 with a wide range
    # (68_007..68_033) so London's own session extremes land >3 pts from 68_020 and
    # do NOT merge into the asia_high zone -- otherwise the engine-v3 availability
    # guard would inherit London's later close instant and gate the touch.
    events = (
        (datetime(2026, 1, 5, 0, 0, tzinfo=UTC), 68_000),
        (datetime(2026, 1, 5, 0, 0, 1, tzinfo=UTC), 68_020),
        (datetime(2026, 1, 5, 8, 0, tzinfo=UTC), 68_007),
        (datetime(2026, 1, 5, 8, 0, 1, tzinfo=UTC), 68_033),
    )

    core_update = None
    trade_lab_update = None
    for ts, price_ticks in events:
        core_update = core.process_event(_core_trade(ts, price_ticks))
        trade_lab_update = trade_lab.process_market_event(_trade_lab_trade(ts, price_ticks))

    assert core_update is not None
    assert trade_lab_update is not None
    assert len(core_update.touches) == 1
    assert len(trade_lab_update.touches) == 1
    assert core_update.touches[0].level_type == trade_lab_update.touches[0].level_kind.value
    assert trade_lab_update.touches[0].level_price_ticks == 68_020
    assert len(trade_lab_update.observations) == 1


def test_runtime_path_uses_strategy_core_service_not_legacy_strategy_engines() -> None:
    runtime_source = inspect.getsource(runtime_module)
    service_source = inspect.getsource(StrategyCoreService)

    assert "StrategyCoreService" in runtime_source
    assert "CandleEngine" not in runtime_source
    assert "SessionLevelEngine" not in runtime_source
    assert "SessionClassifier" not in runtime_source
    assert "CandleEngine" not in service_source
    assert "SessionLevelEngine" not in service_source
    assert "SessionClassifier" not in service_source
