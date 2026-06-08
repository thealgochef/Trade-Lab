"""Regression coverage for audit N1: engine_version fail-closed binding.

A strategy.json that declares an engine_version the runtime cannot match must be
rejected (fail closed), a matching one must load, and a legacy bundle without the
field must still load (unbound). These mirror the contract-loading style in
``test_strategy_contract.py`` (read the real fixture, mutate, write to tmp_path).
"""

import json
from pathlib import Path

import pytest
import strategy_core

from trade_lab.domain.contracts import (
    ContractError,
    StrategyContract,
    load_strategy_contract,
)

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

    contract = load_strategy_contract(target)

    assert isinstance(contract, StrategyContract)
    assert contract.engine_version == strategy_core.ENGINE_VERSION


def test_mismatched_engine_version_fails_closed(tmp_path: Path) -> None:
    payload = _load_real_payload()
    payload["engine_version"] = "strategy_core_engine_v0_does_not_match"
    target = tmp_path / "strategy.json"
    _write_strategy(target, payload)

    with pytest.raises(ContractError, match="engine_version"):
        load_strategy_contract(target)


def test_legacy_bundle_without_engine_version_still_loads(tmp_path: Path) -> None:
    payload = _load_real_payload()
    payload.pop("engine_version", None)  # fixture is already legacy; be explicit
    target = tmp_path / "strategy.json"
    _write_strategy(target, payload)

    contract = load_strategy_contract(target)

    assert contract.engine_version is None
