"""Chicago trading-day and session calendar utilities."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")


class SessionName(StrEnum):
    ASIA = "asia"
    LONDON = "london"
    NY = "ny"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class SessionInfo:
    trading_day: date | None
    session: SessionName
    local_ts: datetime

    @property
    def is_open(self) -> bool:
        return self.session is not SessionName.CLOSED


def to_ct(ts_utc: datetime) -> datetime:
    if ts_utc.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return ts_utc.astimezone(UTC).astimezone(CT)


def classify_session(ts_utc: datetime) -> SessionInfo:
    """Classify an event timestamp using wall-clock CT boundaries and zoneinfo.

    The label is the date of the 4 PM CT close. Boundaries are end-exclusive so
    exactly 2:00 belongs to London, 8:00 to NY, and 4:00 PM is closed.
    """

    local_ts = to_ct(ts_utc)
    local_time = local_ts.time()
    if local_time >= time(18, 0):
        return SessionInfo(local_ts.date() + timedelta(days=1), SessionName.ASIA, local_ts)
    if local_time < time(2, 0):
        return SessionInfo(local_ts.date(), SessionName.ASIA, local_ts)
    if local_time < time(8, 0):
        return SessionInfo(local_ts.date(), SessionName.LONDON, local_ts)
    if local_time < time(16, 0):
        return SessionInfo(local_ts.date(), SessionName.NY, local_ts)
    return SessionInfo(None, SessionName.CLOSED, local_ts)


class SessionClassifier:
    """Cache session classification for monotonic high-frequency event streams.

    Domain engines only need the trading day and session name on their per-event hot
    path.  The public ``classify_session`` function remains the source of truth and
    still returns an exact ``local_ts`` for callers that need it; this helper reuses
    its result for subsequent events in the same UTC minute, which is safe because all
    supported session boundaries are minute-aligned.
    """

    __slots__ = (
        "_minute_end_utc",
        "_minute_start_utc",
        "_session",
        "_trading_day",
    )

    def __init__(self) -> None:
        self._minute_start_utc: datetime | None = None
        self._minute_end_utc: datetime | None = None
        self._session = SessionName.CLOSED
        self._trading_day: date | None = None

    def classify(self, ts_utc: datetime) -> tuple[date | None, SessionName]:
        minute_start = self._minute_start_utc
        minute_end = self._minute_end_utc
        if (
            minute_start is not None
            and minute_end is not None
            and minute_start <= ts_utc < minute_end
        ):
            return self._trading_day, self._session

        info = classify_session(ts_utc)
        self._minute_start_utc = info.local_ts.astimezone(UTC).replace(second=0, microsecond=0)
        self._minute_end_utc = self._minute_start_utc + timedelta(minutes=1)
        self._trading_day = info.trading_day
        self._session = info.session
        return info.trading_day, info.session
