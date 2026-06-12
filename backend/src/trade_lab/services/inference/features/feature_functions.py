"""The six contract features, computed by Strategy-Core's formulas over the buffer.

W1 P3c: Trade-Lab no longer carries its own feature implementations. Each
:data:`FeatureFn` slices the L1/L0 :class:`MarketContextBuffer`, converts the
slice into Strategy-Core neutral ``Trade``/``Quote`` events, and calls the SAME
``strategy_core.decisions.features`` formulas the research/training path uses —
trade-print dwell observables (ratified ``MID_PRICE_SOURCE == "trade_price"``),
SC rounding (4 dp time features, 6 dp absorption) and SC empty-case rules
(interaction -> ``0.0``, approach -> ``NaN``) included. The quote-mid dwell
helpers and the local within-band hardcode are deleted; the within-band half
width now arrives from the active bundle's section via :class:`LevelContext`.

``build_feature_vector`` assembles values strictly in ``feature_set.names``
order (the model's positional contract).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from strategy_core.constants import WITHIN_BAND_PTS as SC_WITHIN_BAND_PTS
from strategy_core.decisions import features as sc_features
from strategy_core.types import Direction as ScDirection
from strategy_core.types import Quote as ScQuote
from strategy_core.types import Trade as ScTrade

if TYPE_CHECKING:
    from strategy_core import StrategyContract

    from trade_lab.domain.market_context import MarketContextBuffer


class FeatureComputationError(RuntimeError):
    """W2 P2d: a named feature function raised during vector construction.

    Carries the failing feature's name so the runtime's inference error path can
    log and surface WHICH feature broke, not just that one did.
    """

    def __init__(self, feature_name: str, original: Exception) -> None:
        super().__init__(f"feature {feature_name!r} failed: {original}")
        self.feature_name = feature_name


class LevelDirection(StrEnum):
    """Trade direction implied by which side of the level was touched."""

    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True, slots=True)
class LevelContext:
    """The level a touch fired against, carried into every feature function.

    ``reference_price`` is the EXACT zone representative price in points (no tick
    snap) as a Decimal. ``tick_size`` translates buffered tick prices into points.
    The proximity band, within-band half width, and large-trade threshold are
    contract/section parameters threaded through here so a :data:`FeatureFn`
    keeps its uniform 3-arg signature and reads no global config.
    """

    reference_price: Decimal
    direction: LevelDirection
    tick_size: Decimal
    proximity_points: float = 0.5
    large_trade_threshold: int = 10
    within_band_pts: float = SC_WITHIN_BAND_PTS

    @property
    def reference_points(self) -> float:
        return float(self.reference_price)

    def sc_direction(self) -> ScDirection:
        return ScDirection[self.direction.name]

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
            within_band_pts=windows.within_band_pts,
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


def _interaction_trades(
    buffer: MarketContextBuffer, window: FeatureWindow
) -> list[ScTrade]:
    return _sc_trades(buffer, window.interaction_start, window.interaction_end)


def _approach_trades(
    buffer: MarketContextBuffer, window: FeatureWindow
) -> list[ScTrade]:
    return _sc_trades(buffer, window.approach_start, window.approach_end)


def _sc_trades(
    buffer: MarketContextBuffer, start: datetime, end: datetime
) -> list[ScTrade]:
    return [
        ScTrade(trade.event_ts_utc, trade.price_ticks, trade.size, None)
        for trade in buffer.trades_in_window(start, end)
    ]


def _sc_quotes(
    buffer: MarketContextBuffer, start: datetime, end: datetime
) -> list[ScQuote]:
    return [
        ScQuote(
            event_ts_utc=quote.event_ts_utc,
            bid_price_ticks=quote.bid_price_ticks,
            ask_price_ticks=quote.ask_price_ticks,
            bid_size=0,
            ask_size=0,
        )
        for quote in buffer.quotes_in_window(start, end)
    ]


def int_time_beyond_level(
    buffer: MarketContextBuffer, window: FeatureWindow, level_ctx: LevelContext
) -> float:
    """Seconds the TRADE price spent on the adverse side of the level (SC formula)."""

    return sc_features.int_time_beyond_level(
        _interaction_trades(buffer, window),
        level_ctx.reference_points,
        level_ctx.sc_direction(),
        float(level_ctx.tick_size),
    )


def int_time_within_2pts(
    buffer: MarketContextBuffer, window: FeatureWindow, level_ctx: LevelContext
) -> float:
    """Seconds the TRADE price stayed within the section's band (SC formula)."""

    return sc_features.int_time_within_2pts(
        _interaction_trades(buffer, window),
        level_ctx.reference_points,
        float(level_ctx.tick_size),
        within_band_pts=level_ctx.within_band_pts,
    )


def int_absorption_ratio(
    buffer: MarketContextBuffer, window: FeatureWindow, level_ctx: LevelContext
) -> float:
    """``at_level_vol / (at_level_vol + through_vol)`` over trades (SC formula)."""

    return sc_features.int_absorption_ratio(
        _interaction_trades(buffer, window),
        level_ctx.reference_points,
        level_ctx.sc_direction(),
        float(level_ctx.tick_size),
        proximity_pts=level_ctx.proximity_points,
    )


def app_large_trade_vol_pct(
    buffer: MarketContextBuffer, window: FeatureWindow, level_ctx: LevelContext
) -> float:
    """Large-trade volume share over the approach window (SC formula; NaN empty)."""

    return sc_features.app_large_trade_vol_pct(
        _approach_trades(buffer, window),
        large_trade_threshold=level_ctx.large_trade_threshold,
    )


def app_avg_trade_size(
    buffer: MarketContextBuffer, window: FeatureWindow, level_ctx: LevelContext
) -> float:
    """Mean trade size over the approach window (SC formula; NaN empty)."""

    return sc_features.app_avg_trade_size(_approach_trades(buffer, window))


def app_max_spread(
    buffer: MarketContextBuffer, window: FeatureWindow, level_ctx: LevelContext
) -> float:
    """Widest ask-bid spread (points) over the approach window (SC formula; NaN empty)."""

    return sc_features.app_max_spread(
        _sc_quotes(buffer, window.approach_start, window.approach_end),
        float(level_ctx.tick_size),
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
    using the active bundle's section ``feature_windows`` minutes. Missing
    approach data surfaces as ``NaN`` (SC's empty-case rule); only the
    absorption / interaction-time features may legitimately return 0.0.
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
        try:
            value = registry.compute(name, buffer, window, level_ctx)
        except Exception as exc:
            # W2 P2d: name the failing feature so the runtime's status surface can
            # report WHICH feature broke, not just that one did.
            raise FeatureComputationError(name, exc) from exc
        ordered.append(value)
        by_name[name] = value
    return ordered, by_name
