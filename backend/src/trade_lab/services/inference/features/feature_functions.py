"""The six contract features as a ``name -> FeatureFn`` registry.

Each :data:`FeatureFn` has the signature ``(buffer, window, level_ctx) -> float``
and reads only the L1/L0 :class:`MarketContextBuffer`. Prices are integer ticks
in the buffer; level comparisons are done in *points* (``ticks * tick_size``) to
match the ported research thresholds (within-band 2.0 pts, proximity 0.5 pts).

Empty-case rules differ per feature and are part of the contract parity surface:

* interaction time features -> ``0.0`` when the window holds no usable events;
* ``int_absorption_ratio`` -> ``0.0`` when total band volume is zero;
* every approach feature -> ``NaN`` when its inputs are absent.

``build_feature_vector`` assembles the values strictly in
``feature_set.names`` order, substituting ``np.nan`` only where a feature's own
rule yields it (never a 0.0 sentinel for missing approach data).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from strategy_core import StrategyContract

    from trade_lab.domain.market_context import (
        BufferedQuote,
        MarketContextBuffer,
    )

# The research time-tempo loop discards inter-event gaps that are negative
# (out-of-order) or longer than this many seconds (a data gap, not dwell time).
_MAX_DWELL_GAP_SECONDS = 600.0
# Hardcoded within-band half-width for ``int_time_within_2pts`` (points). This is
# deliberately NOT level_proximity_pts: the research feature pins it at 2.0.
_WITHIN_BAND_PTS = 2.0


class LevelDirection(StrEnum):
    """Trade direction implied by which side of the level was touched."""

    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True, slots=True)
class LevelContext:
    """The level a touch fired against, carried into every feature function.

    ``reference_price`` is the level's representative price as an exact Decimal
    (converted from ticks upstream). ``tick_size`` lets functions translate
    buffered tick prices into the points used by the research thresholds. The
    proximity band and large-trade threshold are contract parameters threaded
    through here so a :data:`FeatureFn` keeps its uniform 3-arg signature and
    reads no global config.
    """

    reference_price: Decimal
    direction: LevelDirection
    tick_size: Decimal
    proximity_points: float = 0.5
    large_trade_threshold: int = 10

    @property
    def reference_points(self) -> float:
        return float(self.reference_price)

    def ticks_to_points(self, price_ticks: float) -> float:
        return price_ticks * float(self.tick_size)

    @classmethod
    def from_contract(
        cls,
        contract: StrategyContract,
        section,
        *,
        reference_price: Decimal,
        direction: LevelDirection,
    ) -> LevelContext:
        """Build a context from a touch level and the active bundle's bands.

        E3: ``feature_windows`` is section-bound — the bands read the typed
        ``section`` (duck-typed touch-section access; recorded coupling), while
        ``tick_size`` stays an envelope read.
        """

        windows = section.feature_windows
        return cls(
            reference_price=reference_price,
            direction=direction,
            tick_size=Decimal(str(contract.tick_size)),
            proximity_points=windows.level_proximity_pts,
            large_trade_threshold=windows.large_trade_threshold,
        )


@dataclass(frozen=True, slots=True)
class FeatureWindow:
    """The interaction and approach windows for a single touch.

    Both are ``[start, end)`` half-open to match the buffer's window slices.
    Interaction is post-touch ``[touch, touch + interaction)``; approach is
    pre-touch ``[touch - approach, touch)``.
    """

    interaction_start: datetime
    interaction_end: datetime
    approach_start: datetime
    approach_end: datetime

    @classmethod
    def from_touch(
        cls,
        touch_ts_utc: datetime,
        *,
        interaction_window: timedelta,
        approach_window: timedelta,
    ) -> FeatureWindow:
        return cls(
            interaction_start=touch_ts_utc,
            interaction_end=touch_ts_utc + interaction_window,
            approach_start=touch_ts_utc - approach_window,
            approach_end=touch_ts_utc,
        )


# A feature function maps (buffer, window, level_ctx) -> a single float value.
FeatureFn = Callable[
    ["MarketContextBuffer", FeatureWindow, LevelContext], float
]


def _interaction_quotes(
    buffer: MarketContextBuffer, window: FeatureWindow
) -> tuple[BufferedQuote, ...]:
    return buffer.quotes_in_window(window.interaction_start, window.interaction_end)


def _quote_mid_points(quote: BufferedQuote, level_ctx: LevelContext) -> float:
    mid_ticks = (quote.bid_price_ticks + quote.ask_price_ticks) / 2
    return level_ctx.ticks_to_points(mid_ticks)


def _accumulated_dwell(
    quotes: tuple[BufferedQuote, ...],
    level_ctx: LevelContext,
    predicate: Callable[[float, float], bool],
) -> float:
    """Sum the dwell time (seconds) over consecutive quotes where ``predicate``.

    The dwell for the gap ``[quotes[j], quotes[j+1])`` is attributed to the mid
    price *at* ``quotes[j]`` (the price held during that gap), mirroring the
    research tempo loop. Gaps that are negative or exceed the max-dwell ceiling
    are skipped as bad/absent data, not dwell.
    """

    total = 0.0
    level_points = level_ctx.reference_points
    for current, nxt in pairwise(quotes):
        dt_seconds = (nxt.event_ts_utc - current.event_ts_utc).total_seconds()
        if dt_seconds < 0 or dt_seconds > _MAX_DWELL_GAP_SECONDS:
            continue
        mid_points = _quote_mid_points(current, level_ctx)
        if predicate(mid_points, level_points):
            total += dt_seconds
    return total


def int_time_beyond_level(
    buffer: MarketContextBuffer, window: FeatureWindow, level_ctx: LevelContext
) -> float:
    """Seconds the mid spent on the adverse side of the level. 0.0 if no events.

    Adverse is below the level for a LONG (price ran past support) and above it
    for a SHORT (price ran past resistance).
    """

    quotes = _interaction_quotes(buffer, window)

    if level_ctx.direction is LevelDirection.LONG:
        def predicate(mid: float, level: float) -> bool:
            return mid < level
    else:
        def predicate(mid: float, level: float) -> bool:
            return mid > level

    return _accumulated_dwell(quotes, level_ctx, predicate)


def int_time_within_2pts(
    buffer: MarketContextBuffer, window: FeatureWindow, level_ctx: LevelContext
) -> float:
    """Seconds the mid stayed within 2.0 points of the level. 0.0 if no events.

    The 2.0-point band is hardcoded (it is *not* ``level_proximity_pts``).
    """

    quotes = _interaction_quotes(buffer, window)

    def predicate(mid: float, level: float) -> bool:
        return abs(mid - level) <= _WITHIN_BAND_PTS

    return _accumulated_dwell(quotes, level_ctx, predicate)


def int_absorption_ratio(
    buffer: MarketContextBuffer, window: FeatureWindow, level_ctx: LevelContext
) -> float:
    """``at_level_vol / (at_level_vol + through_vol)``. 0.0 if total is zero.

    ``at_level`` is trade volume within +/- ``level_proximity_pts`` of the level;
    ``through`` is adverse-direction volume strictly beyond that band. The result
    is clamped to ``[0, 1]``.
    """

    trades = buffer.trades_in_window(window.interaction_start, window.interaction_end)
    proximity = level_ctx.proximity_points
    level_points = level_ctx.reference_points
    low = level_points - proximity
    high = level_points + proximity

    at_level_vol = 0.0
    through_vol = 0.0
    is_long = level_ctx.direction is LevelDirection.LONG
    for trade in trades:
        price_points = level_ctx.ticks_to_points(trade.price_ticks)
        size = float(trade.size)
        if low <= price_points <= high:
            at_level_vol += size
        elif (is_long and price_points < level_points) or (
            not is_long and price_points > level_points
        ):
            through_vol += size

    total = at_level_vol + through_vol
    if total <= 0:
        return 0.0
    return min(1.0, max(0.0, at_level_vol / total))


def app_large_trade_vol_pct(
    buffer: MarketContextBuffer, window: FeatureWindow, level_ctx: LevelContext
) -> float:
    """Large-trade volume share: ``sum(size>=threshold) / sum(size)``. NaN if no trades."""

    trades = buffer.trades_in_window(window.approach_start, window.approach_end)
    total = 0.0
    large = 0.0
    threshold = level_ctx.large_trade_threshold
    for trade in trades:
        size = float(trade.size)
        total += size
        if trade.size >= threshold:
            large += size
    if total <= 0:
        return float(np.nan)
    return large / total


def app_avg_trade_size(
    buffer: MarketContextBuffer, window: FeatureWindow, level_ctx: LevelContext
) -> float:
    """Mean trade size over the approach window. NaN if no trades."""

    trades = buffer.trades_in_window(window.approach_start, window.approach_end)
    if not trades:
        return float(np.nan)
    return sum(trade.size for trade in trades) / len(trades)


def app_max_spread(
    buffer: MarketContextBuffer, window: FeatureWindow, level_ctx: LevelContext
) -> float:
    """Widest ask-bid spread (points) over the approach window. NaN if no quotes."""

    quotes = buffer.quotes_in_window(window.approach_start, window.approach_end)
    if not quotes:
        return float(np.nan)
    return max(
        level_ctx.ticks_to_points(quote.ask_price_ticks - quote.bid_price_ticks)
        for quote in quotes
    )


class FeatureFunctionRegistry:
    """An ordered, name-keyed registry of feature functions.

    Lookups raise ``KeyError`` on an unknown feature so a contract that names a
    feature the runtime cannot compute fails loudly rather than silently
    skipping it.
    """

    def __init__(self, functions: Mapping[str, FeatureFn]) -> None:
        self._functions: dict[str, FeatureFn] = dict(functions)

    def __contains__(self, name: object) -> bool:
        return name in self._functions

    def __len__(self) -> int:
        return len(self._functions)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._functions)

    def get(self, name: str) -> FeatureFn:
        try:
            return self._functions[name]
        except KeyError as exc:
            raise KeyError(f"no feature function registered for {name!r}") from exc

    def compute(
        self,
        name: str,
        buffer: MarketContextBuffer,
        window: FeatureWindow,
        level_ctx: LevelContext,
    ) -> float:
        return self.get(name)(buffer, window, level_ctx)


DEFAULT_FEATURE_REGISTRY = FeatureFunctionRegistry(
    {
        "int_time_beyond_level": int_time_beyond_level,
        "int_time_within_2pts": int_time_within_2pts,
        "int_absorption_ratio": int_absorption_ratio,
        "app_large_trade_vol_pct": app_large_trade_vol_pct,
        "app_avg_trade_size": app_avg_trade_size,
        "app_max_spread": app_max_spread,
    }
)


def build_feature_vector(
    buffer: MarketContextBuffer,
    level_ctx: LevelContext,
    contract: StrategyContract,
    section,
    *,
    registry: FeatureFunctionRegistry = DEFAULT_FEATURE_REGISTRY,
    touch_ts_utc: datetime | None = None,
    window: FeatureWindow | None = None,
) -> tuple[list[float], dict[str, float]]:
    """Compute the contract's ordered feature vector and a name->value dict.

    Values follow ``feature_set.names`` order exactly (the model's positional
    contract — an ENVELOPE read). Provide either an explicit ``window`` or a
    ``touch_ts_utc`` from which the interaction/approach windows are derived
    using the active bundle's section ``feature_windows`` minutes (E3:
    section-bound; duck-typed touch-section access, recorded coupling). Missing
    approach data surfaces as ``np.nan``; only the absorption /
    interaction-time features may legitimately return 0.0.
    """

    if window is None:
        if touch_ts_utc is None:
            raise ValueError("build_feature_vector requires either window or touch_ts_utc")
        windows = section.feature_windows
        window = FeatureWindow.from_touch(
            touch_ts_utc,
            interaction_window=timedelta(minutes=windows.interaction_window_minutes),
            approach_window=timedelta(minutes=windows.approach_window_minutes),
        )

    ordered: list[float] = []
    by_name: dict[str, float] = {}
    for name in contract.feature_set.names:
        value = registry.compute(name, buffer, window, level_ctx)
        ordered.append(value)
        by_name[name] = value
    return ordered, by_name
