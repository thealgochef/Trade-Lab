"""Typed backend settings with an explicit secrets boundary.

Pydantic is used only at configuration/API boundaries. Hot-path market events and
engines avoid Pydantic allocation overhead by design.
"""

import re
from pathlib import Path
from typing import Any, ClassVar

from pydantic import Field, SecretStr, field_serializer, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """Runtime settings loaded from local `.env` and `TRADE_LAB_` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="TRADE_LAB_",
        env_file=_BACKEND_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    backend_host: str = "127.0.0.1"
    backend_port: int = 8001
    allowed_origins: str = "http://localhost:5174,http://127.0.0.1:5174"
    data_path: Path | None = None

    # Root directory holding model bundles (one subdir per bundle, each with
    # model.cbm + metadata.json + strategy.json). Discovered by the ModelRegistry;
    # request-provided paths are never accepted, mirroring TRADE_LAB_DATA_PATH.
    models_path: Path | None = None

    # Secret values stay backend-only. They are excluded from repr and masked during
    # serialization to prevent accidental logging.
    databento_api_key: SecretStr | None = Field(default=None, repr=False)
    operator_token: SecretStr | None = Field(default=None, repr=False)

    databento_live_enabled: bool = False
    databento_dataset: str = Field(default="GLBX.MDP3", min_length=1, max_length=32)
    databento_requested_symbol: str = Field(default="NQ.c.0", min_length=1, max_length=32)
    databento_stype_in: str = Field(default="continuous", min_length=1, max_length=16)
    databento_trade_schema: str = Field(default="trades", min_length=1, max_length=16)
    databento_quote_schema: str = Field(default="mbp-1", min_length=1, max_length=16)
    databento_context_schemas: tuple[str, ...] = Field(
        default=("definition", "status", "statistics"), max_length=8
    )

    market_data_dataset: str | None = None
    market_data_schema: str | None = None
    front_month_symbol: str | None = "NQ.c.0"
    instrument_root: str = "NQ"

    observation_duration_seconds: int = 300
    tick_timeframes: tuple[int, ...] = (147, 987, 2000)

    # Rolling L1/L0 context retention for pre-touch order-flow features. Inference
    # fires at observation completion (~interaction window after the touch) but
    # approach features reach back approach_window before the touch, so retention must
    # cover approach + interaction + margin (default 30 + 5 + 10 = 45 minutes).
    market_context_retention_minutes: int = Field(default=45, ge=45, le=240)

    # Live warm-up: on live start, pre-fill the chart with the last N completed sessions
    # of front-month tick bars fetched from the Databento Historical API (L0/L1 only, to
    # match what live streaming provides). Bounded per timeframe to cap snapshot size.
    seed_enabled: bool = True
    seed_lookback_days: int = Field(default=3, ge=1, le=30)
    seed_max_bars_per_timeframe: int = Field(default=2500, ge=1, le=20_000)

    _ALLOWED_DATASETS: ClassVar[set[str]] = {"GLBX.MDP3"}
    _ALLOWED_STYPE_IN: ClassVar[set[str]] = {"raw_symbol", "continuous", "instrument_id", "parent"}
    _ALLOWED_TRADE_SCHEMAS: ClassVar[set[str]] = {"trades"}
    _ALLOWED_QUOTE_SCHEMAS: ClassVar[set[str]] = {"mbp-1", "cmbp-1", "tbbo"}
    _ALLOWED_CONTEXT_SCHEMAS: ClassVar[set[str]] = {"definition", "status", "statistics"}
    _SYMBOL_RE: ClassVar[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_.:/-]+$")

    @property
    def allowed_origin_values(self) -> tuple[str, ...]:
        return tuple(origin.strip() for origin in self.allowed_origins.split(",") if origin.strip())

    @field_validator("databento_api_key", "operator_token", mode="before")
    @classmethod
    def _empty_secret_as_unset(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("databento_context_schemas", mode="before")
    @classmethod
    def _parse_context_schemas(cls, value: Any) -> tuple[str, ...] | Any:
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                return value
            return tuple(item.strip() for item in text.split(",") if item.strip())
        return value

    @field_validator("databento_dataset")
    @classmethod
    def _validate_dataset(cls, value: str) -> str:
        if value not in cls._ALLOWED_DATASETS:
            raise ValueError("unsupported Databento dataset")
        return value

    @field_validator("databento_requested_symbol")
    @classmethod
    def _validate_symbol(cls, value: str) -> str:
        if not cls._SYMBOL_RE.fullmatch(value):
            raise ValueError("invalid Databento requested symbol")
        return value

    @field_validator("databento_stype_in")
    @classmethod
    def _validate_stype_in(cls, value: str) -> str:
        lowered = value.lower()
        if lowered not in cls._ALLOWED_STYPE_IN:
            raise ValueError("unsupported Databento stype_in")
        return lowered

    @field_validator("databento_trade_schema")
    @classmethod
    def _validate_trade_schema(cls, value: str) -> str:
        lowered = value.lower()
        if lowered not in cls._ALLOWED_TRADE_SCHEMAS:
            raise ValueError("unsupported Databento trade schema")
        return lowered

    @field_validator("databento_quote_schema")
    @classmethod
    def _validate_quote_schema(cls, value: str) -> str:
        lowered = value.lower()
        if lowered not in cls._ALLOWED_QUOTE_SCHEMAS:
            raise ValueError("unsupported Databento quote schema")
        return lowered

    @field_validator("databento_context_schemas")
    @classmethod
    def _validate_context_schemas(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > 8:
            raise ValueError("too many Databento context schemas")
        normalized = tuple(item.lower() for item in value)
        if any(len(item) > 16 or item not in cls._ALLOWED_CONTEXT_SCHEMAS for item in normalized):
            raise ValueError("unsupported Databento context schema")
        return normalized

    @field_serializer("databento_api_key", "operator_token", when_used="json")
    def _mask_secret(self, value: SecretStr | None) -> str | None:
        return None if value is None else "********"

    def safe_dict(self) -> dict[str, Any]:
        """Return settings safe for diagnostics without exposing secrets."""

        return self.model_dump(mode="json", exclude={"databento_api_key", "operator_token"})


def load_settings() -> Settings:
    return Settings()
