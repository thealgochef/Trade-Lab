"""EXEC P3a: typed execution events + snapshot open_positions block.

Drives the REAL WebSocketBroadcaster message builder with a tracker wired the
way create_app wires it, and pins: position.opened/closed envelopes ride the
same broadcast as their update (after the prediction frames), the snapshot
carries the open-position block with unrealized P&L in both columns, and a
broadcaster without a tracker emits no execution frames.
"""

import json
from datetime import UTC, date, datetime, timedelta
from types import MappingProxyType

from strategy_core import Direction, OpenSetupView

from trade_lab.domain.candles import Candle
from trade_lab.domain.feed import FeedConnectionState, FeedStatus
from trade_lab.domain.outcomes import Outcome, ResolutionType
from trade_lab.services.broadcaster import WebSocketBroadcaster
from trade_lab.services.execution import ExecutionPolicy, PaperExecutionTracker
from trade_lab.services.inference.inference_engine import Prediction
from trade_lab.services.runtime import ApplicationRuntime, RuntimeUpdate

T0 = datetime(2026, 6, 11, 14, 30, tzinfo=UTC)

POLICY = ExecutionPolicy(tick_size=0.25, tp_points=15.0, sl_points=30.0, point_value=20.0)


def _prediction(pid: str) -> Prediction:
    return Prediction(
        prediction_id=pid,
        touch_id=f"touch-{pid}",
        observation_id=f"obs-{pid}",
        event_ts_utc=T0,
        predicted_class="tradeable_reversal",
        probabilities=MappingProxyType({"tradeable_reversal": 0.8}),
        feature_values=MappingProxyType({"f": 1.0}),
        level_kind="pdl",
        level_price_ticks=92_000,
        direction="long",
        session="ny",
        is_eligible=True,
        model_id="bundle-a",
        contract_id="bundle-a",
        nan_count=0,
    )


def _view(pid: str) -> OpenSetupView:
    return OpenSetupView(
        prediction_id=pid,
        entry_price_ticks=92_000,
        entry_ts_utc=T0,
        direction=Direction.LONG,
        tp_price_ticks=92_060,
        sl_price_ticks=91_880,
    )


def _bar(close_ticks: int, close_ts: datetime) -> Candle:
    return Candle(
        timeframe_ticks=147,
        trading_day=date(2026, 6, 11),
        bar_index=0,
        bar_id="147t:2026-06-11:0",
        open_ts_utc=close_ts - timedelta(seconds=30),
        close_ts_utc=close_ts,
        open_ticks=close_ticks,
        high_ticks=close_ticks,
        low_ticks=close_ticks,
        close_ticks=close_ticks,
        volume=147,
        trade_count=147,
        is_complete=True,
        is_partial=False,
    )


def _runtime() -> ApplicationRuntime:
    return ApplicationRuntime(
        requested_symbol="NQ.c.0",
        tick_timeframes=(147,),
        observation_duration_seconds=300,
    )


def _broadcaster(views: list[OpenSetupView]) -> tuple[WebSocketBroadcaster, PaperExecutionTracker]:
    broadcaster = WebSocketBroadcaster(_runtime())
    tracker = PaperExecutionTracker(
        open_setups=lambda: tuple(views),
        policy=lambda: POLICY,
        journal=None,
    )
    broadcaster.set_execution_tracker(tracker)
    return broadcaster, tracker


def _decode(messages: tuple[bytes, ...]) -> list[dict]:
    return [json.loads(message) for message in messages]


def _open_update(pid: str) -> RuntimeUpdate:
    return RuntimeUpdate(
        feed_status=FeedStatus(
            state=FeedConnectionState.REPLAYING,
            mode="replay",
            requested_symbol="NQ.c.0",
            last_event_ts_utc=T0,
        ),
        current_bars=(_bar(92_000, T0),),
        predictions=(_prediction(pid),),
    )


def test_position_opened_and_closed_frames_ride_the_broadcast() -> None:
    views = [_view("p1")]
    broadcaster, _tracker = _broadcaster(views)

    envelopes = _decode(broadcaster.messages_for_update(_open_update("p1")))
    types = [envelope["type"] for envelope in envelopes]
    assert "position.opened" in types
    # The position frame derives from the prediction frame, so it comes after.
    assert types.index("prediction.created") < types.index("position.opened")
    position = next(e for e in envelopes if e["type"] == "position.opened")["payload"][
        "position"
    ]
    assert position["prediction_id"] == "p1"
    assert position["direction"] == "long"
    assert position["entry_price"] == 23_000.0
    assert position["entry_price_conservative"] == 23_000.25
    assert position["tp_price"] == 23_015.0
    assert position["sl_price"] == 22_970.0
    assert position["last_price"] == 23_000.0
    assert position["unrealized_points"] == 0.0
    assert position["unrealized_points_conservative"] == -0.25

    views.clear()
    resolved_ts = T0 + timedelta(minutes=10)
    close_update = RuntimeUpdate(
        outcomes=(
            Outcome(
                outcome_id="out-p1",
                prediction_id="p1",
                touch_id="touch-p1",
                resolution_type=ResolutionType.TP_HIT,
                actual_class="tradeable_reversal",
                predicted_class="tradeable_reversal",
                correct=True,
                max_mfe_pts=16.0,
                max_mae_pts=3.0,
                bars_to_resolution=2,
                resolved_ts_utc=resolved_ts,
                entry_price=23_000.0,
            ),
        )
    )
    envelopes = _decode(broadcaster.messages_for_update(close_update))
    types = [envelope["type"] for envelope in envelopes]
    assert "position.closed" in types
    assert types.index("prediction.resolved") < types.index("position.closed")
    execution = next(e for e in envelopes if e["type"] == "position.closed")["payload"][
        "execution"
    ]
    assert execution["reason"] == "tp_hit"
    assert execution["points"] == 15.0
    assert execution["points_conservative"] == 14.75
    assert execution["dollars"] == 300.0
    assert execution["dollars_conservative"] == 295.0


def test_snapshot_carries_the_open_positions_block() -> None:
    views = [_view("p1")]
    broadcaster, tracker = _broadcaster(views)
    broadcaster.messages_for_update(_open_update("p1"))
    # A later print moves the mark: unrealized is computed against it.
    broadcaster.messages_for_update(
        RuntimeUpdate(current_bars=(_bar(92_010, T0 + timedelta(minutes=1)),))
    )
    assert tracker.last_trade_price_ticks == 92_010

    payload = broadcaster.snapshot_payload().model_dump(mode="json")
    assert len(payload["open_positions"]) == 1
    position = payload["open_positions"][0]
    assert position["prediction_id"] == "p1"
    assert position["last_price"] == 23_002.5
    assert position["unrealized_points"] == 2.5
    assert position["unrealized_points_conservative"] == 2.25


def test_broadcaster_without_tracker_emits_no_execution_frames() -> None:
    broadcaster = WebSocketBroadcaster(_runtime())
    envelopes = _decode(broadcaster.messages_for_update(_open_update("p1")))
    assert all(not e["type"].startswith("position.") for e in envelopes)
    assert broadcaster.snapshot_payload().open_positions == []
