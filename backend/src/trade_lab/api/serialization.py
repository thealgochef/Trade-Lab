"""Deterministic JSON bytes for WebSocket envelopes.

The API prefers orjson for low overhead but falls back to standard JSON so the
contract remains portable in minimal environments.
"""

import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

try:  # pragma: no cover - fallback is tested only when orjson is absent
    import orjson
except ImportError:  # pragma: no cover
    orjson = None


def _default(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"object of type {type(value).__name__} is not JSON serializable")


def dumps_bytes(payload: dict[str, Any]) -> bytes:
    if orjson is not None:
        return orjson.dumps(payload, default=_default, option=orjson.OPT_SORT_KEYS)
    return json.dumps(payload, default=_default, sort_keys=True, separators=(",", ":")).encode()
