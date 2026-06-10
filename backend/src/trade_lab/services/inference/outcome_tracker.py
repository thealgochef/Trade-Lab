"""MAE-first forward-outcome tracking for open predictions.

Each open :class:`Prediction` is registered with the tracker; the tracker then
consumes CLOSED bars of the contract's ``label_policy.forward_bar_type`` (e.g.
``147t``) that occur AFTER the touch, maintaining the running max MFE/MAE in
points versus the level reference price by direction. Resolution mirrors the
offline ``dashboard_utility_labeling`` ladder exactly: MAE is checked FIRST each
bar, so a single bar whose range satisfies BOTH the stop and the target resolves
to the LOSS (trap/blowthrough), never the win.

The forward scan is capped at the RTH close (``label_policy.forward_cutoff``,
16:15 ET). A bar that closes at or after that cutoff forces a ``session_end``
resolution using the same MAE-first classification.

This module is pure Python: it never loads CatBoost or reads market bytes. It
holds no filesystem paths and emits only the path-free :class:`Outcome`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from strategy_core import StrategyContract

from trade_lab.domain.candles import Candle
from trade_lab.domain.outcomes import Outcome, ResolutionType
from trade_lab.services.inference.inference_engine import Prediction

_BAR_TYPE_RE = re.compile(r"^(\d+)t$")


def _parse_bar_type(bar_type: str) -> int:
    """Map a contract ``bar_type`` like ``147t`` to its tick count ``147``."""

    match = _BAR_TYPE_RE.match(bar_type.strip().lower())
    if match is None:
        raise ValueError(f"unsupported forward_bar_type {bar_type!r}; expected '<n>t'")
    return int(match.group(1))


def _parse_cutoff(forward_cutoff: str) -> tuple[time, ZoneInfo]:
    """Parse ``"16:15_US/Eastern_rth_close"`` into a wall-clock time + timezone.

    The cutoff string packs ``HH:MM``, an IANA timezone, and a descriptive tail
    separated by underscores. Only the first two tokens are semantically used; the
    tail (``rth_close``) is purely documentation.
    """

    parts = forward_cutoff.split("_")
    if len(parts) < 2:
        raise ValueError(f"unsupported forward_cutoff {forward_cutoff!r}")
    hour_text, minute_text = parts[0].split(":", 1)
    cutoff_time = time(int(hour_text), int(minute_text))
    return cutoff_time, ZoneInfo(parts[1])


@dataclass(slots=True)
class _OpenTracker:
    """Mutable running MFE/MAE state for one open prediction."""

    prediction: Prediction
    entry_points: float
    is_long: bool
    max_mfe_pts: float = 0.0
    max_mae_pts: float = 0.0
    bars_seen: int = 0


class OutcomeTracker:
    """Resolve open predictions against contract forward bars (MAE-first).

    One tracker instance serves one runtime/session. ``register`` enrolls a
    prediction; ``on_bar_close`` advances every open tracker with a just-closed bar
    and returns the outcomes that resolved on it. ``reset`` drops all open state
    (called on runtime reset and model hot-swap so bundles never mix).
    """

    def __init__(self, contract: StrategyContract) -> None:
        policy = contract.label_policy
        self._forward_timeframe_ticks = _parse_bar_type(policy.forward_bar_type)
        self._tick_size = Decimal(str(contract.tick_size))
        self._tp_points = float(policy.tp_points)
        self._sl_points = float(policy.sl_points)
        self._trap_mfe_min = float(policy.trap_mfe_min)
        self._cutoff_time, self._cutoff_tz = _parse_cutoff(policy.forward_cutoff)
        self._open: list[_OpenTracker] = []

    @property
    def forward_timeframe_ticks(self) -> int:
        return self._forward_timeframe_ticks

    @property
    def open_count(self) -> int:
        return len(self._open)

    def register(self, prediction: Prediction) -> None:
        """Begin tracking a prediction's forward outcome from its touch.

        Entry reference is the level's representative price (``level_price_ticks``
        in points), matching the training labels.
        """

        entry_points = float(Decimal(prediction.level_price_ticks) * self._tick_size)
        self._open.append(
            _OpenTracker(
                prediction=prediction,
                entry_points=entry_points,
                is_long=prediction.direction.lower() == "long",
            )
        )

    def on_bar_close(self, bar: Candle) -> tuple[Outcome, ...]:
        """Advance open trackers with one just-closed bar; emit any resolutions.

        Only bars of the contract's forward timeframe that close strictly after a
        prediction's touch advance that prediction. A bar that closes at or after
        the RTH cutoff forces a ``session_end`` resolution for every still-open
        prediction it touches.
        """

        if bar.timeframe_ticks != self._forward_timeframe_ticks:
            return ()

        high_points = float(Decimal(bar.high_ticks) * self._tick_size)
        low_points = float(Decimal(bar.low_ticks) * self._tick_size)
        at_or_after_cutoff = self._is_at_or_after_cutoff(bar.close_ts_utc)

        resolved: list[Outcome] = []
        still_open: list[_OpenTracker] = []
        for tracker in self._open:
            # A bar only counts if it closed after the touch (touch bar excluded).
            if bar.close_ts_utc <= tracker.prediction.event_ts_utc:
                still_open.append(tracker)
                continue

            if tracker.is_long:
                bar_mfe = high_points - tracker.entry_points
                bar_mae = tracker.entry_points - low_points
            else:
                bar_mfe = tracker.entry_points - low_points
                bar_mae = high_points - tracker.entry_points

            tracker.max_mfe_pts = max(tracker.max_mfe_pts, bar_mfe)
            tracker.max_mae_pts = max(tracker.max_mae_pts, bar_mae)
            tracker.bars_seen += 1

            outcome = self._classify(tracker, bar.close_ts_utc, forced=at_or_after_cutoff)
            if outcome is None:
                still_open.append(tracker)
            else:
                resolved.append(outcome)

        self._open = still_open
        return tuple(resolved)

    def _classify(
        self, tracker: _OpenTracker, resolved_ts_utc: datetime, *, forced: bool
    ) -> Outcome | None:
        """Apply the MAE-first ladder; return an Outcome only when resolved.

        MAE is checked first so a bar that breaches both the stop and the target
        resolves to the loss. ``forced`` (RTH cutoff) resolves the prediction even
        when neither threshold is hit, using the same trap/blowthrough split.
        """

        if tracker.max_mae_pts >= self._sl_points:
            actual = (
                "trap_reversal"
                if tracker.max_mfe_pts >= self._trap_mfe_min
                else "aggressive_blowthrough"
            )
            return self._build(tracker, ResolutionType.SL_HIT, actual, resolved_ts_utc)

        if tracker.max_mfe_pts >= self._tp_points:
            return self._build(
                tracker, ResolutionType.TP_HIT, "tradeable_reversal", resolved_ts_utc
            )

        if forced:
            actual = (
                "trap_reversal"
                if tracker.max_mfe_pts >= self._trap_mfe_min
                else "aggressive_blowthrough"
            )
            return self._build(tracker, ResolutionType.SESSION_END, actual, resolved_ts_utc)

        return None

    def _build(
        self,
        tracker: _OpenTracker,
        resolution_type: ResolutionType,
        actual_class: str,
        resolved_ts_utc: datetime,
    ) -> Outcome:
        predicted = tracker.prediction.predicted_class
        return Outcome(
            outcome_id=str(uuid4()),
            prediction_id=tracker.prediction.prediction_id,
            touch_id=tracker.prediction.touch_id,
            resolution_type=resolution_type,
            actual_class=actual_class,
            predicted_class=predicted,
            correct=predicted == actual_class,
            max_mfe_pts=round(tracker.max_mfe_pts, 4),
            max_mae_pts=round(tracker.max_mae_pts, 4),
            bars_to_resolution=tracker.bars_seen,
            resolved_ts_utc=resolved_ts_utc,
        )

    def _is_at_or_after_cutoff(self, ts_utc: datetime) -> bool:
        local = ts_utc.astimezone(self._cutoff_tz)
        return local.time() >= self._cutoff_time

    def reset(self) -> None:
        """Drop all open-prediction state (runtime reset / model hot-swap)."""

        self._open.clear()
