"""Regression coverage for the engine_version fail-closed gate.

TL loads contracts through SC's ``load_strategy_contract`` with the
``expected_engine_version=ENGINE_VERSION`` hook — the exact call shape used by
``model_registry`` at both its loader entries (discovery and activation). A
contract that declares a mismatched engine_version OR omits the field entirely
must be rejected fail-closed; only a matching one loads. The old local loader's
legacy loads-unbound path (no engine_version -> load with a warning) is retired.
These mirror the contract-loading style in ``test_strategy_contract.py`` (read
the real fixture, mutate, write to tmp_path).
"""

import json
from pathlib import Path

import pytest
import strategy_core
from strategy_core import ContractError, StrategyContract, load_strategy_contract

_FIXTURE_STRATEGY = Path(__file__).parent / "fixtures" / "strategy.json"


def _load_real_payload() -> dict[str, object]:
    return json.loads(_FIXTURE_STRATEGY.read_text(encoding="utf-8"))


def _write_strategy(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_matching_engine_version_loads(tmp_path: Path) -> None:
    payload = _load_real_payload()
    payload["engine_version"] = strategy_core.ENGINE_VERSION
    target = tmp_path / "strategy.json"
    _write_strategy(target, payload)

    contract = load_strategy_contract(
        target, expected_engine_version=strategy_core.ENGINE_VERSION
    )

    assert isinstance(contract, StrategyContract)
    assert contract.engine_version == strategy_core.ENGINE_VERSION


def test_mismatched_engine_version_fails_closed(tmp_path: Path) -> None:
    payload = _load_real_payload()
    payload["engine_version"] = "strategy_core_engine_v2"
    target = tmp_path / "strategy.json"
    _write_strategy(target, payload)

    with pytest.raises(ContractError, match="engine_version"):
        load_strategy_contract(target, expected_engine_version=strategy_core.ENGINE_VERSION)


def test_absent_engine_version_fails_closed(tmp_path: Path) -> None:
    payload = _load_real_payload()
    payload.pop("engine_version", None)
    target = tmp_path / "strategy.json"
    _write_strategy(target, payload)

    with pytest.raises(ContractError, match="engine_version"):
        load_strategy_contract(target, expected_engine_version=strategy_core.ENGINE_VERSION)
