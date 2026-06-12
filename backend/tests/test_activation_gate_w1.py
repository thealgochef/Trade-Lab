"""W1 P3d: the fail-closed serving-compatibility gate, one negative per check."""

from dataclasses import replace
from types import SimpleNamespace

# Registration import: the section hook needs the plugin registered.
import strategy_core.strategies.touch_reversal  # noqa: F401
from strategy_core.strategies.touch_reversal.section import default_touch_reversal_section

from trade_lab.services.model_registry import ServingCapabilities, serving_compatibility_error

_FEATURES = (
    "int_time_beyond_level",
    "int_time_within_2pts",
    "int_absorption_ratio",
    "app_large_trade_vol_pct",
    "app_avg_trade_size",
    "app_max_spread",
)


def _capabilities() -> ServingCapabilities:
    return ServingCapabilities(
        computable_features=_FEATURES,
        market_context_retention_minutes=240,
        instrument_root="NQ",
        observation_duration_seconds=300,
        decision_timeframe_ticks=147,
        supported_live_schemas=frozenset({"trades", "mbp-1"}),
        supported_replay_schemas=frozenset({"trades", "mbp-1", "mbp-10", "bbo"}),
    )


def _contract(**overrides) -> SimpleNamespace:
    base = SimpleNamespace(
        feature_set=SimpleNamespace(names=_FEATURES),
        instrument="NQ",
        label_policy=SimpleNamespace(decision_offset_minutes=5, barrier_mode="fixed_points"),
        data_requirements=SimpleNamespace(
            live_schemas=("trades", "mbp-1"),
            replay_schemas=("trades", "mbp-1", "mbp-10"),
        ),
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def _section(**overrides) -> SimpleNamespace:
    base = SimpleNamespace(
        feature_windows=SimpleNamespace(
            approach_window_minutes=120, interaction_window_minutes=5
        ),
        session_scheme=default_touch_reversal_section().session_scheme,
        touch_rule=SimpleNamespace(bar_type="147t"),
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_compatible_contract_passes() -> None:
    assert serving_compatibility_error(_contract(), _section(), _capabilities()) is None


def test_refuses_uncomputable_feature_names() -> None:
    contract = _contract(
        feature_set=SimpleNamespace(names=(*_FEATURES, "app_trade_count"))
    )
    error = serving_compatibility_error(contract, _section(), _capabilities())
    assert error is not None and "app_trade_count" in error


def test_refuses_windows_exceeding_configured_retention() -> None:
    capabilities = replace(_capabilities(), market_context_retention_minutes=45)
    error = serving_compatibility_error(_contract(), _section(), capabilities)
    assert error is not None and "retention" in error


def test_refuses_instrument_mismatch() -> None:
    error = serving_compatibility_error(
        _contract(instrument="ES"), _section(), _capabilities()
    )
    assert error is not None and "instrument" in error


def test_refuses_decision_offset_vs_observation_window_mismatch() -> None:
    contract = _contract(
        label_policy=SimpleNamespace(decision_offset_minutes=10, barrier_mode="fixed_points")
    )
    error = serving_compatibility_error(contract, _section(), _capabilities())
    assert error is not None and "observation window" in error


def test_refuses_non_fixed_points_barrier_mode() -> None:
    contract = _contract(
        label_policy=SimpleNamespace(decision_offset_minutes=5, barrier_mode="r_relative")
    )
    error = serving_compatibility_error(contract, _section(), _capabilities())
    assert error is not None and "barrier_mode" in error


def test_refuses_session_scheme_divergence() -> None:
    wired = default_touch_reversal_section().session_scheme
    skewed = wired.model_copy(update={"trading_day_boundary": "19:00"})
    error = serving_compatibility_error(
        _contract(), _section(session_scheme=skewed), _capabilities()
    )
    assert error is not None and "session_scheme" in error


def test_refuses_bar_type_off_the_runtime_decision_timeframe() -> None:
    error = serving_compatibility_error(
        _contract(), _section(touch_rule=SimpleNamespace(bar_type="987t")), _capabilities()
    )
    assert error is not None and "decision timeframe" in error


def test_refuses_unsupported_live_schema() -> None:
    contract = _contract(
        data_requirements=SimpleNamespace(
            live_schemas=("trades", "mbo"), replay_schemas=("mbp-10",)
        )
    )
    error = serving_compatibility_error(contract, _section(), _capabilities())
    assert error is not None and "live schemas" in error


def test_refuses_unsupported_replay_schema() -> None:
    contract = _contract(
        data_requirements=SimpleNamespace(
            live_schemas=("trades",), replay_schemas=("mbo",)
        )
    )
    error = serving_compatibility_error(contract, _section(), _capabilities())
    assert error is not None and "replay schemas" in error
