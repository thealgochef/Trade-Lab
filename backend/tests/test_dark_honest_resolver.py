"""D1a — the DARK SC streaming honest resolver seat in ApplicationRuntime.

The resolver runs alongside the legacy OutcomeTracker at the same lifecycle points
(activation / hot-swap / reset), registers every prediction the tracker registers
(off the TOUCH anchors carried on the observation chain), advances from the same
closed-bars hook, and accumulates into a parallel dark ring consumed only by the
gate-B characterization harness — never by any RuntimeUpdate/snapshot/DTO surface.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType

import pytest
from strategy_core import StreamDrop, StreamResolution, load_strategy_contract

from trade_lab.domain.events import TradeEvent, TradeSide
from trade_lab.domain.levels import LevelDirection, LevelKind, TouchEvent
from trade_lab.domain.sessions import SessionName
from trade_lab.services.inference.inference_engine import Prediction
from trade_lab.services.runtime import ApplicationRuntime, RuntimeSnapshot, RuntimeUpdate

_FIXTURE_STRATEGY = Path(__file__).parent / "fixtures" / "strategy.json"
# 14:00 UTC = 10:00 ET on a weekday: inside NY, decision (+5m) well before flatten.
_TOUCH_TS = datetime(2026, 1, 5, 14, 0, tzinfo=UTC)
_LEVEL_TICKS = 68_000


class _FakeEngine:
    """The minimal engine surface ApplicationRuntime consumes (duck-typed)."""

    def __init__(self, contract) -> None:
        self.active_contract = contract
        self.has_active_model = True
        self._counter = 0

    def active(self):  # model_status() probe; not under test here.
        return None

    def predict_for_observation(self, observation, market_context) -> Prediction:
        self._counter += 1
        return Prediction(
            prediction_id=f"pred-{self._counter}",
            touch_id=observation.originating_touch_id,
            observation_id=observation.observation_id,
            event_ts_utc=observation.scheduled_end_ts_utc,
            predicted_class="tradeable_reversal",
            probabilities=MappingProxyType({"tradeable_reversal": 1.0}),
            feature_values=MappingProxyType({}),
            level_kind=observation.level_kind.value,
            level_price_ticks=observation.level_price_ticks,
            direction="long",
            session="ny",
            is_eligible=True,
            model_id="m-1",
            contract_id="NQ_test",
            nan_count=0,
        )


def _runtime(*, engine=None, tick_timeframes=(147,), observation_duration_seconds=300):
    return ApplicationRuntime(
        requested_symbol="NQ.c.0",
        tick_timeframes=tick_timeframes,
        observation_duration_seconds=observation_duration_seconds,
        inference_engine=engine,
    )


def _trade(ts: datetime, price_ticks: int) -> TradeEvent:
    return TradeEvent(
        event_ts_utc=ts,
        receive_ts_utc=None,
        instrument_id=1,
        requested_symbol="NQ.c.0",
        raw_symbol=None,
        price_ticks=price_ticks,
        size=1,
        side=TradeSide.UNKNOWN,
        source_schema="trades",
    )


def _touch(ts: datetime = _TOUCH_TS) -> TouchEvent:
    return TouchEvent(
        touch_id="touch-1",
        event_ts_utc=ts,
        trading_day=ts.date(),
        session=SessionName.NY,
        level_kind=LevelKind.NY_LOW,
        level_price_ticks=_LEVEL_TICKS,
        trade_price_ticks=_LEVEL_TICKS,
        requested_symbol="NQ.c.0",
        raw_symbol=None,
        instrument_id=1,
        direction=LevelDirection.LONG,
    )


def _drive_registration(runtime) -> None:
    """Seed the ring, start an observation from a touch, and expire it into a prediction."""

    runtime.observations.start_from_touch(_touch())
    # Prints before the decision instant feed the SC trade ring the resolver queries.
    runtime.process_market_event(_trade(_TOUCH_TS + timedelta(seconds=60), _LEVEL_TICKS + 4))
    # The first trade at/after scheduled_end (touch + 300s) expires the observation,
    # produces the prediction, and registers BOTH the tracker and the dark resolver.
    runtime.process_market_event(_trade(_TOUCH_TS + timedelta(seconds=301), _LEVEL_TICKS + 6))


def test_registration_mirrors_tracker_and_resolves_dark() -> None:
    engine = _FakeEngine(load_strategy_contract(_FIXTURE_STRATEGY))
    runtime = _runtime(engine=engine)
    _drive_registration(runtime)

    assert runtime._outcome_tracker is not None and runtime._outcome_tracker.open_count == 1
    assert runtime._honest_resolver is not None and runtime._honest_resolver.open_count == 1
    assert runtime.dark_outcomes == ()  # live setup, no registration-time drop

    # 147 trades spiking +16 points close a 147t bar inside the forward window -> TP
    # on both paths; the dark resolution carries the ring entry (the +4-tick print at
    # the decision instant), not the level price.
    ts = _TOUCH_TS + timedelta(seconds=310)
    for i in range(147):
        runtime.process_market_event(_trade(ts + timedelta(seconds=i), _LEVEL_TICKS + 64))
    dark = runtime.dark_outcomes
    assert len(dark) == 1
    resolution = dark[0]
    assert isinstance(resolution, StreamResolution)
    assert resolution.key == "pred-1"
    assert resolution.result.label == "tradeable_reversal"
    assert resolution.entry_price == (_LEVEL_TICKS + 4) * 0.25
    assert runtime._honest_resolver.open_count == 0
    # The legacy tracker resolved the same prediction on its own (level-price) path.
    assert len(runtime.outcomes) == 1


def test_registration_time_drop_lands_in_dark_ring() -> None:
    engine = _FakeEngine(load_strategy_contract(_FIXTURE_STRATEGY))
    runtime = _runtime(engine=engine)
    # 21:36 UTC = 16:36 ET; decision (+5m) = 16:41 ET >= 16:40 flatten -> drop.
    late = datetime(2026, 1, 5, 21, 36, tzinfo=UTC)
    runtime.observations.start_from_touch(_touch(ts=late))
    runtime.process_market_event(_trade(late + timedelta(seconds=60), _LEVEL_TICKS))
    runtime.process_market_event(_trade(late + timedelta(seconds=301), _LEVEL_TICKS))

    dark = runtime.dark_outcomes
    assert len(dark) == 1
    assert isinstance(dark[0], StreamDrop) and dark[0].reason == "flatten"
    assert runtime._honest_resolver.open_count == 0
    # The legacy tracker has no flatten concept: it keeps the prediction open.
    assert runtime._outcome_tracker.open_count == 1


def test_reset_and_hot_swap_clear_dark_state() -> None:
    engine = _FakeEngine(load_strategy_contract(_FIXTURE_STRATEGY))
    runtime = _runtime(engine=engine)
    _drive_registration(runtime)
    assert runtime._honest_resolver.open_count == 1

    runtime.reset()
    assert runtime._honest_resolver.open_count == 0
    assert runtime.dark_outcomes == ()

    _drive_registration(runtime)
    assert runtime._honest_resolver.open_count == 1
    runtime.set_inference_engine(engine)  # hot-swap rebuilds the resolver
    assert runtime._honest_resolver.open_count == 0
    assert runtime.dark_outcomes == ()


def test_dark_ring_is_isolated_from_update_snapshot_and_outcomes() -> None:
    engine = _FakeEngine(load_strategy_contract(_FIXTURE_STRATEGY))
    runtime = _runtime(engine=engine)
    _drive_registration(runtime)
    ts = _TOUCH_TS + timedelta(seconds=310)
    update = None
    for i in range(147):
        update = runtime.process_market_event(_trade(ts + timedelta(seconds=i), _LEVEL_TICKS + 64))
    assert len(runtime.dark_outcomes) == 1
    # No dark field exists on any emitted surface, and the dark resolution never
    # leaks into the legacy outcome stream (which carries TL Outcome objects only).
    assert "dark_outcomes" not in {f for f in RuntimeUpdate.__dataclass_fields__}
    assert "dark_outcomes" not in {f for f in RuntimeSnapshot.__dataclass_fields__}
    assert all(type(o).__name__ == "Outcome" for o in update.outcomes)
    assert all(type(o).__name__ == "Outcome" for o in runtime.snapshot().outcomes)


def test_offset_mismatch_warns_once_at_activation(caplog: pytest.LogCaptureFixture) -> None:
    engine = _FakeEngine(load_strategy_contract(_FIXTURE_STRATEGY))
    with caplog.at_level(logging.WARNING, logger="trade_lab.services.runtime"):
        runtime = _runtime(engine=engine, observation_duration_seconds=600)
    hits = [r for r in caplog.records if "decision_offset_minutes" in r.getMessage()]
    assert len(hits) == 1  # once at activation, not per prediction
    assert runtime._honest_resolver is not None


def test_forward_timeframe_not_configured_fails_loud_at_activation() -> None:
    engine = _FakeEngine(load_strategy_contract(_FIXTURE_STRATEGY))
    with pytest.raises(ValueError, match="forward timeframe"):
        _runtime(engine=engine, tick_timeframes=(2,))


def test_no_engine_means_no_resolver_and_empty_dark_ring() -> None:
    runtime = _runtime(engine=None)
    assert runtime._honest_resolver is None
    runtime.observations.start_from_touch(_touch())
    runtime.process_market_event(_trade(_TOUCH_TS + timedelta(seconds=301), _LEVEL_TICKS))
    assert runtime.dark_outcomes == ()
