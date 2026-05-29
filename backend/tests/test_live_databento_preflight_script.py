import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "live_databento_preflight.py"
SPEC = importlib.util.spec_from_file_location("live_databento_preflight", SCRIPT_PATH)
assert SPEC is not None
preflight = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = preflight
SPEC.loader.exec_module(preflight)


def test_sanitize_payload_redacts_secret_shaped_values_but_keeps_booleans() -> None:
    payload = {
        "api_key_configured": True,
        "operator_token": "secret-token",
        "nested": {"credential_value": "abc", "safe": "ok"},
        "items": [{"password": "pw"}],
    }

    sanitized = preflight.sanitize_payload(payload)

    assert sanitized == {
        "api_key_configured": True,
        "operator_token": "<redacted>",
        "nested": {"credential_value": "<redacted>", "safe": "ok"},
        "items": [{"password": "<redacted>"}],
    }


def test_run_checks_only_uses_safe_read_endpoints(monkeypatch) -> None:
    called: list[str] = []

    def fake_check_json_endpoint(name: str, url: str, timeout: float):
        called.append(url)
        return preflight.CheckResult(name, True, 200, "ok", {})

    monkeypatch.setattr(preflight, "check_json_endpoint", fake_check_json_endpoint)

    results = preflight.run_checks("http://backend.test", None, 1.0)

    assert [result.name for result in results] == [
        "/health",
        "/api/v1/status",
        "/api/v1/live/status",
    ]
    assert all("/api/v1/live/start" not in url for url in called)


def test_main_allows_frontend_unreachable_without_failing_backend(monkeypatch, capsys) -> None:
    def fake_run_checks(backend: str, frontend: str | None, timeout: float):
        assert backend == "http://backend.test"
        assert frontend == "http://frontend.test"
        assert timeout == 1.0
        return [
            preflight.CheckResult("/health", True, 200, "ok", {"ok": True}),
            preflight.CheckResult("frontend", False, None, "not reachable"),
        ]

    monkeypatch.setattr(preflight, "run_checks", fake_run_checks)

    exit_code = preflight.main(
        ["--backend", "http://backend.test", "--frontend", "http://frontend.test", "--timeout", "1"]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "[PASS] /health" in output
    assert "[WARN] frontend" in output
