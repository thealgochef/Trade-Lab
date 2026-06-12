"""Golden-vector tests for the six contract feature functions.

Each fixture hand-builds trade and quote streams in integer ticks (NQ tick size
0.25) with values chosen so every feature's output is computable by hand. The
tests pin: the six values; each feature's distinct empty/edge rule (interaction
times -> 0.0, absorption -> 0.0, approach -> NaN); deterministic recomputation;
and that ``build_feature_vector`` emits values in the contract's exact order.
"""

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

# Registration import: the v3 section hook resolves the plugin's SectionModel.
import strategy_core.strategies.touch_reversal  # noqa: F401
from strategy_core import load_strategy_contract

from trade_lab.domain.events import TradeSide
from trade_lab.domain.market_context import MarketContextBuffer
from trade_lab.services.inference.features import (
    DEFAULT_FEATURE_REGISTRY,
    FeatureWindow,
    LevelContext,
    LevelDirection,
    build_feature_vector,
)
from trade_lab.services.inference.features.feature_functions import (
    app_avg_trade_size,
    app_large_trade_vol_pct,
    app_max_spread,
    int_absorption_ratio,
    int_time_beyond_level,
    int_time_within_2pts,
)

TICK_SIZE = Decimal("0.25")
# Level at 17000.0 points -> 68000 ticks at a 0.25 tick size.
LEVEL_PRICE = Decimal("17000.0")
LEVEL_TICKS = 68_000
TOUCH = datetime(2026, 2, 20, 15, 0, tzinfo=UTC)

FIXTURE_CONTRACT = Path(__file__).parent / "fixtures" / "strategy.json"


def _load_fixture_contract():
    """The v3 fixture loaded with the section hook (feature_windows is section-bound)."""
    return load_strategy_contract(FIXTURE_CONTRACT, validate_section_via_registry=True)


def _ts(seconds: float) -> datetime:
    return TOUCH + timedelta(seconds=seconds)


def _long_ctx() -> LevelContext:
    return LevelContext(
        reference_price=LEVEL_PRICE,
        direction=LevelDirection.LONG,
        tick_size=TICK_SIZE,
        proximity_points=0.5,
        large_trade_threshold=10,
    )


def _short_ctx() -> LevelContext:
    return LevelContext(
        reference_price=LEVEL_PRICE,
        direction=LevelDirection.SHORT,
        tick_size=TICK_SIZE,
        proximity_points=0.5,
        large_trade_threshold=10,
    )


def _window() -> FeatureWindow:
    return FeatureWindow.from_touch(
        TOUCH,
        interaction_window=timedelta(minutes=5),
        approach_window=timedelta(minutes=30),
    )


def _empty_buffer() -> MarketContextBuffer:
    # Wide retention so nothing is evicted while we stage 30m of approach history.
    return MarketContextBuffer(retention=timedelta(hours=2))


def _interaction_buffer() -> MarketContextBuffer:
    """Post-touch TRADE stream (W1 P3c: dwell observable is the trade print).

    One stream drives both the time-tempo features (gap after each print is
    attributed to its price) and absorption (proximity band +/-0.5pts ->
    [16999.5, 17000.5]):

      t=0s   17000.00 x5  at-level    gap 10s -> within;             at_level +5
      t=10s  16996.00 x4  4pts below  gap 30s -> beyond (LONG);      through  +4
      t=40s  17000.00 x3  at-level    gap 60s -> within;             at_level +3
      t=100s 17004.00 x2  4pts above  last print: no following gap;  favorable
    """

    buffer = _empty_buffer()
    buffer.append_trade(_ts(0), 68_000, 5, TradeSide.BUY)
    buffer.append_trade(_ts(10), 67_984, 4, TradeSide.SELL)
    buffer.append_trade(_ts(40), 68_000, 3, TradeSide.BUY)
    buffer.append_trade(_ts(100), 68_016, 2, TradeSide.BUY)
    return buffer


def _approach_buffer() -> MarketContextBuffer:
    """Trades + quotes in the pre-touch [touch-30m, touch) window."""

    buffer = _empty_buffer()
    # Trades: sizes 12, 8, 10, 4 -> total 34, large(>=10) vol 22.
    buffer.append_trade(_ts(-1500), 68_000, 12, TradeSide.BUY)
    buffer.append_trade(_ts(-1400), 67_998, 8, TradeSide.SELL)
    buffer.append_trade(_ts(-1000), 68_002, 10, TradeSide.BUY)
    buffer.append_trade(_ts(-200), 67_996, 4, TradeSide.SELL)
    # Quotes: spreads 20t (5.0pts) and 4t (1.0pts) -> max 5.0.
    buffer.append_quote(_ts(-1450), 67_990, 68_010)
    buffer.append_quote(_ts(-300), 67_998, 68_002)
    return buffer


# ── Interaction features ───────────────────────────────────────────


def test_int_time_beyond_level_long_sums_adverse_dwell() -> None:
    buffer = _interaction_buffer()
    value = int_time_beyond_level(buffer, _window(), _long_ctx())
    # Only the 30s gap after the 16996.0 print is below the level for a LONG.
    assert value == pytest.approx(30.0)


def test_int_time_beyond_level_short_inverts_side() -> None:
    buffer = _interaction_buffer()
    value = int_time_beyond_level(buffer, _window(), _short_ctx())
    # For a SHORT only prints above the level are adverse: the 17004.0 print is
    # last, so it contributes no following gap -> no adverse dwell here.
    assert value == pytest.approx(0.0)


def test_int_time_within_2pts_sums_near_level_dwell() -> None:
    buffer = _interaction_buffer()
    value = int_time_within_2pts(buffer, _window(), _long_ctx())
    # 10s (after the first 17000.00) + 60s (after the second) = 70s within 2pts.
    assert value == pytest.approx(70.0)


def test_int_absorption_ratio_long() -> None:
    buffer = _interaction_buffer()
    value = int_absorption_ratio(buffer, _window(), _long_ctx())
    # at-level 5+3=8, through 4, total 12 -> 8/12.
    assert value == pytest.approx(8.0 / 12.0)


def test_interaction_time_features_zero_when_no_trades() -> None:
    buffer = _empty_buffer()
    window = _window()
    ctx = _long_ctx()
    assert int_time_beyond_level(buffer, window, ctx) == 0.0
    assert int_time_within_2pts(buffer, window, ctx) == 0.0


def test_int_absorption_ratio_zero_when_no_volume() -> None:
    buffer = _empty_buffer()
    assert int_absorption_ratio(buffer, _window(), _long_ctx()) == 0.0


def test_int_absorption_ratio_zero_when_only_favorable_volume() -> None:
    buffer = _empty_buffer()
    # A favorable-side trade for a LONG is neither at-level nor through -> total 0.
    buffer.append_trade(_ts(3), 68_010, 7, TradeSide.BUY)  # 17002.5, above level
    assert int_absorption_ratio(buffer, _window(), _long_ctx()) == 0.0


def test_dwell_skips_gaps_longer_than_ten_minutes() -> None:
    buffer = _empty_buffer()
    # Two at-level trades 700s apart (> 600s ceiling): the gap is discarded.
    buffer.append_trade(_ts(0), 68_000, 1, TradeSide.BUY)
    buffer.append_trade(_ts(700), 68_000, 1, TradeSide.BUY)
    # Extend the interaction window so both quotes fall inside it.
    window = FeatureWindow.from_touch(
        TOUCH,
        interaction_window=timedelta(minutes=30),
        approach_window=timedelta(minutes=30),
    )
    assert int_time_within_2pts(buffer, window, _long_ctx()) == 0.0


# ── Approach features ──────────────────────────────────────────────


def test_app_large_trade_vol_pct() -> None:
    buffer = _approach_buffer()
    value = app_large_trade_vol_pct(buffer, _window(), _long_ctx())
    assert value == pytest.approx(22.0 / 34.0)


def test_app_avg_trade_size() -> None:
    buffer = _approach_buffer()
    value = app_avg_trade_size(buffer, _window(), _long_ctx())
    assert value == pytest.approx(34.0 / 4.0)


def test_app_max_spread() -> None:
    buffer = _approach_buffer()
    value = app_max_spread(buffer, _window(), _long_ctx())
    assert value == pytest.approx(5.0)


def test_app_features_nan_when_no_data() -> None:
    buffer = _empty_buffer()
    window = _window()
    ctx = _long_ctx()
    assert math.isnan(app_large_trade_vol_pct(buffer, window, ctx))
    assert math.isnan(app_avg_trade_size(buffer, window, ctx))
    assert math.isnan(app_max_spread(buffer, window, ctx))


def test_app_large_trade_vol_pct_nan_when_zero_total() -> None:
    # No trades in the approach window (only a quote) -> total 0 -> NaN, not 0.0.
    buffer = _empty_buffer()
    buffer.append_quote(_ts(-300), 67_998, 68_002)
    assert math.isnan(app_large_trade_vol_pct(buffer, _window(), _long_ctx()))


def test_approach_window_is_end_exclusive_of_touch() -> None:
    buffer = _empty_buffer()
    # A trade exactly at the touch timestamp belongs to interaction, not approach.
    buffer.append_trade(TOUCH, 68_000, 9, TradeSide.BUY)
    assert math.isnan(app_avg_trade_size(buffer, _window(), _long_ctx()))


# ── W1 P3c pins: section band + exact off-grid reference ──────────


def test_within_band_width_comes_from_level_context() -> None:
    """The within-band half width is a section parameter, not a local hardcode."""
    buffer = _empty_buffer()
    # 17001.5 print (1.5pts above level) with a 20s following gap.
    buffer.append_trade(_ts(0), 68_006, 1, TradeSide.BUY)
    buffer.append_trade(_ts(20), 68_006, 1, TradeSide.BUY)
    wide = _long_ctx()  # default band 2.0 -> 1.5pts is within
    narrow = LevelContext(
        reference_price=LEVEL_PRICE,
        direction=LevelDirection.LONG,
        tick_size=TICK_SIZE,
        within_band_pts=1.0,
    )
    assert int_time_within_2pts(buffer, _window(), wide) == pytest.approx(20.0)
    assert int_time_within_2pts(buffer, _window(), narrow) == pytest.approx(0.0)


def test_reference_price_accepts_off_grid_zone_mean() -> None:
    """The reference is the EXACT zone representative price — no tick snap."""
    off_grid = Decimal("17000.1667")  # a 3-level zone mean, off the 0.25 grid
    ctx = LevelContext(
        reference_price=off_grid,
        direction=LevelDirection.LONG,
        tick_size=TICK_SIZE,
        proximity_points=0.5,
    )
    buffer = _empty_buffer()
    buffer.append_trade(_ts(1), 68_001, 2, TradeSide.BUY)  # 17000.25: in band
    buffer.append_trade(_ts(2), 67_998, 1, TradeSide.SELL)  # 16999.50: through
    assert int_absorption_ratio(buffer, _window(), ctx) == pytest.approx(2.0 / 3.0)


# ── Vector assembly / ordering ─────────────────────────────────────


def _combined_buffer() -> MarketContextBuffer:
    """A single buffer carrying both the approach and interaction fixtures.

    Built by replaying each source buffer's public window slices so the same
    hand-checked streams drive the full-vector assembly tests.
    """

    span_start = _ts(-3600)
    span_end = _ts(3600)
    buffer = _empty_buffer()
    for source in (_approach_buffer(), _interaction_buffer()):
        for trade in source.trades_in_window(span_start, span_end):
            buffer.append_trade(
                trade.event_ts_utc, trade.price_ticks, trade.size, trade.side
            )
        for quote in source.quotes_in_window(span_start, span_end):
            buffer.append_quote(
                quote.event_ts_utc, quote.bid_price_ticks, quote.ask_price_ticks
            )
    return buffer


def test_build_feature_vector_orders_by_contract_names() -> None:
    contract = _load_fixture_contract()
    buffer = _combined_buffer()
    ctx = LevelContext.from_contract(
        contract,
        contract.section_model,
        reference_price=LEVEL_PRICE,
        direction=LevelDirection.LONG,
    )

    ordered, by_name = build_feature_vector(
        buffer, ctx, contract, contract.section_model, touch_ts_utc=TOUCH
    )

    assert list(by_name) == list(contract.feature_set.names)
    assert len(ordered) == contract.feature_count
    expected = {
        "int_time_beyond_level": 30.0,
        "int_time_within_2pts": 70.0,
        "int_absorption_ratio": 8.0 / 12.0,
        "app_large_trade_vol_pct": 22.0 / 34.0,
        "app_avg_trade_size": 34.0 / 4.0,
        "app_max_spread": 5.0,
    }
    for name, want in expected.items():
        assert by_name[name] == pytest.approx(want)
    assert ordered == [by_name[name] for name in contract.feature_set.names]


def test_build_feature_vector_uses_nan_for_missing_approach() -> None:
    contract = _load_fixture_contract()
    buffer = _empty_buffer()  # no events at all
    ctx = LevelContext.from_contract(
        contract,
        contract.section_model,
        reference_price=LEVEL_PRICE,
        direction=LevelDirection.LONG,
    )

    _ordered, by_name = build_feature_vector(
        buffer, ctx, contract, contract.section_model, touch_ts_utc=TOUCH
    )

    # Interaction features fall to their 0.0 rule; approach features to NaN.
    assert by_name["int_time_beyond_level"] == 0.0
    assert by_name["int_time_within_2pts"] == 0.0
    assert by_name["int_absorption_ratio"] == 0.0
    assert math.isnan(by_name["app_large_trade_vol_pct"])
    assert math.isnan(by_name["app_avg_trade_size"])
    assert math.isnan(by_name["app_max_spread"])
    # No 0.0 sentinel leaked into the approach slots.
    assert all(
        math.isnan(value)
        for name, value in by_name.items()
        if name.startswith("app_")
    )


def test_build_feature_vector_is_deterministic() -> None:
    contract = _load_fixture_contract()
    section = contract.section_model
    buffer = _combined_buffer()
    ctx = LevelContext.from_contract(
        contract, section, reference_price=LEVEL_PRICE, direction=LevelDirection.LONG
    )

    first, _ = build_feature_vector(buffer, ctx, contract, section, touch_ts_utc=TOUCH)
    second, _ = build_feature_vector(buffer, ctx, contract, section, touch_ts_utc=TOUCH)
    assert first == second


def test_build_feature_vector_requires_window_or_touch() -> None:
    contract = _load_fixture_contract()
    ctx = _long_ctx()
    with pytest.raises(ValueError, match="window or touch_ts_utc"):
        build_feature_vector(_empty_buffer(), ctx, contract, contract.section_model)


def test_registry_exposes_six_contract_features_in_order() -> None:
    assert DEFAULT_FEATURE_REGISTRY.names == (
        "int_time_beyond_level",
        "int_time_within_2pts",
        "int_absorption_ratio",
        "app_large_trade_vol_pct",
        "app_avg_trade_size",
        "app_max_spread",
    )


def test_registry_rejects_unknown_feature() -> None:
    with pytest.raises(KeyError, match="no feature function registered"):
        DEFAULT_FEATURE_REGISTRY.get("app_does_not_exist")


def test_feature_values_are_native_floats_not_numpy() -> None:
    # build_feature_vector should yield plain floats so DTO serialization is clean.
    contract = _load_fixture_contract()
    ordered, _ = build_feature_vector(
        _empty_buffer(), _long_ctx(), contract, contract.section_model, touch_ts_utc=TOUCH
    )
    assert all(isinstance(value, float) and not isinstance(value, np.floating)
               for value in ordered)
