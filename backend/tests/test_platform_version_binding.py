"""Regression coverage for the two-axis fail-closed gates (E1/E2).

TL loads contracts through SC's ``load_strategy_contract`` with the
``expected_platform_version=PLATFORM_VERSION`` hook — the exact call shape used
by ``model_registry`` at both its loader entries (discovery and activation). A
contract that declares a mismatched platform_version OR omits the field must be
rejected fail-closed; only a matching one loads. On top of the platform axis,
the E2 strategy-axis gate (``_strategy_binding_error`` + the serving-id check)
rejects unknown strategy_ids, strategy_version mismatches, and unservable
bundles at BOTH discovery and activation; a pre-migration v1 contract dies at
the contract_version check. These mirror the contract-loading style in
``test_strategy_contract.py`` (read the real fixture, mutate, write to
tmp_path).
"""

import json
import logging
from pathlib import Path

import pytest
import strategy_core
from strategy_core import ContractError, StrategyContract, load_strategy_contract

from trade_lab.services.model_registry import (
    ModelRegistry,
    ModelValidationError,
    discover_model_bundles,
)

_FIXTURE_STRATEGY = Path(__file__).parent / "fixtures" / "strategy.json"


def _load_real_payload() -> dict[str, object]:
    return json.loads(_FIXTURE_STRATEGY.read_text(encoding="utf-8"))


def _write_strategy(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_bundle_dir(root: Path, name: str, payload: dict[str, object]) -> Path:
    """A minimal on-disk bundle: real strategy.json + stub model/metadata files.

    The stub model.cbm is enough for DISCOVERY (which never opens the binary)
    and for ACTIVATION's strategy gate (which fires BEFORE the model load).
    """

    bundle = root / name
    bundle.mkdir()
    _write_strategy(bundle / "strategy.json", payload)
    (bundle / "model.cbm").write_bytes(b"stub")
    (bundle / "metadata.json").write_text(
        json.dumps({"selected_features": list(payload["feature_set"]["names"])}),  # type: ignore[index]
        encoding="utf-8",
    )
    return bundle


# ── E1: the platform axis (loader hook) ─────────────────────────────────────


def test_matching_platform_version_loads(tmp_path: Path) -> None:
    payload = _load_real_payload()
    payload["platform_version"] = strategy_core.PLATFORM_VERSION
    target = tmp_path / "strategy.json"
    _write_strategy(target, payload)

    contract = load_strategy_contract(
        target, expected_platform_version=strategy_core.PLATFORM_VERSION
    )

    assert isinstance(contract, StrategyContract)
    assert contract.platform_version == strategy_core.PLATFORM_VERSION
    assert contract.strategy_version == "1"


def test_mismatched_platform_version_fails_closed(tmp_path: Path) -> None:
    payload = _load_real_payload()
    payload["platform_version"] = "strategy_core_engine_v3"  # the retired axis literal
    target = tmp_path / "strategy.json"
    _write_strategy(target, payload)

    with pytest.raises(ContractError, match="platform_version"):
        load_strategy_contract(target, expected_platform_version=strategy_core.PLATFORM_VERSION)


def test_absent_platform_version_fails_closed(tmp_path: Path) -> None:
    payload = _load_real_payload()
    payload.pop("platform_version", None)
    target = tmp_path / "strategy.json"
    _write_strategy(target, payload)

    with pytest.raises(ContractError, match="platform_version"):
        load_strategy_contract(target, expected_platform_version=strategy_core.PLATFORM_VERSION)


def test_v1_contract_rejected_on_contract_version(tmp_path: Path) -> None:
    # A pre-migration bundle dies at the FIRST loader check, before the platform
    # hook or any shape complaint.
    payload = _load_real_payload()
    payload["contract_version"] = "trade_lab_contract_v1"
    target = tmp_path / "strategy.json"
    _write_strategy(target, payload)

    with pytest.raises(ContractError, match="unsupported contract_version"):
        load_strategy_contract(target, expected_platform_version=strategy_core.PLATFORM_VERSION)


# ── E2: the strategy axis (registry router gate, BOTH entries) ──────────────


def test_unknown_strategy_id_rejected_at_discovery_and_activation(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    payload = _load_real_payload()
    payload["strategy_id"] = "not_a_registered_strategy"
    _write_bundle_dir(tmp_path, "bundle-x", payload)

    with caplog.at_level(logging.WARNING):
        assert discover_model_bundles(tmp_path) == []
    assert any("unknown strategy_id" in r.getMessage() for r in caplog.records)

    with pytest.raises(ModelValidationError, match="unknown strategy_id"):
        ModelRegistry(tmp_path).activate("bundle-x")


def test_strategy_version_mismatch_rejected_at_discovery_and_activation(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    payload = _load_real_payload()
    payload["strategy_version"] = "999"
    _write_bundle_dir(tmp_path, "bundle-x", payload)

    with caplog.at_level(logging.WARNING):
        assert discover_model_bundles(tmp_path) == []
    assert any("strategy_version mismatch" in r.getMessage() for r in caplog.records)

    with pytest.raises(ModelValidationError, match="strategy_version mismatch"):
        ModelRegistry(tmp_path).activate("bundle-x")


def test_unservable_flag_rejected_at_discovery_and_activation(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    payload = _load_real_payload()
    payload["supported_by_runtime"] = False
    _write_bundle_dir(tmp_path, "bundle-x", payload)

    with caplog.at_level(logging.WARNING):
        assert discover_model_bundles(tmp_path) == []
    assert any("supported_by_runtime" in r.getMessage() for r in caplog.records)

    with pytest.raises(ModelValidationError, match="supported_by_runtime"):
        ModelRegistry(tmp_path).activate("bundle-x")


def test_serving_strategy_id_guard_rejects_foreign_routing(tmp_path: Path) -> None:
    # Check (iv): even a registry-valid contract is refused when it routes to a
    # strategy other than the one the running service's plugin serves.
    payload = _load_real_payload()
    _write_bundle_dir(tmp_path, "bundle-x", payload)

    registry = ModelRegistry(tmp_path, serving_strategy_id="some_other_plugin")
    with pytest.raises(ModelValidationError, match="does not match the"):
        registry.activate("bundle-x")


# ── E3: the section hook (both entries) + the activation ledger fixes ───────


def test_invalid_section_rejected_at_discovery_and_activation(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A section subtree the plugin's SectionModel rejects (unknown key) is skipped
    # with a warning at discovery and raises fail-closed at activation.
    payload = _load_real_payload()
    payload["section"]["bogus_key"] = 1  # type: ignore[index]
    _write_bundle_dir(tmp_path, "bundle-x", payload)

    with caplog.at_level(logging.WARNING):
        assert discover_model_bundles(tmp_path) == []
    assert any("invalid strategy section" in r.getMessage() for r in caplog.records)

    with pytest.raises(ModelValidationError, match="invalid strategy section"):
        ModelRegistry(tmp_path).activate("bundle-x")


def test_partition_cross_check_mismatch_rejected_at_activation(tmp_path: Path) -> None:
    # The envelope<->section feature-partition cross-check (the TL validation
    # site): a section whose partition does not cover feature_set.names fails
    # activation closed. (The section itself is SectionModel-valid — only the
    # cross-check trips.)
    payload = _load_real_payload()
    payload["section"]["approach_features"] = ["app_large_trade_vol_pct"]  # type: ignore[index]
    _write_bundle_dir(tmp_path, "bundle-x", payload)

    with pytest.raises(ModelValidationError, match="exactly the union"):
        ModelRegistry(tmp_path).activate("bundle-x")


def test_metadata_mismatch_rejected_at_activation(tmp_path: Path) -> None:
    # E3 ledger (a): the metadata cross-check is FAIL-CLOSED at activation — a
    # bundle whose metadata selected_features disagree with the contract can no
    # longer activate (discovery's flag is no longer ignorable).
    payload = _load_real_payload()
    bundle = _write_bundle_dir(tmp_path, "bundle-x", payload)
    (bundle / "metadata.json").write_text(
        json.dumps({"selected_features": ["int_time_beyond_level"]}), encoding="utf-8"
    )

    with pytest.raises(ModelValidationError, match="selected_features"):
        ModelRegistry(tmp_path).activate("bundle-x")


def test_missing_checksum_sidecar_logs_debug_at_activation(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # W2-FIX F3 (D-P-01): an absent sidecar is recorded at DEBUG only — pre-W2
    # bundles carry no sidecar and must stay activatable without alarm noise.
    # The stub binary then fails the CatBoost load — asserting AFTER the
    # checksum step proves the log fired on the real activation path.
    payload = _load_real_payload()
    _write_bundle_dir(tmp_path, "bundle-x", payload)  # no model.cbm.sha256 written

    with (
        caplog.at_level(logging.DEBUG),
        pytest.raises(ModelValidationError, match="loadable CatBoost"),
    ):
        ModelRegistry(tmp_path).activate("bundle-x")
    sidecar_records = [r for r in caplog.records if "no model.cbm.sha256 sidecar" in r.getMessage()]
    assert sidecar_records, "absent-sidecar log line did not fire"
    assert all(r.levelno == logging.DEBUG for r in sidecar_records)
