"""EXEC P2: paper execution tracker — lifecycle, both columns, reset, journal.

Pure state-machine tests: synthetic RuntimeUpdates + the REAL strategy_core
OpenSetupView (pins the P1 seam) + an injected policy. Policy used throughout:
tick 0.25, tp 15.0, sl 30.0, point_value 20.0 — asymmetric tp/sl so a sign slip
cannot cancel out. Long anchor: entry 23000.0 = 92000 ticks, conservative entry
92001 (1 tick adverse), tp 92060 (23015.0), sl 91880 (22970.0).
"""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType

from strategy_core import Direction, OpenSetupView

from trade_lab.domain.candles import Candle
from trade_lab.domain.feed import FeedConnectionState, FeedStatus
from trade_lab.domain.outcomes import DroppedPrediction, Outcome, ResolutionType
from trade_lab.services.execution import (
    ExecutionJournal,
    ExecutionPolicy,
    PaperExecutionTracker,
    executions_root_for,
)
from trade_lab.services.inference.inference_engine import Prediction
from trade_lab.services.runtime import RuntimeUpdate

T0 = datetime(2026, 6, 11, 14, 30, tzinfo=UTC)  # 10:30 ET -> trading day 2026-06-11

POLICY = ExecutionPolicy(tick_size=0.25, tp_points=15.0, sl_points=30.0, point_value=20.0)


def _prediction(pid: str, *, direction: str = "long", eligible: bool = True) -> Prediction:
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
        direction=direction,
        session="ny",
        is_eligible=eligible,
        model_id="bundle-a",
        contract_id="bundle-a",
        nan_count=0,
    )


def _view(pid: str, *, direction: Direction = Direction.LONG) -> OpenSetupView:
    is_long = direction is Direction.LONG
    return OpenSetupView(
        prediction_id=pid,
        entry_price_ticks=92_000,
        entry_ts_utc=T0,
        direction=direction,
        tp_price_ticks=92_060 if is_long else 91_940,
        sl_price_ticks=91_880 if is_long else 92_120,
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


def _feed(mode: str = "replay", ts: datetime | None = None) -> FeedStatus:
    return FeedStatus(
        state=FeedConnectionState.REPLAYING,
        mode=mode,
        requested_symbol="NQ.c.0",
        last_event_ts_utc=ts,
    )


def _outcome(
    pid: str,
    resolution: ResolutionType,
    *,
    resolved_ts: datetime,
    entry_price: float = 23_000.0,
) -> Outcome:
    return Outcome(
        outcome_id=f"out-{pid}",
        prediction_id=pid,
        touch_id=f"touch-{pid}",
        resolution_type=resolution,
        actual_class="tradeable_reversal",
        predicted_class="tradeable_reversal",
        correct=True,
        max_mfe_pts=16.0,
        max_mae_pts=3.0,
        bars_to_resolution=2,
        resolved_ts_utc=resolved_ts,
        entry_price=entry_price,
    )


def _tracker(
    tmp_path: Path, views: list[OpenSetupView]
) -> tuple[PaperExecutionTracker, Path]:
    root = executions_root_for(tmp_path / "journal")
    tracker = PaperExecutionTracker(
        open_setups=lambda: tuple(views),
        policy=lambda: POLICY,
        journal=ExecutionJournal(root),
    )
    return tracker, root


def _rows(root: Path) -> list[dict]:
    rows: list[dict] = []
    for file in sorted(root.glob("*.jsonl")):
        rows.extend(json.loads(line) for line in file.read_text("utf-8").splitlines())
    return rows


def _open_update(pid: str, *, direction: str = "long", eligible: bool = True) -> RuntimeUpdate:
    return RuntimeUpdate(
        feed_status=_feed(ts=T0),
        current_bars=(_bar(92_000, T0),),
        predictions=(_prediction(pid, direction=direction, eligible=eligible),),
    )


def test_open_from_view_then_tp_close_both_columns(tmp_path: Path) -> None:
    views = [_view("p1")]
    tracker, _root = _tracker(tmp_path, views)

    opened, closed = tracker.observe(_open_update("p1"))
    assert closed == ()
    assert len(opened) == 1
    position = opened[0]
    assert position.entry_price == 23_000.0
    assert position.entry_price_conservative == 23_000.25  # 1 tick adverse
    assert position.tp_price == 23_015.0
    assert position.sl_price == 22_970.0
    assert position.contracts == 1
    assert position.point_value == 20.0
    assert position.mode == "replay"
    assert position.source == "open_setups"
    assert tracker.open_positions() == (position,)

    views.clear()  # the resolver emitted for it: the setup leaves the snapshot
    resolved_ts = T0 + timedelta(minutes=10)
    opened, closed = tracker.observe(
        RuntimeUpdate(outcomes=(_outcome("p1", ResolutionType.TP_HIT, resolved_ts=resolved_ts),))
    )
    assert opened == ()
    assert len(closed) == 1
    execution = closed[0]
    assert execution.reason == "tp_hit"
    # tp exit at the barrier in BOTH columns (the print requirement is already
    # resolution semantics); only the entry differs.
    assert execution.exit_price == 23_015.0
    assert execution.exit_price_conservative == 23_015.0
    assert execution.points == 15.0
    assert execution.points_conservative == 14.75
    assert execution.dollars == 300.0
    assert execution.dollars_conservative == 295.0
    assert execution.exit_ts_utc == resolved_ts
    assert tracker.open_positions() == ()


def test_sl_close_is_two_ticks_worse_conservative(tmp_path: Path) -> None:
    views = [_view("p1")]
    tracker, _ = _tracker(tmp_path, views)
    tracker.observe(_open_update("p1"))
    views.clear()

    _, closed = tracker.observe(
        RuntimeUpdate(
            outcomes=(
                _outcome("p1", ResolutionType.SL_HIT, resolved_ts=T0 + timedelta(minutes=5)),
            )
        )
    )
    execution = closed[0]
    assert execution.reason == "sl_hit"
    assert execution.exit_price == 22_970.0
    assert execution.exit_price_conservative == 22_969.75  # sl exit 1 tick adverse
    assert execution.points == -30.0
    assert execution.points_conservative == -30.5  # adverse entry + adverse sl exit
    assert execution.dollars == -600.0
    assert execution.dollars_conservative == -610.0


def test_short_direction_arithmetic_mirrors(tmp_path: Path) -> None:
    views = [_view("p1", direction=Direction.SHORT)]
    tracker, _ = _tracker(tmp_path, views)
    opened, _ = tracker.observe(_open_update("p1", direction="short"))
    position = opened[0]
    assert position.entry_price_conservative == 22_999.75  # short: 1 tick BELOW
    assert position.tp_price == 22_985.0
    assert position.sl_price == 23_030.0
    views.clear()

    _, closed = tracker.observe(
        RuntimeUpdate(
            outcomes=(
                _outcome("p1", ResolutionType.SL_HIT, resolved_ts=T0 + timedelta(minutes=5)),
            )
        )
    )
    execution = closed[0]
    assert execution.exit_price == 23_030.0
    assert execution.exit_price_conservative == 23_030.25  # adverse = higher for short
    assert execution.points == -30.0
    assert execution.points_conservative == -30.5


def test_terminal_drop_closes_at_last_seen_trade_price(tmp_path: Path) -> None:
    views = [_view("p1")]
    tracker, _ = _tracker(tmp_path, views)
    tracker.observe(_open_update("p1"))
    views.clear()

    bar_ts = T0 + timedelta(minutes=7)
    tracker.observe(RuntimeUpdate(current_bars=(_bar(92_010, bar_ts),)))
    _, closed = tracker.observe(
        RuntimeUpdate(
            dropped=(
                DroppedPrediction("p1", "touch-p1", "no_resolution", T0, entry_price=23_000.0),
            )
        )
    )
    execution = closed[0]
    assert execution.reason == "no_resolution"
    # A print is a print: both columns exit at the last-seen trade price; only
    # the conservative entry differs.
    assert execution.exit_price == 23_002.5
    assert execution.exit_price_conservative == 23_002.5
    assert execution.points == 2.5
    assert execution.points_conservative == 2.25
    assert execution.exit_ts_utc == bar_ts
    assert tracker.open_positions() == ()


def test_registration_drop_means_the_position_never_existed(tmp_path: Path) -> None:
    # The resolver never filled (flatten at registration, entry null): the
    # eligible prediction has no setup, and no position may ever open.
    tracker, root = _tracker(tmp_path, views=[])
    update = RuntimeUpdate(
        feed_status=_feed(ts=T0),
        current_bars=(_bar(92_000, T0),),
        predictions=(_prediction("p1"),),
        dropped=(DroppedPrediction("p1", "touch-p1", "flatten", T0, entry_price=None),),
    )
    opened, closed = tracker.observe(update)
    assert opened == () and closed == ()
    assert tracker.open_positions() == ()
    assert "eligible_prediction_without_setup" not in tracker.anomalies()
    assert all(row["type"] != "open" for row in _rows(root))

    # Invariant guard: a registration-time drop arriving for an OPEN position
    # is counted and clears the position without synthesizing a fill.
    views = [_view("p2")]
    tracker2, _ = _tracker(tmp_path / "second", views)
    tracker2.observe(_open_update("p2"))
    _, closed = tracker2.observe(
        RuntimeUpdate(
            dropped=(DroppedPrediction("p2", "touch-p2", "no_fill", T0, entry_price=None),)
        )
    )
    assert closed == ()
    assert tracker2.open_positions() == ()
    assert tracker2.anomalies()["registration_drop_for_open_position"] == 1


def test_ineligible_predictions_are_never_tracked(tmp_path: Path) -> None:
    views = [_view("p1")]
    tracker, _ = _tracker(tmp_path, views)
    opened, _ = tracker.observe(_open_update("p1", eligible=False))
    assert opened == ()
    assert tracker.open_positions() == ()
    # Its later outcome is not ours either.
    _, closed = tracker.observe(
        RuntimeUpdate(
            outcomes=(
                _outcome("p1", ResolutionType.TP_HIT, resolved_ts=T0 + timedelta(minutes=5)),
            )
        )
    )
    assert closed == ()


def test_reset_clears_open_positions_with_a_reset_row(tmp_path: Path) -> None:
    views = [_view("p1")]
    tracker, root = _tracker(tmp_path, views)
    tracker.observe(_open_update("p1"))
    assert len(tracker.open_positions()) == 1

    opened, closed = tracker.observe(
        RuntimeUpdate(feed_status=_feed(mode="idle"), model_reset_reason="replay_reset")
    )
    assert opened == () and closed == ()  # no synthetic closes, no phantom carry
    assert tracker.open_positions() == ()
    reset_rows = [row for row in _rows(root) if row["type"] == "reset"]
    assert len(reset_rows) == 1
    assert reset_rows[0]["reason"] == "replay_reset"
    assert reset_rows[0]["cleared"] == 1
    assert reset_rows[0]["cleared_prediction_ids"] == ["p1"]

    # A late terminal drop for the cleared id is ignored (the position is gone).
    _, closed = tracker.observe(
        RuntimeUpdate(
            dropped=(
                DroppedPrediction("p1", "touch-p1", "no_resolution", T0, entry_price=23_000.0),
            )
        )
    )
    assert closed == ()


def test_same_batch_registration_and_resolution_falls_back_to_the_outcome_fill(
    tmp_path: Path,
) -> None:
    # The setup registered AND resolved within one coalesced batch: it is
    # already gone from open_setups(), so the open reconstructs from the
    # outcome row's own honest entry + the policy barriers.
    tracker, _ = _tracker(tmp_path, views=[])
    resolved_ts = T0 + timedelta(minutes=1)
    update = RuntimeUpdate(
        feed_status=_feed(ts=resolved_ts),
        current_bars=(_bar(92_060, resolved_ts),),
        predictions=(_prediction("p1"),),
        outcomes=(_outcome("p1", ResolutionType.TP_HIT, resolved_ts=resolved_ts),),
    )
    opened, closed = tracker.observe(update)
    assert len(opened) == 1 and len(closed) == 1
    assert opened[0].source == "resolution_fallback"
    assert opened[0].entry_price == 23_000.0
    assert closed[0].points == 15.0
    assert closed[0].points_conservative == 14.75
    assert tracker.anomalies()["opened_from_resolution"] == 1
    assert tracker.open_positions() == ()


def test_unrealized_both_columns_against_last_price(tmp_path: Path) -> None:
    views = [_view("p1")]
    tracker, _ = _tracker(tmp_path, views)
    opened, _ = tracker.observe(_open_update("p1"))
    position = opened[0]
    assert position.unrealized_points(None) is None
    assert position.unrealized_points(92_010) == 2.5
    assert position.unrealized_points_conservative(92_010) == 2.25
    assert tracker.last_trade_price_ticks == 92_000


def test_journal_rows_shape_and_trading_day_files(tmp_path: Path) -> None:
    views = [_view("p1")]
    tracker, root = _tracker(tmp_path, views)
    tracker.observe(_open_update("p1"))
    views.clear()
    tracker.observe(
        RuntimeUpdate(
            outcomes=(
                _outcome("p1", ResolutionType.TP_HIT, resolved_ts=T0 + timedelta(minutes=5)),
            )
        )
    )
    tracker.observe(RuntimeUpdate(model_reset_reason="replay_reset"))

    day_file = root / "2026-06-11.jsonl"
    assert day_file.is_file()
    rows = _rows(root)
    assert [row["type"] for row in rows] == ["open", "close", "reset"]
    open_row, close_row, reset_row = rows
    assert set(open_row) == {
        "type",
        "mode",
        "bundle_id",
        "ts_utc",
        "prediction_id",
        "touch_id",
        "direction",
        "contracts",
        "point_value",
        "tick_size",
        "entry_price",
        "entry_price_conservative",
        "tp_price",
        "sl_price",
        "session",
        "level_kind",
        "source",
    }
    assert set(close_row) == {
        "type",
        "mode",
        "bundle_id",
        "ts_utc",
        "prediction_id",
        "touch_id",
        "reason",
        "direction",
        "contracts",
        "point_value",
        "entry_ts_utc",
        "entry_price",
        "entry_price_conservative",
        "exit_price",
        "exit_price_conservative",
        "points",
        "points_conservative",
        "dollars",
        "dollars_conservative",
        "session",
        "level_kind",
    }
    assert close_row["points"] == 15.0
    assert close_row["points_conservative"] == 14.75
    assert set(reset_row) == {
        "type",
        "mode",
        "ts_utc",
        "reason",
        "cleared",
        "cleared_prediction_ids",
    }


def test_journal_append_failure_never_raises(tmp_path: Path) -> None:
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    views = [_view("p1")]
    tracker = PaperExecutionTracker(
        open_setups=lambda: tuple(views),
        policy=lambda: POLICY,
        journal=ExecutionJournal(blocker / "executions"),
    )
    opened, _ = tracker.observe(_open_update("p1"))  # must not raise
    assert len(opened) == 1


def test_outcome_for_unknown_prediction_is_ignored(tmp_path: Path) -> None:
    tracker, _ = _tracker(tmp_path, views=[])
    _, closed = tracker.observe(
        RuntimeUpdate(
            outcomes=(_outcome("ghost", ResolutionType.TP_HIT, resolved_ts=T0),)
        )
    )
    assert closed == ()
    assert tracker.open_positions() == ()
