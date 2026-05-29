from pathlib import Path

import pytest
from pydantic import ValidationError

from trade_lab.config import Settings


def test_settings_defaults_are_sane_without_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRADE_LAB_BACKEND_PORT", raising=False)
    monkeypatch.delenv("TRADE_LAB_DATABENTO_API_KEY", raising=False)

    settings = Settings(_env_file=None)

    assert settings.backend_host == "127.0.0.1"
    assert settings.backend_port == 8001
    assert settings.allowed_origin_values == ("http://localhost:5174", "http://127.0.0.1:5174")
    assert settings.instrument_root == "NQ"
    assert settings.observation_duration_seconds == 300
    assert settings.tick_timeframes == (147, 987, 2000)
    assert settings.databento_api_key is None


def test_databento_secret_is_not_leaked_in_repr_dump_or_safe_dict() -> None:
    settings = Settings(_env_file=None, databento_api_key="super-secret-key")

    assert "super-secret-key" not in repr(settings)
    assert "databento_api_key" not in settings.safe_dict()
    assert settings.model_dump(mode="json")["databento_api_key"] == "********"
    assert "super-secret-key" not in settings.model_dump_json()


def test_trade_lab_env_prefix_overrides_settings_without_reading_env_file(monkeypatch) -> None:
    monkeypatch.setenv("TRADE_LAB_BACKEND_PORT", "9000")
    monkeypatch.setenv("TRADE_LAB_DATA_PATH", str(Path("synthetic")))
    monkeypatch.setenv("TRADE_LAB_ALLOWED_ORIGINS", "http://localhost:3000, http://127.0.0.1:3000")
    monkeypatch.setenv("UNRELATED_BACKEND_PORT", "1234")

    settings = Settings(_env_file=None)

    assert settings.backend_port == 9000
    assert settings.data_path == Path("synthetic")
    assert settings.allowed_origin_values == ("http://localhost:3000", "http://127.0.0.1:3000")


def test_settings_loads_explicit_dotenv_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TRADE_LAB_BACKEND_PORT", raising=False)
    monkeypatch.delenv("TRADE_LAB_DATABENTO_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TRADE_LAB_BACKEND_PORT=9999\n"
        "TRADE_LAB_DATABENTO_API_KEY=dotenv-secret\n"
        "TRADE_LAB_DATABENTO_LIVE_ENABLED=true\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.backend_port == 9999
    assert settings.databento_api_key is not None
    assert settings.databento_api_key.get_secret_value() == "dotenv-secret"
    assert settings.databento_live_enabled is True


def test_settings_does_not_load_dotenv_from_working_directory_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TRADE_LAB_BACKEND_PORT", raising=False)
    (tmp_path / ".env").write_text("TRADE_LAB_BACKEND_PORT=9100\n", encoding="utf-8")
    repo_env_file = tmp_path / "repo" / "backend" / ".env"
    monkeypatch.setitem(Settings.model_config, "env_file", repo_env_file)

    settings = Settings()

    assert Path(Settings.model_config["env_file"]).is_absolute()
    assert Path(Settings.model_config["env_file"]).name == ".env"
    assert Path(Settings.model_config["env_file"]).parent.name == "backend"
    assert settings.backend_port == 8001


def test_settings_loads_backend_dotenv_independent_of_cwd_without_secret_leakage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / "backend" / ".env"
    env_file.parent.mkdir()
    env_file.write_text(
        "TRADE_LAB_BACKEND_PORT=9188\n"
        "TRADE_LAB_DATA_PATH=fixture-data\n"
        "TRADE_LAB_DATABENTO_API_KEY=dotenv-secret\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path / "backend")
    monkeypatch.delenv("TRADE_LAB_BACKEND_PORT", raising=False)
    monkeypatch.delenv("TRADE_LAB_DATA_PATH", raising=False)
    monkeypatch.delenv("TRADE_LAB_DATABENTO_API_KEY", raising=False)
    original_env_file = Settings.model_config["env_file"]
    monkeypatch.setitem(Settings.model_config, "env_file", env_file)

    try:
        settings = Settings()
    finally:
        monkeypatch.setitem(Settings.model_config, "env_file", original_env_file)

    assert settings.backend_port == 9188
    assert settings.data_path == Path("fixture-data")
    assert "dotenv-secret" not in repr(settings)
    assert "databento_api_key" not in settings.safe_dict()


def test_environment_variables_override_dotenv_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TRADE_LAB_BACKEND_PORT=9999\n"
        "TRADE_LAB_DATABENTO_REQUESTED_SYMBOL=NQZ6\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("TRADE_LAB_DATABENTO_REQUESTED_SYMBOL", raising=False)
    monkeypatch.setenv("TRADE_LAB_BACKEND_PORT", "9001")

    settings = Settings(_env_file=env_file)

    assert settings.backend_port == 9001
    assert settings.databento_requested_symbol == "NQZ6"


def test_empty_dotenv_secrets_are_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("TRADE_LAB_DATABENTO_API_KEY", raising=False)
    monkeypatch.delenv("TRADE_LAB_OPERATOR_TOKEN", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TRADE_LAB_DATABENTO_API_KEY=\nTRADE_LAB_OPERATOR_TOKEN=   \n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.databento_api_key is None
    assert settings.operator_token is None


def test_databento_defaults_and_env_overrides_are_safe(monkeypatch) -> None:
    monkeypatch.setenv("TRADE_LAB_DATABENTO_DATASET", "GLBX.MDP3")
    monkeypatch.setenv("TRADE_LAB_DATABENTO_REQUESTED_SYMBOL", "NQZ6")
    monkeypatch.setenv("TRADE_LAB_DATABENTO_TRADE_SCHEMA", "trades")
    monkeypatch.setenv("TRADE_LAB_DATABENTO_QUOTE_SCHEMA", "tbbo")
    monkeypatch.setenv("TRADE_LAB_DATABENTO_CONTEXT_SCHEMAS", '["definition","status"]')

    settings = Settings(_env_file=None)

    assert settings.databento_dataset == "GLBX.MDP3"
    assert settings.databento_requested_symbol == "NQZ6"
    assert settings.databento_stype_in == "continuous"
    assert settings.databento_trade_schema == "trades"
    assert settings.databento_quote_schema == "tbbo"
    assert settings.databento_context_schemas == ("definition", "status")
    assert "databento_api_key" not in settings.safe_dict()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"databento_dataset": "XNAS.ITCH"},
        {"databento_dataset": "G" * 40},
        {"databento_requested_symbol": "NQ;DROP"},
        {"databento_requested_symbol": "N" * 40},
        {"databento_stype_in": "symbol;drop"},
        {"databento_stype_in": "raw_symbol_with_unbounded_name"},
        {"databento_trade_schema": "mbo"},
        {"databento_trade_schema": "mbp-1"},
        {"databento_quote_schema": "mbo"},
        {"databento_quote_schema": "trades"},
        {"databento_context_schemas": ("definition", "mbo")},
        {"databento_context_schemas": ("definition", "trades")},
        {"databento_context_schemas": ("status",) * 9},
    ],
)
def test_live_settings_reject_invalid_or_unbounded_values(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **kwargs)
