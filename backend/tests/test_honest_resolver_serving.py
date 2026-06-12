"""D1b — the SC streaming honest resolver IS the served outcome path.

The resolver (promoted from its D1a dark seat) registers every prediction off the
TOUCH anchors carried on the observation chain, advances from the closed-bars
hook, and its emissions are served through the resolution adapter: resolutions
become ``Outcome``s (honest entry, TL-side correctness, 0-based bars) on
``RuntimeUpdate.outcomes``/the snapshot; drops become ``DroppedPrediction``s on
the new ``dropped`` surfaces and the ``prediction.dropped`` WS frame. The legacy
level-anchored OutcomeTracker is deleted.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest
from strategy_core import load_strategy_contract

from trade_lab.domain.events import TradeEvent, TradeSide
from trade_lab.domain.levels import LevelDirection, LevelKind, TouchEvent
from trade_lab.domain.outcomes import DroppedPrediction, Outcome, ResolutionType
from trade_lab.domain.sessions import SessionName
from trade_lab.services.broadcaster import WebSocketBroadcaster
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

    def active(self):
        # W2 P2b: the runtime's rebind reads the ActiveModel-shaped probe
        # (contract for the resolver; section for retention — None keeps the
        # configured baseline, which is not under test here).
        return SimpleNamespace(
            contract=self.active_contract,
            section=None,
            model_id="m-1",
            validation_ok=True,
            validation_detail="fake",
        )

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
    # produces the prediction, and registers it with the resolver.
    runtime.process_market_event(_trade(_TOUCH_TS + timedelta(seconds=301), _LEVEL_TICKS + 6))


def _spike_to_resolution(runtime) -> RuntimeUpdate:
    """Close one 147t bar at +16 points inside the forward window (TP on the first bar).

    Returns the update that carried the resolution (the bar closes mid-spike once
    147 trades accumulate, not on the final trade).
    """

    ts = _TOUCH_TS + timedelta(seconds=310)
    carrying = RuntimeUpdate()
    for i in range(147):
        update = runtime.process_market_event(
            _trade(ts + timedelta(seconds=i), _LEVEL_TICKS + 64)
        )
        if update.outcomes:
            carrying = update
    return carrying


def test_resolution_is_served_with_honest_entry_and_zero_based_bars() -> None:
    engine = _FakeEngine(load_strategy_contract(_FIXTURE_STRATEGY))
    runtime = _runtime(engine=engine)
    _drive_registration(runtime)

    assert runtime._honest_resolver is not None and runtime._honest_resolver.open_count == 1
    assert runtime.outcomes == () and runtime.dropped == ()

    update = _spike_to_resolution(runtime)
    assert len(runtime.outcomes) == 1
    outcome = runtime.outcomes[0]
    assert isinstance(outcome, Outcome)
    assert outcome.prediction_id == "pred-1"
    assert outcome.touch_id == "touch-1"
    assert outcome.actual_class == "tradeable_reversal"
    assert outcome.resolution_type is ResolutionType.TP_HIT
    assert outcome.correct is True  # predicted == actual, computed TL-side
    # The honest fill: the ring entry (the +4-tick print at the decision instant),
    # NOT the level price the retired tracker anchored on.
    assert outcome.entry_price == (_LEVEL_TICKS + 4) * 0.25
    # ZERO-BASED bars: the FIRST in-window forward bar resolves at index 0 (the
    # retired tracker would have served a 1-based count of 1).
    assert outcome.bars_to_resolution == 0
    assert outcome.resolved_ts_utc is not None
    assert runtime._honest_resolver.open_count == 0
    assert runtime._open_predictions == {}
    # The resolution rode the RuntimeUpdate (the prediction.resolved path) and snapshot.
    assert update.outcomes == (outcome,)
    assert runtime.snapshot().outcomes == (outcome,)


def test_registration_time_drop_is_served_and_broadcast_end_to_end() -> None:
    engine = _FakeEngine(load_strategy_contract(_FIXTURE_STRATEGY))
    runtime = _runtime(engine=engine)
    broadcaster = WebSocketBroadcaster(runtime)
    # 21:36 UTC = 16:36 ET; decision (+5m) = 16:41 ET >= 16:40 flatten -> drop.
    late = datetime(2026, 1, 5, 21, 36, tzinfo=UTC)
    runtime.observations.start_from_touch(_touch(ts=late))
    runtime.process_market_event(_trade(late + timedelta(seconds=60), _LEVEL_TICKS))
    update = runtime.process_market_event(_trade(late + timedelta(seconds=301), _LEVEL_TICKS))

    assert len(update.dropped) == 1
    dropped = update.dropped[0]
    assert isinstance(dropped, DroppedPrediction)
    assert dropped.reason == "flatten"
    assert dropped.prediction_id == "pred-1"
    assert dropped.touch_id == "touch-1"
    assert dropped.entry_price is None  # registration-time drop: no fill was queried
    assert runtime._honest_resolver.open_count == 0
    assert runtime.dropped == (dropped,)
    assert runtime.outcomes == ()  # a drop never enters the outcome stream
    assert runtime.snapshot().dropped == (dropped,)

    # End-to-end: the drop rides a prediction.dropped WS frame off the same update.
    envelopes = [json.loads(raw) for raw in broadcaster.messages_for_update(update)]
    frames = [e for e in envelopes if e["type"] == "prediction.dropped"]
    assert len(frames) == 1
    payload = frames[0]["payload"]["dropped"]
    assert payload["prediction_id"] == "pred-1"
    assert payload["touch_id"] == "touch-1"
    assert payload["reason"] == "flatten"
    assert payload["entry_price"] is None
    assert payload["decision_ts_utc"] is not None


def test_reset_and_hot_swap_clear_serving_state() -> None:
    engine = _FakeEngine(load_strategy_contract(_FIXTURE_STRATEGY))
    runtime = _runtime(engine=engine)
    _drive_registration(runtime)
    assert runtime._honest_resolver.open_count == 1
    assert runtime._open_predictions != {}

    runtime.reset()
    assert runtime._honest_resolver.open_count == 0
    assert runtime._open_predictions == {}
    assert runtime.outcomes == () and runtime.dropped == ()

    _drive_registration(runtime)
    assert runtime._honest_resolver.open_count == 1
    runtime.set_inference_engine(engine)  # hot-swap rebuilds the resolver
    assert runtime._honest_resolver.open_count == 0
    assert runtime._open_predictions == {}
    assert runtime.outcomes == () and runtime.dropped == ()


def test_dark_seat_surfaces_are_gone_and_streams_stay_typed() -> None:
    engine = _FakeEngine(load_strategy_contract(_FIXTURE_STRATEGY))
    runtime = _runtime(engine=engine)
    _drive_registration(runtime)
    update = _spike_to_resolution(runtime)
    # The dark ring died WITH the flip: the resolver serves, nothing accumulates darkly.
    assert not hasattr(runtime, "dark_outcomes")
    assert "dropped" in RuntimeUpdate.__dataclass_fields__
    assert "dropped" in RuntimeSnapshot.__dataclass_fields__
    # Served streams carry exactly the domain types (no SC envelope leaks).
    assert all(type(o).__name__ == "Outcome" for o in update.outcomes)
    assert all(type(o).__name__ == "Outcome" for o in runtime.snapshot().outcomes)
    assert all(type(d).__name__ == "DroppedPrediction" for d in runtime.snapshot().dropped)


def test_failing_adaptation_never_breaks_the_hot_path(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The serving seat keeps D1a's swallow posture: an emission that cannot be
    adapted is logged and lost, but the trade event, the bar's other emissions,
    and the RuntimeUpdate all survive (the docstring's hot-path invariant)."""

    engine = _FakeEngine(load_strategy_contract(_FIXTURE_STRATEGY))
    runtime = _runtime(engine=engine)
    _drive_registration(runtime)

    def _boom(resolution, prediction):
        raise ValueError("synthetic adapter failure")

    monkeypatch.setattr("trade_lab.services.runtime.resolution_to_outcome", _boom)
    with caplog.at_level(logging.WARNING, logger="trade_lab.services.runtime"):
        update = _spike_to_resolution(runtime)
    # The resolution was lost (logged), not served — and nothing raised.
    assert runtime.outcomes == () and update.outcomes == ()
    assert any("could not be served" in r.getMessage() for r in caplog.records)
    # The hot path kept flowing: subsequent trades still process normally.
    follow_up = runtime.process_market_event(
        _trade(_TOUCH_TS + timedelta(seconds=600), _LEVEL_TICKS + 64)
    )
    assert follow_up.feed_status is not None


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


def test_no_engine_means_no_resolver_and_no_outcome_tracking() -> None:
    runtime = _runtime(engine=None)
    assert runtime._honest_resolver is None
    runtime.observations.start_from_touch(_touch())
    update = runtime.process_market_event(_trade(_TOUCH_TS + timedelta(seconds=301), _LEVEL_TICKS))
    assert update.outcomes == () and update.dropped == ()
    assert runtime.outcomes == () and runtime.dropped == ()
