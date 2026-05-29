import json
from datetime import UTC, date, datetime
from decimal import Decimal

from trade_lab.api import serialization
from trade_lab.api.dto import make_envelope


def test_dumps_bytes_sorts_keys_and_serializes_datetime_date_and_decimal() -> None:
    payload = {
        "z": Decimal("1.25"),
        "a": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        "m": date(2026, 1, 2),
    }

    data = serialization.dumps_bytes(payload)

    assert data.startswith(b'{"a":')
    assert json.loads(data) == {
        "a": "2026-01-02T03:04:05+00:00",
        "m": "2026-01-02",
        "z": "1.25",
    }


def test_json_fallback_path_matches_contract_when_orjson_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(serialization, "orjson", None)

    data = serialization.dumps_bytes({"b": 2, "a": datetime(2026, 1, 1, tzinfo=UTC)})

    assert data == b'{"a":"2026-01-01T00:00:00+00:00","b":2}'


def test_orjson_path_uses_sorted_keys_when_available() -> None:
    if serialization.orjson is None:
        return

    data = serialization.dumps_bytes({"b": 2, "a": 1})

    assert data == b'{"a":1,"b":2}'


def test_envelope_serializes_with_deterministic_required_keys() -> None:
    envelope = make_envelope("system.heartbeat", 1, {"status": "ok"})

    decoded = json.loads(serialization.dumps_bytes(envelope))

    assert list(decoded) == ["payload", "sequence", "server_time_utc", "type", "version"]
    assert decoded["server_time_utc"].endswith(("+00:00", "Z"))
