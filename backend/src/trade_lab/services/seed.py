"""Build historical warm-up tick bars for the last N completed sessions.

On live start the chart should already show recent context instead of waiting for
147/987/2000 new trades to form the first bars. This service pulls the last few
completed sessions of historical front-month trades as a DataFrame and builds tick
bars **vectorized** (PyArrow/pandas), which is ~100x faster than per-trade Python and
keeps a multi-day warm-up to tens of seconds. It is intentionally synchronous/blocking
— callers run it off the event loop (e.g. ``asyncio.to_thread``).

``build_tick_bars_from_frame`` is a legacy warm-up helper for display context only;
authoritative live/replay runtime bars, sessions, levels, and touches are produced
by Strategy-Core.
"""

import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from trade_lab.adapters.databento_historical import DatabentoHistoricalSource
from trade_lab.domain.candles import Candle, CandleCloseReason, make_bar_id
from trade_lab.domain.prices import NQ_TICK_SIZE
from trade_lab.domain.sessions import CT, classify_session, to_ct

if TYPE_CHECKING:
    import pandas as pd

logger = logging.getLogger(__name__)

# Extra calendar days fetched beyond the requested lookback so weekends/holidays do
# not starve the window of N actual trading sessions.
_CALENDAR_PADDING_DAYS = 4

# Chicago seconds-of-day session boundaries (must match domain.sessions.classify_session):
# trades at/after 16:00 and before 18:00 CT are the closed maintenance window.
_SESSION_CLOSE_SOD = 16 * 3600  # 16:00 CT
_SESSION_OPEN_SOD = 18 * 3600  # 18:00 CT (next trading day begins)
_TICK_SIZE = float(NQ_TICK_SIZE)


def build_tick_bars_from_frame(
    frame: "pd.DataFrame", timeframes: tuple[int, ...]
) -> list[Candle]:
    """Build display seed bars from historical trades without starting live replay.

    ``frame`` must have ``ts_event`` (UTC), ``price`` (tick-aligned dollars) and
    ``size``. Trades in the 16:00-18:00 CT closed window are dropped for backward
    compatibility with existing chart warm-up behavior. The most recent session's
    trailing partial is emitted as an END_OF_DAY display bar.
    """

    import numpy as np
    import pandas as pd

    if frame is None or len(frame) == 0:
        return []

    ts = frame["ts_event"]
    if not pd.api.types.is_datetime64_any_dtype(ts):
        ts = pd.to_datetime(ts, utc=True, unit="ns")
    elif ts.dt.tz is None:
        ts = ts.dt.tz_localize("UTC")
    else:
        ts = ts.dt.tz_convert("UTC")

    price_ticks = np.rint(frame["price"].to_numpy(dtype="float64") / _TICK_SIZE).astype("int64")
    work = pd.DataFrame(
        {
            "ts_event": ts.to_numpy(),
            "price_ticks": price_ticks,
            "size": frame["size"].to_numpy(dtype="int64"),
        }
    )
    work["ts_event"] = pd.to_datetime(work["ts_event"], utc=True)

    ct = work["ts_event"].dt.tz_convert(CT)
    sod = ct.dt.hour * 3600 + ct.dt.minute * 60 + ct.dt.second
    cal_date = ct.dt.tz_localize(None).dt.floor("D")
    roll = (sod >= _SESSION_OPEN_SOD).astype("int64")
    work["trading_day"] = cal_date + pd.to_timedelta(roll, unit="D")
    closed = (sod >= _SESSION_CLOSE_SOD) & (sod < _SESSION_OPEN_SOD)

    work = work[~closed].sort_values("ts_event", kind="stable").reset_index(drop=True)
    if work.empty:
        return []

    day_groups = work.groupby("trading_day", sort=True)
    bars: list[Candle] = []
    for timeframe in sorted(set(timeframes)):
        work["bar_index"] = (day_groups.cumcount() // timeframe).to_numpy()
        agg = (
            work.groupby(["trading_day", "bar_index"], sort=True)
            .agg(
                open_ts=("ts_event", "first"),
                close_ts=("ts_event", "last"),
                open_ticks=("price_ticks", "first"),
                close_ticks=("price_ticks", "last"),
                high_ticks=("price_ticks", "max"),
                low_ticks=("price_ticks", "min"),
                volume=("size", "sum"),
                trade_count=("price_ticks", "size"),
            )
            .reset_index()
        )
        for row in agg.itertuples(index=False):
            trading_day = row.trading_day.date()
            bar_index = int(row.bar_index)
            complete = int(row.trade_count) == timeframe
            bars.append(
                Candle(
                    timeframe_ticks=timeframe,
                    trading_day=trading_day,
                    bar_index=bar_index,
                    bar_id=make_bar_id(timeframe, trading_day, bar_index),
                    open_ts_utc=row.open_ts.to_pydatetime(warn=False),
                    close_ts_utc=row.close_ts.to_pydatetime(warn=False),
                    open_ticks=int(row.open_ticks),
                    high_ticks=int(row.high_ticks),
                    low_ticks=int(row.low_ticks),
                    close_ticks=int(row.close_ticks),
                    volume=int(row.volume),
                    trade_count=int(row.trade_count),
                    is_complete=complete,
                    is_partial=not complete,
                    close_reason=(
                        CandleCloseReason.COMPLETE if complete else CandleCloseReason.END_OF_DAY
                    ),
                )
            )
    return bars


class HistoricalSeedService:
    """Produce the last N completed trading sessions of tick bars for live warm-up."""

    def __init__(
        self,
        source: DatabentoHistoricalSource,
        *,
        tick_timeframes: tuple[int, ...],
        lookback_days: int = 3,
        max_bars_per_timeframe: int = 2500,
        enabled: bool = True,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        if lookback_days <= 0:
            raise ValueError("lookback_days must be positive")
        if max_bars_per_timeframe <= 0:
            raise ValueError("max_bars_per_timeframe must be positive")
        self._source = source
        self._tick_timeframes = tick_timeframes
        self._lookback_days = lookback_days
        self._max_bars_per_timeframe = max_bars_per_timeframe
        self._enabled = enabled
        self._now = now_provider or (lambda: datetime.now(UTC))

    @property
    def enabled(self) -> bool:
        return self._enabled and self._source.available

    @property
    def lookback_days(self) -> int:
        return self._lookback_days

    def build_seed_bars(self) -> tuple[Candle, ...]:
        """Return closed bars for the last N completed sessions (newest sessions kept).

        Bars dated on/after the current trading day are excluded so seed bar ids never
        collide with the live engine's bars for the session live is about to stream.
        Returns an empty tuple (never raises) when seeding is disabled/unavailable or the
        fetch fails, so live start is never blocked by warm-up problems.
        """

        if not self.enabled:
            return ()
        now = self._now()
        cutoff_trading_day = self._current_trading_day(now)
        start = now - timedelta(days=self._lookback_days + _CALENDAR_PADDING_DAYS)
        try:
            frame = self._source.trades_frame(start=start, end=now)
            bars = build_tick_bars_from_frame(frame, self._tick_timeframes)
        except Exception:
            logger.warning("historical warm-up seeding failed; live will start without seed bars")
            return ()
        return self._select_recent_sessions(bars, cutoff_trading_day)

    def _current_trading_day(self, now: datetime) -> date:
        info = classify_session(now)
        return info.trading_day if info.trading_day is not None else to_ct(now).date()

    def _select_recent_sessions(
        self, bars: list[Candle], cutoff_trading_day: date
    ) -> tuple[Candle, ...]:
        eligible = [bar for bar in bars if bar.trading_day < cutoff_trading_day]
        if not eligible:
            return ()
        keep_days = set(sorted({bar.trading_day for bar in eligible})[-self._lookback_days :])
        per_timeframe: dict[int, list[Candle]] = defaultdict(list)
        for bar in eligible:
            if bar.trading_day in keep_days:
                per_timeframe[bar.timeframe_ticks].append(bar)
        selected: list[Candle] = []
        for tf_bars in per_timeframe.values():
            tf_bars.sort(key=lambda bar: (bar.trading_day, bar.bar_index))
            selected.extend(tf_bars[-self._max_bars_per_timeframe :])
        selected.sort(key=lambda bar: (bar.trading_day, bar.bar_index, bar.timeframe_ticks))
        return tuple(selected)
