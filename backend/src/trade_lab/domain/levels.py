"""Session levels, eligibility, and exact-price touches.

The engine consumes trades, not candles, because timeframe selection must never alter
authoritative highs/lows or touch timing.
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import uuid4

from trade_lab.domain.events import TradeEvent
from trade_lab.domain.sessions import SessionClassifier, SessionName


class LevelKind(StrEnum):
    PDH = "pdh"
    PDL = "pdl"
    ASIA_HIGH = "asia_high"
    ASIA_LOW = "asia_low"
    LONDON_HIGH = "london_high"
    LONDON_LOW = "london_low"
    NY_HIGH = "ny_high"
    NY_LOW = "ny_low"


class LevelDirection(StrEnum):
    """Trade direction implied by which side of the level was touched.

    audit #NN-1: this is the authoritative direction Strategy-Core resolves on the
    MERGED ZONE side (low touch -> long, high touch -> short) and carries on
    ``Touch.direction``. It must be carried through the touch -> observation ->
    inference path rather than re-derived from ``level_kind`` (= ``zone.names[0]``,
    the lowest-priced constituent), which inverts for mixed-side merged zones. The
    ``long``/``short`` values match the inference ``LevelDirection`` convention so
    the service layer maps between the two by value with no translation table.
    """

    LONG = "long"
    SHORT = "short"


SESSION_LEVELS: dict[SessionName, tuple[LevelKind, LevelKind]] = {
    SessionName.ASIA: (LevelKind.ASIA_HIGH, LevelKind.ASIA_LOW),
    SessionName.LONDON: (LevelKind.LONDON_HIGH, LevelKind.LONDON_LOW),
    SessionName.NY: (LevelKind.NY_HIGH, LevelKind.NY_LOW),
}

LEVEL_ORIGIN: dict[LevelKind, SessionName | None] = {
    LevelKind.PDH: None,
    LevelKind.PDL: None,
    LevelKind.ASIA_HIGH: SessionName.ASIA,
    LevelKind.ASIA_LOW: SessionName.ASIA,
    LevelKind.LONDON_HIGH: SessionName.LONDON,
    LevelKind.LONDON_LOW: SessionName.LONDON,
    LevelKind.NY_HIGH: SessionName.NY,
    LevelKind.NY_LOW: SessionName.NY,
}


@dataclass(frozen=True, slots=True)
class DisplayLevel:
    kind: LevelKind
    price_ticks: int
    trading_day: date
    origin_session: SessionName | None
    is_developing: bool
    is_eligible: bool


@dataclass(frozen=True, slots=True)
class TouchEvent:
    touch_id: str
    event_ts_utc: datetime
    trading_day: date
    session: SessionName
    level_kind: LevelKind
    level_price_ticks: int
    trade_price_ticks: int
    requested_symbol: str
    raw_symbol: str | None
    instrument_id: int | None
    created_observation: bool = True
    sequence_in_session: int = 1
    # audit #NN-1: authoritative direction carried from Strategy-Core's Touch.direction.
    # Defaults to None so existing constructions keep working; the adapter always sets it.
    direction: LevelDirection | None = None


@dataclass(frozen=True, slots=True)
class LevelUpdate:
    display_levels: tuple[DisplayLevel, ...]
    touches: tuple[TouchEvent, ...]


@dataclass(slots=True)
class _SessionRange:
    high: int | None = None
    low: int | None = None

    def update(self, price_ticks: int) -> bool:
        changed = False
        if self.high is None or price_ticks > self.high:
            self.high = price_ticks
            changed = True
        if self.low is None or price_ticks < self.low:
            self.low = price_ticks
            changed = True
        return changed


@dataclass(frozen=True, slots=True)
class _DaySummary:
    high: int
    low: int


class SessionLevelEngine:
    """Maintain display levels and detect valid exact-price touches."""

    def __init__(self) -> None:
        self._trading_day: date | None = None
        self._session: SessionName | None = None
        self._day_high: int | None = None
        self._day_low: int | None = None
        self._prior_day_high: int | None = None
        self._prior_day_low: int | None = None
        self._completed_day_summaries: dict[date, _DaySummary] = {}
        self._ranges: dict[SessionName, _SessionRange] = {
            SessionName.ASIA: _SessionRange(),
            SessionName.LONDON: _SessionRange(),
            SessionName.NY: _SessionRange(),
        }
        self._touched: set[tuple[date, SessionName, LevelKind]] = set()
        self._touch_counts: dict[tuple[date, SessionName], int] = {}
        self._session_classifier = SessionClassifier()
        self._display_cache_session: SessionName | None = None
        self._display_cache: tuple[DisplayLevel, ...] | None = None

    def process_trade(self, trade: TradeEvent) -> LevelUpdate:
        trading_day, session = self._session_classifier.classify(trade.event_ts_utc)
        if trading_day is None or session is SessionName.CLOSED:
            return LevelUpdate(self.display_levels(current_session=SessionName.CLOSED), ())

        self._roll_if_needed(trading_day, session)
        touches = self._detect_touches(trade, trading_day, session)

        # Update after touch detection so same-session new highs/lows do not create
        # their own signal touch while the level is still developing.
        display_changed = False
        if self._day_high is None or trade.price_ticks > self._day_high:
            self._day_high = trade.price_ticks
            display_changed = True
        if self._day_low is None or trade.price_ticks < self._day_low:
            self._day_low = trade.price_ticks
            display_changed = True
        display_changed = self._ranges[session].update(trade.price_ticks) or display_changed
        if display_changed:
            self._clear_display_cache()
        return LevelUpdate(self.display_levels(current_session=session), touches)

    def display_levels(
        self, current_session: SessionName | None = None
    ) -> tuple[DisplayLevel, ...]:
        if self._trading_day is None:
            return ()
        if self._display_cache_session is current_session and self._display_cache is not None:
            return self._display_cache
        levels: list[DisplayLevel] = []
        if self._prior_day_high is not None:
            levels.append(self._level(LevelKind.PDH, self._prior_day_high, current_session))
        if self._prior_day_low is not None:
            levels.append(self._level(LevelKind.PDL, self._prior_day_low, current_session))
        for session, (high_kind, low_kind) in SESSION_LEVELS.items():
            rng = self._ranges[session]
            if rng.high is not None:
                levels.append(self._level(high_kind, rng.high, current_session))
            if rng.low is not None:
                levels.append(self._level(low_kind, rng.low, current_session))
        display_levels = tuple(levels)
        self._display_cache_session = current_session
        self._display_cache = display_levels
        return display_levels

    def finalize_trading_day(self, trading_day: date) -> None:
        """Mark an observed trading day complete and eligible for future PDH/PDL.

        Partial snippets are deliberately not promoted during automatic day rollover.
        Callers must invoke this once they know the observed stream spans the full
        trading day, or use ``load_prior_day_summary`` for a known complete summary.
        """

        if self._trading_day != trading_day:
            raise ValueError("can only finalize the currently observed trading day")
        if self._day_high is None or self._day_low is None:
            raise ValueError("cannot finalize a trading day with no observed trades")
        self._completed_day_summaries[trading_day] = _DaySummary(self._day_high, self._day_low)
        self._apply_prior_day_summary_for_current_day()

    def load_prior_day_summary(self, trading_day: date, high_ticks: int, low_ticks: int) -> None:
        """Load a known complete trading-day high/low summary for PDH/PDL use."""

        if high_ticks < low_ticks:
            raise ValueError("prior day high_ticks must be greater than or equal to low_ticks")
        self._completed_day_summaries[trading_day] = _DaySummary(high_ticks, low_ticks)
        self._apply_prior_day_summary_for_current_day()

    def _level(
        self, kind: LevelKind, price_ticks: int, current_session: SessionName | None
    ) -> DisplayLevel:
        origin = LEVEL_ORIGIN[kind]
        developing = origin is not None and origin == current_session
        eligible = current_session not in (None, SessionName.CLOSED) and not developing
        return DisplayLevel(
            kind=kind,
            price_ticks=price_ticks,
            trading_day=self._trading_day,  # type: ignore[arg-type]
            origin_session=origin,
            is_developing=developing,
            is_eligible=eligible,
        )

    def _roll_if_needed(self, trading_day: date, session: SessionName) -> None:
        if self._trading_day != trading_day:
            self._trading_day = trading_day
            self._session = None
            self._day_high = None
            self._day_low = None
            self._prior_day_high = None
            self._prior_day_low = None
            self._apply_prior_day_summary_for_current_day()
            self._ranges = {name: _SessionRange() for name in SESSION_LEVELS}
            self._touched.clear()
            self._touch_counts.clear()
            self._clear_display_cache()
        if self._session != session:
            self._session = session
            self._clear_display_cache()
            # One touch per level per session: touch keys include session, so no global
            # level state is cleared here.

    def _apply_prior_day_summary_for_current_day(self) -> None:
        if self._trading_day is None:
            return
        prior_trading_day = max(
            (
                trading_day
                for trading_day in self._completed_day_summaries
                if trading_day < self._trading_day
            ),
            default=None,
        )
        if prior_trading_day is None:
            return
        summary = self._completed_day_summaries[prior_trading_day]
        self._prior_day_high = summary.high
        self._prior_day_low = summary.low
        self._clear_display_cache()

    def _clear_display_cache(self) -> None:
        self._display_cache_session = None
        self._display_cache = None

    def _detect_touches(
        self, trade: TradeEvent, trading_day: date, session: SessionName
    ) -> tuple[TouchEvent, ...]:
        touches: list[TouchEvent] = []
        for kind, level_price_ticks in self._eligible_level_prices(session):
            key = (trading_day, session, kind)
            if key in self._touched:
                continue
            if trade.price_ticks != level_price_ticks:
                continue
            self._touched.add(key)
            count_key = (trading_day, session)
            sequence = self._touch_counts.get(count_key, 0) + 1
            self._touch_counts[count_key] = sequence
            touches.append(
                TouchEvent(
                    touch_id=str(uuid4()),
                    event_ts_utc=trade.event_ts_utc,
                    trading_day=trading_day,
                    session=session,
                    level_kind=kind,
                    level_price_ticks=level_price_ticks,
                    trade_price_ticks=trade.price_ticks,
                    requested_symbol=trade.requested_symbol,
                    raw_symbol=trade.raw_symbol,
                    instrument_id=trade.instrument_id,
                    sequence_in_session=sequence,
                )
            )
        return tuple(touches)

    def _eligible_level_prices(
        self, current_session: SessionName
    ) -> tuple[tuple[LevelKind, int], ...]:
        levels: list[tuple[LevelKind, int]] = []
        if self._prior_day_high is not None:
            levels.append((LevelKind.PDH, self._prior_day_high))
        if self._prior_day_low is not None:
            levels.append((LevelKind.PDL, self._prior_day_low))
        for session, (high_kind, low_kind) in SESSION_LEVELS.items():
            if session is current_session:
                continue
            rng = self._ranges[session]
            if rng.high is not None:
                levels.append((high_kind, rng.high))
            if rng.low is not None:
                levels.append((low_kind, rng.low))
        return tuple(levels)
