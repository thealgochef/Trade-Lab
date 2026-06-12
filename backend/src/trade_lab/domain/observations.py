"""Observation lifecycle foundation.

Observations start from validated touches and reserve state for future feature/ML
work without importing inference, risk, or execution concerns.
"""

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from uuid import uuid4

from trade_lab.domain.levels import LevelDirection, LevelKind, TouchEvent
from trade_lab.domain.sessions import SessionName


class ObservationStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class Observation:
    observation_id: str
    originating_touch_id: str
    start_ts_utc: datetime
    scheduled_end_ts_utc: datetime
    status: ObservationStatus
    trading_day: date
    session: SessionName
    level_kind: LevelKind
    level_price_ticks: int
    # audit #NN-1: carried authoritative touch direction (None for legacy touches that
    # predate direction carry); inference consumes this instead of re-deriving from
    # level_kind so mixed-side merged zones are not inverted.
    direction: LevelDirection | None = None
    # W1 P3c: exact zone representative price (points, no tick snap) carried from
    # the touch; inference prefers this over the snapped ticks.
    level_price: float | None = None


class ObservationEngine:
    def __init__(self, duration: timedelta = timedelta(minutes=5)) -> None:
        if duration.total_seconds() <= 0:
            raise ValueError("observation duration must be positive")
        self.duration = duration
        self._observations: dict[str, Observation] = {}

    def start_from_touch(self, touch: TouchEvent) -> Observation:
        obs = Observation(
            observation_id=str(uuid4()),
            originating_touch_id=touch.touch_id,
            start_ts_utc=touch.event_ts_utc,
            scheduled_end_ts_utc=touch.event_ts_utc + self.duration,
            status=ObservationStatus.ACTIVE,
            trading_day=touch.trading_day,
            session=touch.session,
            level_kind=touch.level_kind,
            level_price_ticks=touch.level_price_ticks,
            direction=touch.direction,  # audit #NN-1: carry authoritative direction
            level_price=touch.level_price,  # W1 P3c: exact reference, no tick snap
        )
        self._observations[obs.observation_id] = obs
        return obs

    def active(self) -> tuple[Observation, ...]:
        return tuple(
            obs for obs in self._observations.values() if obs.status == ObservationStatus.ACTIVE
        )

    def refresh(self, now_utc: datetime) -> tuple[Observation, ...]:
        """Expire active observations whose scheduled window has elapsed."""

        if now_utc.tzinfo is None:
            raise ValueError("observation refresh timestamp must be timezone-aware UTC datetime")
        now_utc = now_utc.astimezone(UTC)
        changed: list[Observation] = []
        for observation_id, obs in tuple(self._observations.items()):
            if obs.status != ObservationStatus.ACTIVE or now_utc < obs.scheduled_end_ts_utc:
                continue
            expired = replace(obs, status=ObservationStatus.EXPIRED)
            self._observations[observation_id] = expired
            changed.append(expired)
        return tuple(changed)
