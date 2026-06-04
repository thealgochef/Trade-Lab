"""Stage 4 — MAE-first OutcomeTracker on contract forward bars (147t).

Pure Python: no CatBoost is loaded here. A frozen :class:`Prediction` is built
by hand (Stage 3's shape) and resolved against synthetic 147t :class:`Candle`s.
The ladder mirrors ``dashboard_utility_labeling`` exactly: MAE is checked FIRST,
so a bar that breaches both the stop and the target resolves to the LOSS.

Thresholds come from the fixture contract: tp 15 / sl 30 / trap_mfe_min 5 pts,
tick_size 0.25, forward_bar_type 147t, RTH cutoff 16:15 US/Eastern.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType

from trade_lab.domain.candles import Candle, CandleCloseReason
from trade_lab.domain.contracts import load_strategy_contract
from trade_lab.domain.outcomes import ResolutionType
from trade_lab.services.inference.inference_engine import Prediction
from trade_lab.services.inference.outcome_tracker import OutcomeTracker

_FIXTURE_STRATEGY = Path(__file__).parent / "fixtures" / "strategy.json"
_TICK_SIZE = 0.25
# 68_000 ticks * 0.25 = 17_000.0 points entry reference.
_LEVEL_TICKS = 68_000
# A 14:30Z touch sits well inside RTH (≈09:30 ET), comfortably before the cutoff.
_TOUCH_TS = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


def _contract():
    return load_strategy_contract(_FIXTURE_STRATEGY)


def _points_to_ticks(points: float) -> int:
    return round(points / _TICK_SIZE)


def _prediction(
    *,
    direction: str = "long",
    predicted_class: str = "tradeable_reversal",
    level_ticks: int = _LEVEL_TICKS,
    touch_ts: datetime = _TOUCH_TS,
    prediction_id: str = "pred-1",
    touch_id: str = "touch-1",
) -> Prediction:
    return Prediction(
        prediction_id=prediction_id,
        touch_id=touch_id,
        observation_id="obs-1",
        event_ts_utc=touch_ts,
        predicted_class=predicted_class,
        probabilities=MappingProxyType({predicted_class: 1.0}),
        feature_values=MappingProxyType({}),
        level_kind="ny_low" if direction == "long" else "ny_high",
        level_price_ticks=level_ticks,
        direction=direction,
        session="ny",
        is_eligible=True,
        model_id="m-1",
        contract_id="NQ_test",
        nan_count=0,
    )


def _bar(
    *,
    high_pts: float,
    low_pts: float,
    close_ts: datetime,
    timeframe_ticks: int = 147,
    entry_pts: float = 17_000.0,
) -> Candle:
    """Build a 147t bar whose high/low are entry ± the given point offsets.

    ``high_pts``/``low_pts`` are absolute point levels relative to the 17_000 entry
    so callers express MFE/MAE directly.
    """

    high_ticks = _points_to_ticks(high_pts)
    low_ticks = _points_to_ticks(low_pts)
    return Candle(
        timeframe_ticks=timeframe_ticks,
        trading_day=close_ts.date(),
        bar_index=0,
        bar_id=f"{timeframe_ticks}t:{close_ts.date().isoformat()}:0",
        open_ts_utc=close_ts - timedelta(seconds=30),
        close_ts_utc=close_ts,
        open_ticks=_points_to_ticks(entry_pts),
        high_ticks=high_ticks,
        low_ticks=low_ticks,
        close_ticks=high_ticks,
        volume=147,
        trade_count=147,
        is_complete=True,
        is_partial=False,
        close_reason=CandleCloseReason.COMPLETE,
    )


# --------------------------------------------------------------------------- #
# MAE-first ladder
# --------------------------------------------------------------------------- #


def test_same_bar_breaching_both_sl_and_tp_resolves_to_loss() -> None:
    """A single bar satisfying BOTH SL and TP must resolve to the LOSS, not the win.

    This is the crux of MAE-first: MAE is checked before MFE, so the stop wins.
    With MFE 16 >= trap_mfe_min 5 the loss is a trap_reversal.
    """

    tracker = OutcomeTracker(_contract())
    tracker.register(_prediction(direction="long"))
    bar = _bar(
        high_pts=17_000 + 16,  # MFE 16 >= tp 15
        low_pts=17_000 - 31,  # MAE 31 >= sl 30
        close_ts=_TOUCH_TS + timedelta(minutes=2),
    )

    outcomes = tracker.on_bar_close(bar)

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.resolution_type is ResolutionType.SL_HIT
    assert outcome.actual_class == "trap_reversal"  # mfe>=trap_min -> trap, not tradeable
    assert outcome.max_mae_pts == 31.0
    assert outcome.max_mfe_pts == 16.0
    assert tracker.open_count == 0


def test_sl_hit_with_low_mfe_is_aggressive_blowthrough() -> None:
    tracker = OutcomeTracker(_contract())
    tracker.register(_prediction(direction="long", predicted_class="aggressive_blowthrough"))
    bar = _bar(
        high_pts=17_000 + 3,  # MFE 3 < trap_mfe_min 5
        low_pts=17_000 - 30,  # MAE 30 >= sl 30
        close_ts=_TOUCH_TS + timedelta(minutes=2),
    )

    outcomes = tracker.on_bar_close(bar)

    assert len(outcomes) == 1
    assert outcomes[0].resolution_type is ResolutionType.SL_HIT
    assert outcomes[0].actual_class == "aggressive_blowthrough"
    assert outcomes[0].correct is True  # predicted == actual


def test_trap_vs_blowthrough_split_at_trap_mfe_min() -> None:
    """Exactly at trap_mfe_min the loss is a trap; just below it is a blowthrough."""

    contract = _contract()

    at_min = OutcomeTracker(contract)
    at_min.register(_prediction(direction="long"))
    trap = at_min.on_bar_close(
        _bar(high_pts=17_000 + 5, low_pts=17_000 - 30, close_ts=_TOUCH_TS + timedelta(minutes=1))
    )
    assert trap[0].actual_class == "trap_reversal"

    below_min = OutcomeTracker(contract)
    below_min.register(_prediction(direction="long"))
    blow = below_min.on_bar_close(
        _bar(high_pts=17_000 + 4, low_pts=17_000 - 30, close_ts=_TOUCH_TS + timedelta(minutes=1))
    )
    assert blow[0].actual_class == "aggressive_blowthrough"


def test_tp_hit_when_only_target_is_reached() -> None:
    tracker = OutcomeTracker(_contract())
    tracker.register(_prediction(direction="long", predicted_class="tradeable_reversal"))
    bar = _bar(
        high_pts=17_000 + 15,  # MFE 15 >= tp 15
        low_pts=17_000 - 10,  # MAE 10 < sl 30
        close_ts=_TOUCH_TS + timedelta(minutes=2),
    )

    outcomes = tracker.on_bar_close(bar)

    assert len(outcomes) == 1
    assert outcomes[0].resolution_type is ResolutionType.TP_HIT
    assert outcomes[0].actual_class == "tradeable_reversal"
    assert outcomes[0].correct is True


def test_running_max_accumulates_across_bars() -> None:
    """MFE/MAE accumulate across bars; neither bar alone hits a threshold."""

    tracker = OutcomeTracker(_contract())
    tracker.register(_prediction(direction="long"))

    first = tracker.on_bar_close(
        _bar(high_pts=17_000 + 8, low_pts=17_000 - 8, close_ts=_TOUCH_TS + timedelta(minutes=1))
    )
    assert first == ()  # neither tp(15) nor sl(30) hit yet
    assert tracker.open_count == 1

    second = tracker.on_bar_close(
        _bar(high_pts=17_000 + 16, low_pts=17_000 - 2, close_ts=_TOUCH_TS + timedelta(minutes=2))
    )
    assert len(second) == 1
    assert second[0].resolution_type is ResolutionType.TP_HIT
    assert second[0].bars_to_resolution == 2  # both bars scanned


# --------------------------------------------------------------------------- #
# Direction
# --------------------------------------------------------------------------- #


def test_short_direction_inverts_mfe_and_mae() -> None:
    """For a SHORT, favorable = entry-low and adverse = high-entry."""

    tracker = OutcomeTracker(_contract())
    tracker.register(_prediction(direction="short", predicted_class="tradeable_reversal"))
    # Price falls 15 below entry (MFE 15 for a short) and only rises 5 (MAE 5).
    bar = _bar(
        high_pts=17_000 + 5,
        low_pts=17_000 - 15,
        close_ts=_TOUCH_TS + timedelta(minutes=2),
    )

    outcomes = tracker.on_bar_close(bar)

    assert len(outcomes) == 1
    assert outcomes[0].resolution_type is ResolutionType.TP_HIT
    assert outcomes[0].max_mfe_pts == 15.0
    assert outcomes[0].max_mae_pts == 5.0


def test_short_sl_hit_is_a_loss() -> None:
    tracker = OutcomeTracker(_contract())
    tracker.register(_prediction(direction="short"))
    # Price rises 30 above entry (MAE 30 for a short) -> stop hit; small favorable.
    bar = _bar(
        high_pts=17_000 + 30,
        low_pts=17_000 - 6,
        close_ts=_TOUCH_TS + timedelta(minutes=2),
    )

    outcomes = tracker.on_bar_close(bar)

    assert outcomes[0].resolution_type is ResolutionType.SL_HIT
    assert outcomes[0].actual_class == "trap_reversal"  # mfe 6 >= trap_min 5


# --------------------------------------------------------------------------- #
# Entry reference + correctness flag
# --------------------------------------------------------------------------- #


def test_entry_reference_is_the_level_price() -> None:
    """A different level price shifts the entry, so the same bar resolves differently."""

    contract = _contract()
    # Level at 68_060 ticks = 17_015 points. A bar topping at 17_015 has MFE 0,
    # bottoming at 16_985 has MAE 30 -> stop. (Same absolute bar, higher entry.)
    tracker = OutcomeTracker(contract)
    tracker.register(_prediction(direction="long", level_ticks=68_060))
    bar = _bar(
        high_pts=17_015,  # == entry -> MFE 0
        low_pts=17_015 - 30,  # MAE 30 -> stop
        close_ts=_TOUCH_TS + timedelta(minutes=2),
    )

    outcomes = tracker.on_bar_close(bar)

    assert outcomes[0].resolution_type is ResolutionType.SL_HIT
    assert outcomes[0].max_mfe_pts == 0.0
    assert outcomes[0].max_mae_pts == 30.0


def test_correct_flag_is_false_on_misprediction() -> None:
    tracker = OutcomeTracker(_contract())
    # Predicted a win, actual is a stop loss -> correct is False.
    tracker.register(_prediction(direction="long", predicted_class="tradeable_reversal"))
    bar = _bar(
        high_pts=17_000 + 1,
        low_pts=17_000 - 30,
        close_ts=_TOUCH_TS + timedelta(minutes=2),
    )

    outcomes = tracker.on_bar_close(bar)

    assert outcomes[0].actual_class == "aggressive_blowthrough"
    assert outcomes[0].predicted_class == "tradeable_reversal"
    assert outcomes[0].correct is False


# --------------------------------------------------------------------------- #
# Session-end forced resolution
# --------------------------------------------------------------------------- #


def test_session_end_forces_resolution_at_rth_close() -> None:
    """A bar closing at/after 16:15 ET forces resolution even if neither hit.

    16:15 US/Eastern in January is 21:15 UTC. Neither tp nor sl is reached, so the
    forced ladder classifies on MFE alone: mfe<trap_min -> blowthrough.
    """

    tracker = OutcomeTracker(_contract())
    tracker.register(_prediction(direction="long"))
    cutoff_close = datetime(2026, 1, 5, 21, 15, tzinfo=UTC)  # 16:15 ET
    bar = _bar(
        high_pts=17_000 + 2,  # mfe 2 < trap_min 5
        low_pts=17_000 - 2,  # mae 2 < sl 30
        close_ts=cutoff_close,
    )

    outcomes = tracker.on_bar_close(bar)

    assert len(outcomes) == 1
    assert outcomes[0].resolution_type is ResolutionType.SESSION_END
    assert outcomes[0].actual_class == "aggressive_blowthrough"
    assert tracker.open_count == 0


def test_session_end_classifies_trap_when_mfe_clears_trap_min() -> None:
    tracker = OutcomeTracker(_contract())
    tracker.register(_prediction(direction="long"))
    cutoff_close = datetime(2026, 1, 5, 21, 20, tzinfo=UTC)  # past 16:15 ET
    bar = _bar(
        high_pts=17_000 + 7,  # mfe 7 >= trap_min 5
        low_pts=17_000 - 3,  # mae 3 < sl 30
        close_ts=cutoff_close,
    )

    outcomes = tracker.on_bar_close(bar)

    assert outcomes[0].resolution_type is ResolutionType.SESSION_END
    assert outcomes[0].actual_class == "trap_reversal"


def test_session_end_still_prefers_real_sl_hit_over_forced() -> None:
    """At the cutoff a genuine stop is an sl_hit, not a session_end."""

    tracker = OutcomeTracker(_contract())
    tracker.register(_prediction(direction="long"))
    cutoff_close = datetime(2026, 1, 5, 21, 15, tzinfo=UTC)
    bar = _bar(
        high_pts=17_000 + 1,
        low_pts=17_000 - 30,  # real stop
        close_ts=cutoff_close,
    )

    outcomes = tracker.on_bar_close(bar)

    assert outcomes[0].resolution_type is ResolutionType.SL_HIT


# --------------------------------------------------------------------------- #
# Bar filtering + lifecycle
# --------------------------------------------------------------------------- #


def test_touch_bar_and_earlier_bars_are_excluded() -> None:
    """A bar closing at/before the touch never advances the tracker."""

    tracker = OutcomeTracker(_contract())
    tracker.register(_prediction(direction="long"))
    before = _bar(
        high_pts=17_000 + 50,
        low_pts=17_000 - 50,
        close_ts=_TOUCH_TS - timedelta(seconds=1),
    )

    assert tracker.on_bar_close(before) == ()
    assert tracker.open_count == 1  # still open, the early bar was ignored


def test_non_forward_timeframe_bars_are_ignored() -> None:
    tracker = OutcomeTracker(_contract())
    tracker.register(_prediction(direction="long"))
    other = _bar(
        high_pts=17_000 + 50,
        low_pts=17_000 - 50,
        close_ts=_TOUCH_TS + timedelta(minutes=1),
        timeframe_ticks=987,  # not the contract's 147t forward bar
    )

    assert tracker.on_bar_close(other) == ()
    assert tracker.open_count == 1


def test_reset_drops_open_trackers() -> None:
    tracker = OutcomeTracker(_contract())
    tracker.register(_prediction(direction="long"))
    assert tracker.open_count == 1

    tracker.reset()

    assert tracker.open_count == 0
    # A later bar produces nothing because the prediction was dropped.
    assert tracker.on_bar_close(
        _bar(high_pts=17_000 + 16, low_pts=17_000, close_ts=_TOUCH_TS + timedelta(minutes=1))
    ) == ()


def test_forward_timeframe_matches_contract_bar_type() -> None:
    tracker = OutcomeTracker(_contract())
    assert tracker.forward_timeframe_ticks == 147
