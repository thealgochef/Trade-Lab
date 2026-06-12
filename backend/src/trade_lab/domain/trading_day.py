"""ET trading-day clock helpers (18:00 America/New_York roll).

D-P-06 anchors live warm-start, reconnect recovery, and the prediction journal
on the canonical 18:00 ET trading-day boundary — the same boundary
Strategy-Core's v3 session scheme uses. ``prior_trading_day`` skips weekends so
a Monday looks back to Friday; exchange holidays are deliberately not modeled
(an empty fetch is surfaced by the caller instead of silently guessed around).
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
TRADING_DAY_BOUNDARY = time(18, 0)


def trading_day_for(ts_utc: datetime) -> date:
    """The trading day a UTC instant belongs to: ``[prev 18:00 ET, 18:00 ET)``."""

    local = ts_utc.astimezone(ET)
    day = local.date()
    if local.time() >= TRADING_DAY_BOUNDARY:
        day += timedelta(days=1)
    return day


def trading_day_start_utc(trading_day: date) -> datetime:
    """The UTC instant trading day ``D`` opens: ``(D - 1) 18:00 ET``."""

    local = datetime.combine(
        trading_day - timedelta(days=1), TRADING_DAY_BOUNDARY, tzinfo=ET
    )
    return local.astimezone(UTC)


def most_recent_session_open_utc(now_utc: datetime) -> datetime:
    """The most recent 18:00 ET at/before ``now_utc`` (the live warm-start anchor)."""

    return trading_day_start_utc(trading_day_for(now_utc))


def prior_trading_day(trading_day: date) -> date:
    """The previous Mon-Fri calendar date (Friday for a Monday trading day)."""

    day = trading_day - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def trading_day_bounds_utc(trading_day: date) -> tuple[datetime, datetime]:
    """``[start, end)`` UTC bounds of one trading day (prev 18:00 ET → 18:00 ET)."""

    return trading_day_start_utc(trading_day), trading_day_start_utc(
        trading_day + timedelta(days=1)
    )
