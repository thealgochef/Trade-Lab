"""W1 P3b: contract-driven market-context retention + safety-ceiling semantics."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from trade_lab.domain.events import TradeSide
from trade_lab.domain.market_context import (
    DEFAULT_MAX_ELEMENTS,
    MarketContextBuffer,
)
from trade_lab.services.runtime import (
    MARKET_CONTEXT_RETENTION_SLACK_MINUTES,
    ApplicationRuntime,
)


def _ts(minute: int, second: int = 0) -> datetime:
    return datetime(2026, 2, 18, 15, minute, second, tzinfo=UTC)


def test_set_retention_shrink_re_evicts_immediately() -> None:
    buffer = MarketContextBuffer(retention=timedelta(minutes=45))
    buffer.append_trade(_ts(0), 68000, 1, TradeSide.BUY)
    buffer.append_trade(_ts(30), 68001, 1, TradeSide.BUY)
    assert buffer.trade_count == 2
    buffer.set_retention(timedelta(minutes=10))
    assert buffer.trade_count == 1
    assert buffer.trades_in_window(_ts(0), _ts(31))[0].price_ticks == 68001


def test_set_retention_rejects_non_positive() -> None:
    buffer = MarketContextBuffer()
    with pytest.raises(ValueError):
        buffer.set_retention(timedelta(0))


def test_element_cap_is_a_safety_ceiling_not_the_operative_bound() -> None:
    assert DEFAULT_MAX_ELEMENTS == 6_000_000


class _StubEngine:
    """Just enough InferenceEngine surface for retention wiring tests."""

    def __init__(self, approach: int | None, interaction: int | None) -> None:
        self._approach = approach
        self._interaction = interaction

    @property
    def active_contract(self):  # resolver build path: no contract -> no resolver
        return None

    def active(self):
        if self._approach is None:
            return None
        return SimpleNamespace(
            section=SimpleNamespace(
                feature_windows=SimpleNamespace(
                    approach_window_minutes=self._approach,
                    interaction_window_minutes=self._interaction,
                )
            )
        )


def _runtime() -> ApplicationRuntime:
    return ApplicationRuntime(
        requested_symbol="NQ.c.0",
        tick_timeframes=(147,),
        observation_duration_seconds=300,
        market_context_retention_minutes=45,
    )


def test_activation_drives_retention_from_contract_windows() -> None:
    runtime = _runtime()
    assert runtime.market_context.retention == timedelta(minutes=45)
    runtime.set_inference_engine(_StubEngine(approach=120, interaction=5))
    expected = timedelta(minutes=120 + 5 + MARKET_CONTEXT_RETENTION_SLACK_MINUTES)
    assert runtime.market_context.retention == expected


def test_deactivation_reverts_to_configured_baseline() -> None:
    runtime = _runtime()
    runtime.set_inference_engine(_StubEngine(approach=120, interaction=5))
    runtime.set_inference_engine(None)
    assert runtime.market_context.retention == timedelta(minutes=45)


def test_engine_without_active_model_keeps_baseline() -> None:
    runtime = _runtime()
    runtime.set_inference_engine(_StubEngine(approach=None, interaction=None))
    assert runtime.market_context.retention == timedelta(minutes=45)
