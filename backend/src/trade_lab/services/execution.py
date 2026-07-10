"""Paper execution tracking — derived fills, positions, and P&L (EXEC P2).

An OBSERVER OF THE OBSERVER: the tracker consumes the same
:class:`~trade_lab.services.runtime.RuntimeUpdate` stream the WebSocket
broadcaster fans out to the UI, plus two read-only providers (the honest
resolver's :meth:`open_setups` snapshot and the active contract's execution
policy). INVARIANT: zero influence on touches, inference, the resolver, or the
prediction journal — nothing here mutates runtime/engine state, and the only
artifact written is the tracker's own ``executions/<trading_day>.jsonl``
(beside the prediction journal, same never-raises discipline).

OPEN: when an ELIGIBLE prediction's setup appears in ``open_setups()`` the
resolver filled it — the position opens at the honest entry (the view's fill),
side from the prediction's direction. Ineligible predictions are never tracked
in v1. A same-batch race (the setup registers AND resolves/terminal-drops on
one trade batch, so it is already gone from the snapshot) opens from the
outcome/drop row's own honest ``entry_price`` and is counted
(``opened_from_resolution``).

CLOSE: ``tp_hit``/``sl_hit`` outcomes exit at the setup's barrier price;
terminal drops (``no_forward``/``no_resolution`` — entry present) exit at the
tracker's last-seen trade price at the drop instant (the latest trade print in
the tracker's observed stream when the drop is observed); registration-time
drops (``flatten``/``cutoff``/``no_fill`` — entry null) mean the resolver never
filled, so the position never existed (an open position receiving one is an
invariant violation, counted, never raised).

TWO COLUMNS per fill, both always computed:

* ``optimistic`` — exact anchor/barriers: entry at the honest fill, tp/sl exits
  at the exact barrier prices, drop exits at the last trade print.
* ``conservative`` — entry 1 tick adverse; sl exit 1 tick adverse; tp exit AT
  the barrier (the print requirement is already resolution semantics: the
  excursion rule fires only once a print reaches the barrier); drop exits at
  the print as-is (it is already a real print).

Sizing: 1 contract at the active contract's ``point_value`` (default 20).

RESET semantics: a runtime reset (replay start / live start / model
activation, observed via ``RuntimeUpdate.model_reset_reason``) clears open
positions with a ``reset`` event row — no phantom carry, no synthetic closes.

LIVE-PHASE ONLY BY CONSTRUCTION: the live warm gate (WARM-FIX P3) suppresses
prediction production for warm-replayed touches, so no eligible prediction —
and therefore no position — can exist during the live warm-start drain; live
positions are live-phase only. Replay positions ride the replay clock.

All prices are tracked internally in integer TICKS (the view/bar grid) and
surfaced in points via the policy ``tick_size``, so column arithmetic is exact.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from trade_lab.domain.outcomes import DroppedPrediction, Outcome, ResolutionType
from trade_lab.domain.trading_day import trading_day_for
from trade_lab.services.inference.inference_engine import Prediction
from trade_lab.services.runtime import RuntimeUpdate

logger = logging.getLogger(__name__)

#: Registration-time drop reasons: the resolver never filled (entry null).
REGISTRATION_DROP_REASONS = frozenset({"flatten", "cutoff", "no_fill"})
#: Terminal drop reasons: the setup was live and carries the honest entry.
TERMINAL_DROP_REASONS = frozenset({"no_forward", "no_resolution"})

#: Bound on simultaneously open tracked positions. Setups resolve or flush at
#: their cutoff daily, so this is a leak backstop, not an expected limit.
_OPEN_POSITION_CAP = 500


def executions_root_for(journal_path: Path) -> Path:
    """The executions directory beside the prediction journal directory."""

    return Path(journal_path).parent / "executions"


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    """The active contract's execution economics, read at open time."""

    tick_size: float
    tp_points: float
    sl_points: float
    point_value: float = 20.0


@dataclass(frozen=True, slots=True)
class OpenPosition:
    """One tracked paper position, priced in integer ticks (exact arithmetic).

    ``entry_price_ticks`` is the honest fill (optimistic column);
    ``entry_price_conservative_ticks`` is 1 tick adverse. ``source`` is
    ``"open_setups"`` for the normal path or ``"resolution_fallback"`` for the
    same-batch race (see the module docstring).
    """

    prediction_id: str
    touch_id: str
    direction: str  # "long" | "short"
    contracts: int
    entry_ts_utc: datetime
    entry_price_ticks: int
    entry_price_conservative_ticks: int
    tp_price_ticks: int
    sl_price_ticks: int
    tick_size: float
    point_value: float
    session: str
    level_kind: str
    bundle_id: str
    mode: str
    source: str

    @property
    def is_long(self) -> bool:
        return self.direction == "long"

    @property
    def entry_price(self) -> float:
        return self.entry_price_ticks * self.tick_size

    @property
    def entry_price_conservative(self) -> float:
        return self.entry_price_conservative_ticks * self.tick_size

    @property
    def tp_price(self) -> float:
        return self.tp_price_ticks * self.tick_size

    @property
    def sl_price(self) -> float:
        return self.sl_price_ticks * self.tick_size

    def _points(self, entry_ticks: int, exit_ticks: int) -> float:
        signed = exit_ticks - entry_ticks if self.is_long else entry_ticks - exit_ticks
        return signed * self.tick_size

    def unrealized_points(self, last_trade_price_ticks: int | None) -> float | None:
        """Optimistic mark-to-last-print (no exit adjustment: a print is a print)."""

        if last_trade_price_ticks is None:
            return None
        return self._points(self.entry_price_ticks, last_trade_price_ticks)

    def unrealized_points_conservative(
        self, last_trade_price_ticks: int | None
    ) -> float | None:
        """Conservative mark: the 1-tick-adverse entry against the same print."""

        if last_trade_price_ticks is None:
            return None
        return self._points(self.entry_price_conservative_ticks, last_trade_price_ticks)


@dataclass(frozen=True, slots=True)
class ClosedExecution:
    """One closed paper execution: both columns realized, in points and dollars."""

    prediction_id: str
    touch_id: str
    direction: str
    contracts: int
    entry_ts_utc: datetime
    exit_ts_utc: datetime | None
    reason: str  # tp_hit | sl_hit | no_forward | no_resolution
    entry_price: float
    entry_price_conservative: float
    exit_price: float
    exit_price_conservative: float
    points: float
    points_conservative: float
    dollars: float
    dollars_conservative: float
    point_value: float
    session: str
    level_kind: str
    bundle_id: str
    mode: str


class ExecutionJournal:
    """Append paper-execution events to per-trading-day JSONL files.

    Mirrors :class:`~trade_lab.services.journal.PredictionJournal`: one file per
    trading day (18:00 ET roll) under the executions root, line-buffered append,
    no fsync, failures logged and never propagated.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def record(self, ts_utc: datetime | None, payload: dict[str, Any]) -> None:
        try:
            day = trading_day_for(ts_utc) if ts_utc is not None else None
            name = f"{day.isoformat()}.jsonl" if day is not None else "undated.jsonl"
            self._root.mkdir(parents=True, exist_ok=True)
            line = json.dumps(payload, default=_json_default, separators=(",", ":"))
            with (self._root / name).open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:
            logger.warning("execution journal append failed", exc_info=True)


class PaperExecutionTracker:
    """The pure paper-execution state machine over observed RuntimeUpdates.

    Inputs are injected read-only providers: ``open_setups`` returns the honest
    resolver's current :class:`OpenSetupView` snapshot (duck-typed: objects with
    ``prediction_id``/``entry_price_ticks``/``entry_ts_utc``/``tp_price_ticks``/
    ``sl_price_ticks``); ``policy`` returns the active contract's
    :class:`ExecutionPolicy` (or ``None`` when no model is active — no position
    can open then, counted). ``observe()`` must be called once per broadcast
    RuntimeUpdate, AFTER the runtime processed the underlying events (the
    broadcaster's position). It returns the (opened, closed) deltas for the WS
    surface and never raises.
    """

    def __init__(
        self,
        *,
        open_setups: Callable[[], Sequence[Any]],
        policy: Callable[[], ExecutionPolicy | None],
        journal: ExecutionJournal | None = None,
    ) -> None:
        self._open_setups = open_setups
        self._policy = policy
        self._journal = journal
        self._open: dict[str, OpenPosition] = {}
        self._mode = "unknown"
        self._last_trade_price_ticks: int | None = None
        self._last_event_ts_utc: datetime | None = None
        self._anomalies: dict[str, int] = {}

    # -- read-only surface ---------------------------------------------------

    @property
    def last_trade_price_ticks(self) -> int | None:
        return self._last_trade_price_ticks

    def open_positions(self) -> tuple[OpenPosition, ...]:
        return tuple(self._open.values())

    def anomalies(self) -> dict[str, int]:
        return dict(self._anomalies)

    # -- the state machine ---------------------------------------------------

    def observe(
        self, update: RuntimeUpdate
    ) -> tuple[tuple[OpenPosition, ...], tuple[ClosedExecution, ...]]:
        """Advance on one observed update; returns (opened, closed) deltas."""

        try:
            return self._observe(update)
        except Exception:
            # The tracker must never break the broadcast path it observes.
            logger.warning("paper execution tracker observe failed", exc_info=True)
            return (), ()

    def _observe(
        self, update: RuntimeUpdate
    ) -> tuple[tuple[OpenPosition, ...], tuple[ClosedExecution, ...]]:
        # Reset FIRST: a coalesced replay batch can carry the reset plus the
        # fresh post-reset deltas in one update (the broadcaster emits the reset
        # frame first for the same reason).
        if update.model_reset_reason is not None:
            self._reset(update.model_reset_reason)
        if update.feed_status is not None:
            self._mode = update.feed_status.mode
            if update.feed_status.last_event_ts_utc is not None:
                self._last_event_ts_utc = update.feed_status.last_event_ts_utc
        self._advance_last_trade(update)

        opened: list[OpenPosition] = []
        for prediction in update.predictions:
            position = self._maybe_open(prediction, update)
            if position is not None:
                opened.append(position)

        closed: list[ClosedExecution] = []
        for outcome in update.outcomes:
            execution = self._close_on_outcome(outcome)
            if execution is not None:
                closed.append(execution)
        for drop in update.dropped:
            execution = self._close_on_drop(drop)
            if execution is not None:
                closed.append(execution)
        return tuple(opened), tuple(closed)

    def _advance_last_trade(self, update: RuntimeUpdate) -> None:
        # The last trade print, observed exactly as the UI observes it: the
        # newest bar's close is the newest trade's price. Closed bars first so
        # a batch's forming bar (which includes its latest trade) wins.
        for bars in (update.closed_bars, update.current_bars):
            if bars:
                bar = bars[-1]
                self._last_trade_price_ticks = bar.close_ticks
                self._last_event_ts_utc = bar.close_ts_utc

    # -- open ------------------------------------------------------------------

    def _maybe_open(
        self, prediction: Prediction, update: RuntimeUpdate
    ) -> OpenPosition | None:
        if not prediction.is_eligible:
            return None  # ineligible predictions are never tracked in v1
        pid = prediction.prediction_id
        if pid in self._open:
            self._count("duplicate_open_suppressed")
            return None
        policy = self._policy()
        if policy is None:
            self._count("open_without_policy")
            return None
        view = self._find_view(pid)
        if view is not None:
            position = self._position_from_view(prediction, view, policy)
        else:
            position = self._position_from_same_batch_resolution(
                prediction, update, policy
            )
            if position is None:
                # No fill exists: a registration-time drop rode this update, or
                # the resolver registration failed upstream. Never opened.
                if not any(
                    drop.prediction_id == pid
                    and drop.reason in REGISTRATION_DROP_REASONS
                    for drop in update.dropped
                ):
                    self._count("eligible_prediction_without_setup")
                return None
        self._open[pid] = position
        self._evict_if_over_cap()
        self._journal_open(position)
        return position

    def _find_view(self, prediction_id: str) -> Any | None:
        try:
            views = self._open_setups()
        except Exception:
            logger.warning("open-setup snapshot read failed", exc_info=False)
            self._count("open_setups_read_failed")
            return None
        for view in views:
            if view.prediction_id == prediction_id:
                return view
        return None

    def _position_from_view(
        self, prediction: Prediction, view: Any, policy: ExecutionPolicy
    ) -> OpenPosition:
        entry_ticks = int(view.entry_price_ticks)
        return self._build_position(
            prediction,
            policy,
            entry_ticks=entry_ticks,
            entry_ts_utc=view.entry_ts_utc,
            tp_ticks=int(view.tp_price_ticks),
            sl_ticks=int(view.sl_price_ticks),
            source="open_setups",
        )

    def _position_from_same_batch_resolution(
        self, prediction: Prediction, update: RuntimeUpdate, policy: ExecutionPolicy
    ) -> OpenPosition | None:
        """The same-batch race: the setup registered AND left within one batch.

        The resolver filled (the outcome/terminal-drop row carries the honest
        entry) but the snapshot no longer holds the setup, so the fill and the
        policy-implied barriers are reconstructed from the row itself.
        """

        pid = prediction.prediction_id
        entry_price: float | None = None
        for outcome in update.outcomes:
            if outcome.prediction_id == pid:
                entry_price = outcome.entry_price
                break
        if entry_price is None:
            for drop in update.dropped:
                if (
                    drop.prediction_id == pid
                    and drop.reason in TERMINAL_DROP_REASONS
                    and drop.entry_price is not None
                ):
                    entry_price = drop.entry_price
                    break
        if entry_price is None:
            return None
        tick = policy.tick_size
        entry_ticks = round(entry_price / tick)
        tp_offset = round(policy.tp_points / tick)
        sl_offset = round(policy.sl_points / tick)
        is_long = prediction.direction == "long"
        self._count("opened_from_resolution")
        return self._build_position(
            prediction,
            policy,
            entry_ticks=entry_ticks,
            entry_ts_utc=prediction.event_ts_utc,
            tp_ticks=entry_ticks + tp_offset if is_long else entry_ticks - tp_offset,
            sl_ticks=entry_ticks - sl_offset if is_long else entry_ticks + sl_offset,
            source="resolution_fallback",
        )

    def _build_position(
        self,
        prediction: Prediction,
        policy: ExecutionPolicy,
        *,
        entry_ticks: int,
        entry_ts_utc: datetime,
        tp_ticks: int,
        sl_ticks: int,
        source: str,
    ) -> OpenPosition:
        is_long = prediction.direction == "long"
        return OpenPosition(
            prediction_id=prediction.prediction_id,
            touch_id=prediction.touch_id,
            direction=prediction.direction,
            contracts=1,
            entry_ts_utc=entry_ts_utc,
            entry_price_ticks=entry_ticks,
            # Conservative column: entry 1 tick adverse.
            entry_price_conservative_ticks=entry_ticks + 1 if is_long else entry_ticks - 1,
            tp_price_ticks=tp_ticks,
            sl_price_ticks=sl_ticks,
            tick_size=policy.tick_size,
            point_value=policy.point_value,
            session=prediction.session,
            level_kind=prediction.level_kind,
            bundle_id=prediction.model_id,
            mode=self._mode,
            source=source,
        )

    def _evict_if_over_cap(self) -> None:
        while len(self._open) > _OPEN_POSITION_CAP:
            evicted_pid = next(iter(self._open))
            del self._open[evicted_pid]
            self._count("evicted_open_positions")
            logger.warning("paper execution tracker evicted an open position (cap)")

    # -- close -----------------------------------------------------------------

    def _close_on_outcome(self, outcome: Outcome) -> ClosedExecution | None:
        position = self._open.pop(outcome.prediction_id, None)
        if position is None:
            return None  # ineligible or pre-tracker prediction — not ours
        if outcome.resolution_type is ResolutionType.TP_HIT:
            # tp exit at the barrier in BOTH columns: the excursion rule fires
            # only once a print reaches the barrier (resolution semantics).
            exit_ticks = position.tp_price_ticks
            exit_cons_ticks = position.tp_price_ticks
        else:  # SL_HIT
            exit_ticks = position.sl_price_ticks
            exit_cons_ticks = (
                position.sl_price_ticks - 1
                if position.is_long
                else position.sl_price_ticks + 1
            )
        return self._finalize_close(
            position,
            reason=outcome.resolution_type.value,
            exit_ticks=exit_ticks,
            exit_cons_ticks=exit_cons_ticks,
            exit_ts_utc=outcome.resolved_ts_utc,
        )

    def _close_on_drop(self, drop: DroppedPrediction) -> ClosedExecution | None:
        position = self._open.get(drop.prediction_id)
        if drop.reason in REGISTRATION_DROP_REASONS:
            if position is not None:
                # The resolver never filled — the tracker must never have
                # opened this. Invariant violation: counted, position removed
                # without synthesizing a fill that never existed.
                del self._open[drop.prediction_id]
                self._count("registration_drop_for_open_position")
                logger.warning(
                    "registration-time drop received for an open paper position"
                )
            return None
        if position is None:
            return None  # ineligible or pre-tracker — not ours
        del self._open[drop.prediction_id]
        exit_ticks = self._last_trade_price_ticks
        if exit_ticks is None:
            # No print ever observed (cannot happen after a real open): fall
            # back to the entry (flat close), counted.
            exit_ticks = position.entry_price_ticks
            self._count("drop_close_without_price")
        return self._finalize_close(
            position,
            reason=drop.reason,
            exit_ticks=exit_ticks,
            # A print is a print: no conservative exit adjustment on drop
            # closes; the conservative column still carries the adverse entry.
            exit_cons_ticks=exit_ticks,
            exit_ts_utc=self._last_event_ts_utc or drop.decision_ts_utc,
        )

    def _finalize_close(
        self,
        position: OpenPosition,
        *,
        reason: str,
        exit_ticks: int,
        exit_cons_ticks: int,
        exit_ts_utc: datetime | None,
    ) -> ClosedExecution:
        sign = 1 if position.is_long else -1
        points = (exit_ticks - position.entry_price_ticks) * sign * position.tick_size
        points_cons = (
            (exit_cons_ticks - position.entry_price_conservative_ticks)
            * sign
            * position.tick_size
        )
        execution = ClosedExecution(
            prediction_id=position.prediction_id,
            touch_id=position.touch_id,
            direction=position.direction,
            contracts=position.contracts,
            entry_ts_utc=position.entry_ts_utc,
            exit_ts_utc=exit_ts_utc,
            reason=reason,
            entry_price=position.entry_price,
            entry_price_conservative=position.entry_price_conservative,
            exit_price=exit_ticks * position.tick_size,
            exit_price_conservative=exit_cons_ticks * position.tick_size,
            points=points,
            points_conservative=points_cons,
            dollars=points * position.point_value * position.contracts,
            dollars_conservative=points_cons * position.point_value * position.contracts,
            point_value=position.point_value,
            session=position.session,
            level_kind=position.level_kind,
            bundle_id=position.bundle_id,
            mode=position.mode,
        )
        self._journal_close(execution)
        return execution

    # -- reset -------------------------------------------------------------------

    def _reset(self, reason: str) -> None:
        cleared = tuple(self._open)
        self._open.clear()
        if self._journal is not None:
            self._journal.record(
                self._last_event_ts_utc,
                {
                    "type": "reset",
                    "mode": self._mode,
                    "ts_utc": self._last_event_ts_utc,
                    "reason": reason,
                    "cleared": len(cleared),
                    "cleared_prediction_ids": list(cleared),
                },
            )
        # A fresh session gets a fresh clock and price frontier.
        self._last_trade_price_ticks = None
        self._last_event_ts_utc = None

    # -- persistence ---------------------------------------------------------------

    def _journal_open(self, position: OpenPosition) -> None:
        if self._journal is None:
            return
        self._journal.record(
            position.entry_ts_utc,
            {
                "type": "open",
                "mode": position.mode,
                "bundle_id": position.bundle_id,
                "ts_utc": position.entry_ts_utc,
                "prediction_id": position.prediction_id,
                "touch_id": position.touch_id,
                "direction": position.direction,
                "contracts": position.contracts,
                "point_value": position.point_value,
                "tick_size": position.tick_size,
                "entry_price": position.entry_price,
                "entry_price_conservative": position.entry_price_conservative,
                "tp_price": position.tp_price,
                "sl_price": position.sl_price,
                "session": position.session,
                "level_kind": position.level_kind,
                "source": position.source,
            },
        )

    def _journal_close(self, execution: ClosedExecution) -> None:
        if self._journal is None:
            return
        self._journal.record(
            execution.exit_ts_utc,
            {
                "type": "close",
                "mode": execution.mode,
                "bundle_id": execution.bundle_id,
                "ts_utc": execution.exit_ts_utc,
                "prediction_id": execution.prediction_id,
                "touch_id": execution.touch_id,
                "reason": execution.reason,
                "direction": execution.direction,
                "contracts": execution.contracts,
                "point_value": execution.point_value,
                "entry_ts_utc": execution.entry_ts_utc,
                "entry_price": execution.entry_price,
                "entry_price_conservative": execution.entry_price_conservative,
                "exit_price": execution.exit_price,
                "exit_price_conservative": execution.exit_price_conservative,
                "points": execution.points,
                "points_conservative": execution.points_conservative,
                "dollars": execution.dollars,
                "dollars_conservative": execution.dollars_conservative,
                "session": execution.session,
                "level_kind": execution.level_kind,
            },
        )

    def _count(self, key: str) -> None:
        self._anomalies[key] = self._anomalies.get(key, 0) + 1


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return str(value)
