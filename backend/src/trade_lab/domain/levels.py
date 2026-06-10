"""Level/touch DTO/display types. Level DERIVATION lives in Strategy-Core (D2).

The local session-level-engine shadow implementation (session range tracking,
PDH/PDL rolls, exact-price touch detection) was deleted in D2: authoritative
levels and touches are produced by Strategy-Core's plugin-owned level state and
mapped into these compatibility types at the adapter seam
(``strategy_core_service``).
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from trade_lab.domain.sessions import SessionName


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
