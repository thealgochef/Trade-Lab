import json
from dataclasses import asdict
from pathlib import Path

import pytest
from strategy_core import CONTRACT_VERSION, ContractError, StrategyContract, load_strategy_contract

from trade_lab.services.model_registry import (
    ModelBundle,
    discover_model_bundles,
    is_safe_model_id,
)

_FIXTURE_STRATEGY = Path(__file__).parent / "fixtures" / "strategy.json"


def _load_real_payload() -> dict[str, object]:
    return json.loads(_FIXTURE_STRATEGY.read_text(encoding="utf-8"))


def _write_strategy(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_bundle(
    root: Path,
    model_id: str,
    *,
    strategy_payload: dict[str, object] | None = None,
    selected_features: list[str] | None = None,
    with_checksum: bool = True,
) -> Path:
    payload = strategy_payload if strategy_payload is not None else _load_real_payload()
    features = (
        selected_features
        if selected_features is not None
        else list(payload["feature_set"]["names"])  # type: ignore[index]
    )
    directory = root / model_id
    directory.mkdir(parents=True)
    (directory / "model.cbm").write_bytes(b"not-a-real-model")
    (directory / "metadata.json").write_text(
        json.dumps({"selected_features": features}), encoding="utf-8"
    )
    _write_strategy(directory / "strategy.json", payload)
    if with_checksum:
        (directory / "model.cbm.sha256").write_text("0" * 64, encoding="utf-8")
    return directory


def test_load_real_strategy_contract_parses_all_sections() -> None:
    # v3: the section hook types the strategy-owned subtree (touch_rule /
    # feature_windows now live there); envelope reads are unchanged.
    contract = load_strategy_contract(_FIXTURE_STRATEGY, validate_section_via_registry=True)

    assert isinstance(contract, StrategyContract)
    assert contract.contract_version == CONTRACT_VERSION
    assert contract.instrument == "NQ"
    assert contract.tick_size == 0.25
    assert contract.point_value == 20.0
    assert contract.feature_count == 6
    assert contract.feature_set.names == (
        "int_time_beyond_level",
        "int_time_within_2pts",
        "int_absorption_ratio",
        "app_large_trade_vol_pct",
        "app_avg_trade_size",
        "app_max_spread",
    )
    assert contract.class_map.labels == (
        "tradeable_reversal",
        "trap_reversal",
        "aggressive_blowthrough",
    )
    assert len(contract.class_map) == 3
    section = contract.section_model
    assert section.touch_rule.bar_type == "147t"
    assert section.feature_windows.interaction_window_minutes == 5
    assert section.feature_windows.approach_window_minutes == 30
    assert contract.label_policy.resolution == "mae_first"
    assert contract.label_policy.barrier_mode == "fixed_points"
    assert contract.inference.confidence_gate == 0.7
    assert contract.data_requirements.depth_usage == "top_of_book_only"


def test_load_rejects_unsupported_contract_version(tmp_path: Path) -> None:
    # E3: v2 is now the UNSUPPORTED version (the shape break #2 migrated v2 -> v3).
    payload = _load_real_payload()
    payload["contract_version"] = "trade_lab_contract_v2"
    target = tmp_path / "strategy.json"
    _write_strategy(target, payload)

    with pytest.raises(ContractError, match="unsupported contract_version"):
        load_strategy_contract(target)


def test_load_rejects_missing_required_section(tmp_path: Path) -> None:
    payload = _load_real_payload()
    del payload["feature_set"]
    target = tmp_path / "strategy.json"
    _write_strategy(target, payload)

    with pytest.raises(ContractError, match="invalid strategy contract"):
        load_strategy_contract(target)


def test_load_rejects_section_failing_the_plugin_model(tmp_path: Path) -> None:
    # v3: the partition moved into the section; a section subtree the plugin's
    # SectionModel rejects (unknown key) dies at the hook.
    payload = _load_real_payload()
    payload["section"]["bogus_key"] = 1  # type: ignore[index]
    target = tmp_path / "strategy.json"
    _write_strategy(target, payload)

    with pytest.raises(ContractError, match="invalid strategy section"):
        load_strategy_contract(target, validate_section_via_registry=True)


def test_load_rejects_non_contiguous_class_map(tmp_path: Path) -> None:
    payload = _load_real_payload()
    payload["class_map"] = {"0": "tradeable_reversal", "2": "aggressive_blowthrough"}
    target = tmp_path / "strategy.json"
    _write_strategy(target, payload)

    with pytest.raises(ContractError, match="invalid strategy contract"):
        load_strategy_contract(target)


def test_load_rejects_unknown_extra_keys(tmp_path: Path) -> None:
    payload = _load_real_payload()
    payload["surprise_field"] = True
    target = tmp_path / "strategy.json"
    _write_strategy(target, payload)

    with pytest.raises(ContractError, match="invalid strategy contract"):
        load_strategy_contract(target)


def test_load_rejects_non_json(tmp_path: Path) -> None:
    target = tmp_path / "strategy.json"
    target.write_text("{not json", encoding="utf-8")

    with pytest.raises(ContractError, match="not valid JSON"):
        load_strategy_contract(target)


def test_registry_returns_empty_without_configured_path() -> None:
    assert discover_model_bundles(None) == []


def test_registry_returns_empty_for_missing_root(tmp_path: Path) -> None:
    assert discover_model_bundles(tmp_path / "does-not-exist") == []


def test_registry_discovers_bundle_without_exposing_paths(tmp_path: Path) -> None:
    model_id = "NQ_20260405_147t_5m_30m_multiclass-iterations800_depth4"
    _write_bundle(tmp_path, model_id)

    bundles = discover_model_bundles(tmp_path)

    assert len(bundles) == 1
    bundle = bundles[0]
    assert isinstance(bundle, ModelBundle)
    assert bundle.model_id == model_id
    assert bundle.training_mode == "dashboard_utility"
    assert bundle.feature_count == 6
    assert bundle.class_map[0] == "tradeable_reversal"
    assert bundle.has_checksum is True
    assert bundle.validation_ok is True

    serialized = json.dumps(asdict(bundle), default=str).lower()
    assert str(tmp_path).lower().replace("\\", "/") not in serialized.replace("\\", "/")
    assert "c:\\users" not in serialized
    assert ".cbm" not in serialized


def test_registry_flags_feature_metadata_mismatch(tmp_path: Path) -> None:
    _write_bundle(
        tmp_path,
        "bundle-mismatch",
        selected_features=["int_time_beyond_level", "int_time_within_2pts"],
    )

    bundles = discover_model_bundles(tmp_path)

    assert len(bundles) == 1
    assert bundles[0].validation_ok is False
    assert "selected_features" in bundles[0].validation_detail


def test_registry_skips_bundle_with_invalid_contract(tmp_path: Path) -> None:
    payload = _load_real_payload()
    payload["contract_version"] = "wrong"
    _write_bundle(tmp_path, "bad-contract", strategy_payload=payload)

    assert discover_model_bundles(tmp_path) == []


def test_registry_skips_incomplete_bundle(tmp_path: Path) -> None:
    directory = tmp_path / "incomplete"
    directory.mkdir()
    (directory / "model.cbm").write_bytes(b"x")
    # Missing metadata.json and strategy.json.

    assert discover_model_bundles(tmp_path) == []


def test_registry_rejects_path_like_directory_name(tmp_path: Path) -> None:
    # A real path-like id cannot be a directory name on disk, so assert the guard
    # used by discovery rejects the dangerous shapes directly.
    for unsafe in ("../escape", "a/b", "a\\b", "C:bundle", "..", ""):
        assert is_safe_model_id(unsafe) is False
    assert is_safe_model_id("NQ_20260405-iter800_depth4") is True


def test_registry_omits_strategy_json_only_directory(tmp_path: Path) -> None:
    directory = tmp_path / "no-model"
    directory.mkdir()
    _write_strategy(directory / "strategy.json", _load_real_payload())
    (directory / "metadata.json").write_text(
        json.dumps({"selected_features": list(_load_real_payload()["feature_set"]["names"])}),  # type: ignore[index]
        encoding="utf-8",
    )
    # No model.cbm present.

    assert discover_model_bundles(tmp_path) == []
