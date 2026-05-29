#!/usr/bin/env python3
"""Safe local preflight checks for manual live Databento validation.

The script only performs GET/readiness checks. It never reads `.env`, never prints
secret values, and never calls the live start endpoint.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


DEFAULT_BACKEND = "http://127.0.0.1:8001"
DEFAULT_FRONTEND = "http://127.0.0.1:5174"
CHECK_PATHS = ("/health", "/api/v1/status", "/api/v1/live/status")
SECRET_FIELD_FRAGMENTS = ("api_key", "token", "secret", "password", "credential")


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    status_code: int | None
    message: str
    payload: Any | None = None


def sanitize_payload(value: Any) -> Any:
    """Recursively redact secret-shaped fields while preserving safe booleans."""

    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(fragment in lowered for fragment in SECRET_FIELD_FRAGMENTS):
                sanitized[key] = item if isinstance(item, bool) else "<redacted>"
            else:
                sanitized[key] = sanitize_payload(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    return value


def build_url(base_url: str, path: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def fetch_json(url: str, timeout: float) -> tuple[int, Any]:
    request = Request(url, method="GET")
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - operator supplied local URL
        status_code = response.getcode()
        body = response.read().decode("utf-8", errors="replace")
    if not body:
        return status_code, None
    return status_code, json.loads(body)


def check_json_endpoint(name: str, url: str, timeout: float) -> CheckResult:
    try:
        status_code, payload = fetch_json(url, timeout)
    except HTTPError as exc:
        return CheckResult(name, False, exc.code, f"HTTP {exc.code}: {exc.reason}")
    except (URLError, TimeoutError, OSError) as exc:
        return CheckResult(name, False, None, exc.__class__.__name__)
    except json.JSONDecodeError as exc:
        return CheckResult(name, False, None, f"invalid JSON: {exc.msg}")

    ok = 200 <= status_code < 300
    return CheckResult(name, ok, status_code, "ok" if ok else "unexpected status", sanitize_payload(payload))


def check_frontend(url: str, timeout: float) -> CheckResult:
    try:
        request = Request(url, method="GET")
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - operator supplied local URL
            status_code = response.getcode()
    except HTTPError as exc:
        return CheckResult("frontend", False, exc.code, f"HTTP {exc.code}: {exc.reason}")
    except (URLError, TimeoutError, OSError) as exc:
        return CheckResult("frontend", False, None, f"not reachable ({exc.__class__.__name__})")

    ok = 200 <= status_code < 400
    return CheckResult("frontend", ok, status_code, "reachable" if ok else "unexpected status")


def run_checks(backend: str, frontend: str | None, timeout: float) -> list[CheckResult]:
    results = [
        check_json_endpoint(path, build_url(backend, path), timeout) for path in CHECK_PATHS
    ]
    if frontend:
        results.append(check_frontend(frontend, timeout))
    return results


def print_results(results: list[CheckResult]) -> None:
    for result in results:
        status = "PASS" if result.ok else "WARN" if result.name == "frontend" else "FAIL"
        code = "n/a" if result.status_code is None else str(result.status_code)
        print(f"[{status}] {result.name} status={code} {result.message}")
        if result.payload is not None:
            print(json.dumps(result.payload, indent=2, sort_keys=True))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Safe readiness checks for manual live Databento validation."
    )
    parser.add_argument("--backend", default=DEFAULT_BACKEND, help="Backend base URL")
    parser.add_argument(
        "--frontend",
        default=DEFAULT_FRONTEND,
        help="Frontend URL to probe; pass an empty string to skip",
    )
    parser.add_argument("--timeout", type=float, default=3.0, help="Per-request timeout seconds")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    frontend = args.frontend or None
    results = run_checks(args.backend, frontend, args.timeout)
    print_results(results)
    backend_failed = any(not result.ok for result in results if result.name != "frontend")
    return 1 if backend_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
