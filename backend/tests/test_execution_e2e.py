"""EXEC end-to-end: real resolver -> accessor -> tracker -> WS + journal.

Unlike the unit tests (fake providers), this drives the FULL observer chain the
way production wires it: ApplicationRuntime with the real SC streaming resolver
(fixture contract: tick 0.25, tp 15, sl 30, point_value 20) -> the runtime's
open_setup_views() pass-through -> PaperExecutionTracker -> WebSocketBroadcaster
message builder (the choke point every update flows through) -> the executions
journal on disk. Also pins the observer invariant: the tracker's presence does
not change the runtime's served outcome stream.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

from strategy_core import load_strategy_contract

from trade_lab.domain.events import TradeEvent, TradeSide
from trade_lab.domain.levels import LevelDirection, LevelKind, TouchEvent
from trade_lab.domain.sessions import SessionName
from trade_lab.services.broadcaster import WebSocketBroadcaster
from trade_lab.services.execution import (
    ExecutionJournal,
    ExecutionPolicy,
    PaperExecutionTracker,
    executions_root_for,
)
from trade_lab.services.inference.inference_engine import Prediction
from trade_lab.services.runtime import ApplicationRuntime

_FIXTURE_STRATEGY = Path(__file__).parent / "fixtures" / "strategy.json"
_TOUCH_TS = datetime(2026, 1, 5, 14, 0, tzinfo=UTC)  # 10:00 ET weekday
_LEVEL_TICKS = 68_000


class _FakeEngine:
    def __init__(self, contract) -> None:
        self.active_contract = contract
        self.has_active_model = True
        self._counter = 0

    def active(self):
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


def _wire(tmp_path: Path):
    """Compose runtime + broadcaster + tracker exactly the way create_app does."""

    contract = load_strategy_contract(_FIXTURE_STRATEGY)
    engine = _FakeEngine(contract)
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0",
        tick_timeframes=(147,),
        observation_duration_seconds=300,
        inference_engine=engine,
    )
    broadcaster = WebSocketBroadcaster(runtime)
    executions_root = executions_root_for(tmp_path / "journal")
    tracker = PaperExecutionTracker(
        open_setups=runtime.open_setup_views,
        policy=lambda: ExecutionPolicy(
            tick_size=contract.tick_size,
            tp_points=contract.label_policy.tp_points,
            sl_points=contract.label_policy.sl_points,
            point_value=contract.point_value,
        ),
        journal=ExecutionJournal(executions_root),
    )
    broadcaster.set_execution_tracker(tracker)
    return runtime, broadcaster, tracker, executions_root


def _drive(runtime, broadcaster, event) -> list[dict]:
    """Process one event and broadcast its update — the production flow."""

    update = runtime.process_market_event(event)
    return [json.loads(raw) for raw in broadcaster.messages_for_update(update)]


def test_full_lifecycle_through_the_real_resolver_and_choke_point(tmp_path: Path) -> None:
    runtime, broadcaster, tracker, executions_root = _wire(tmp_path)
    runtime.observations.start_from_touch(_touch())
    _drive(runtime, broadcaster, _trade(_TOUCH_TS + timedelta(seconds=60), _LEVEL_TICKS + 4))
    envelopes = _drive(
        runtime, broadcaster, _trade(_TOUCH_TS + timedelta(seconds=301), _LEVEL_TICKS + 6)
    )

    # The registration update carried prediction.created AND position.opened.
    types = [e["type"] for e in envelopes]
    assert "prediction.created" in types and "position.opened" in types
    opened = next(e for e in envelopes if e["type"] == "position.opened")["payload"]["position"]
    # The honest fill from the REAL resolver accessor: the ring print at the
    # decision instant is the +4-tick trade -> 17001.0. Barriers = fixture policy.
    assert opened["entry_price"] == 17_001.0
    assert opened["entry_price_conservative"] == 17_001.25
    assert opened["tp_price"] == 17_016.0
    assert opened["sl_price"] == 16_971.0
    assert opened["point_value"] == 20.0
    assert len(tracker.open_positions()) == 1

    # Spike one 147t forward bar to +16 pts: TP resolution.
    ts = _TOUCH_TS + timedelta(seconds=310)
    closed_frames: list[dict] = []
    for i in range(147):
        envelopes = _drive(
            runtime, broadcaster, _trade(ts + timedelta(seconds=i), _LEVEL_TICKS + 64)
        )
        closed_frames.extend(e for e in envelopes if e["type"] == "position.closed")
        if closed_frames:
            resolved_types = [e["type"] for e in envelopes]
            assert resolved_types.index("prediction.resolved") < resolved_types.index(
                "position.closed"
            )
            break
    assert len(closed_frames) == 1
    execution = closed_frames[0]["payload"]["execution"]
    assert execution["reason"] == "tp_hit"
    assert execution["exit_price"] == 17_016.0
    assert execution["points"] == 15.0
    assert execution["points_conservative"] == 14.75
    assert execution["dollars"] == 300.0
    assert tracker.open_positions() == ()

    # The journal on disk: one open + one close row in the touch's trading-day file.
    rows = []
    for file in sorted(executions_root.glob("*.jsonl")):
        rows.extend(json.loads(line) for line in file.read_text("utf-8").splitlines())
    assert [row["type"] for row in rows] == ["open", "close"]
    assert rows[1]["points"] == 15.0

    # Observer invariant: the runtime's own served streams are what they always
    # were — one outcome, no drops, resolver empty.
    assert len(runtime.outcomes) == 1
    assert runtime.dropped == ()
    assert runtime.open_setup_views() == ()


def test_registration_drop_never_opens_through_the_full_chain(tmp_path: Path) -> None:
    runtime, broadcaster, tracker, executions_root = _wire(tmp_path)
    # 21:36 UTC = 16:36 ET; decision (+5m) = 16:41 ET >= 16:40 flatten -> drop.
    late = datetime(2026, 1, 5, 21, 36, tzinfo=UTC)
    runtime.observations.start_from_touch(_touch(ts=late))
    _drive(runtime, broadcaster, _trade(late + timedelta(seconds=60), _LEVEL_TICKS))
    envelopes = _drive(runtime, broadcaster, _trade(late + timedelta(seconds=301), _LEVEL_TICKS))

    types = [e["type"] for e in envelopes]
    assert "prediction.dropped" in types
    assert "position.opened" not in types and "position.closed" not in types
    assert tracker.open_positions() == ()
    # The position never existed: nothing was journaled.
    assert not list(executions_root.glob("*.jsonl"))
