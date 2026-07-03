"""W3b regression — run_window per-day exception handling (deterministic, no replay).

Reconciles the `_drive`/`run_window` exception contract WITHOUT replaying any real
market data: a fake `_process_day` is injected per day to simulate each failure
mode, and `resolve_window` is stubbed so no QL store / DuckDB scan is needed.

Contract under test:
  * ordinary ``Exception``         -> day marked RED, run continues, report emitted;
  * ``ReplayTaskFailed`` (the form `_drive` wraps a cancelled / non-terminal /
    non-Exception-BaseException replay task into) -> RED, run continues;
  * raw ``asyncio.CancelledError`` reaching run_window -> NOT caught -> aborts
    (this is exactly why `_drive` converts it to ``ReplayTaskFailed`` upstream);
  * ``KeyboardInterrupt`` / ``SystemExit`` -> propagate, abort, never RED.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import w3b.run_window as rw  # noqa: E402
from w3b.headless_replay import ReplayTaskFailed  # noqa: E402
from w3b.parity import DayDiff  # noqa: E402

_D1 = "2026-01-01"
_D2 = "2026-01-02"


def _install(monkeypatch: pytest.MonkeyPatch, behaviors: dict) -> None:
    """Stub resolve_window (no store) and _process_day (per-day behavior).

    behaviors[day] is either a DayDiff to return or a BaseException to raise.
    """

    monkeypatch.setattr(
        rw, "resolve_window", lambda: types.SimpleNamespace(window_dates=tuple(behaviors))
    )

    def fake_process_day(date_str, journal_base, window, *, score, resume):
        outcome = behaviors[date_str]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(rw, "_process_day", fake_process_day)


def _run(tmp_path: Path):
    return rw.run_window(
        [_D1, _D2], journal_base=tmp_path, workers=1, score=False, resume=False
    )


# ── A. Ordinary Exception -> RED + report + continue ──────────────────────────
def test_A_ordinary_exception_marks_red_emits_report_and_continues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(monkeypatch, {_D1: ValueError("boom"), _D2: DayDiff(day=_D2)})

    results = _run(tmp_path)  # does NOT raise

    by_day = {d.day: d for d in results}
    assert set(by_day) == {_D1, _D2}            # both days processed (continued)
    assert not by_day[_D1].green                # day 1 RED
    assert any("ValueError: boom" in n for n in by_day[_D1].notes)
    assert by_day[_D2].green                    # day 2 ran and is green

    text, green = rw.report(results, None)       # report is built...
    assert green is False
    assert "VERDICT: RED" in text
    out = tmp_path / "report.txt"
    out.write_text(text + "\n", encoding="utf-8")  # ...and emitted
    written = out.read_text(encoding="utf-8")
    assert written.strip()                  # report file is non-empty
    assert "VERDICT: RED" in written


# ── B. CancelledError ─────────────────────────────────────────────────────────
def test_B_wrapped_replaytaskfailed_marks_red_and_continues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The form `_drive` produces for a cancelled/non-terminal replay task is
    catchable -> day RED, run continues."""

    _install(
        monkeypatch, {_D1: ReplayTaskFailed("replay task was cancelled"), _D2: DayDiff(day=_D2)}
    )

    results = _run(tmp_path)

    by_day = {d.day: d for d in results}
    assert not by_day[_D1].green
    assert any("ReplayTaskFailed" in n for n in by_day[_D1].notes)
    assert by_day[_D2].green


def test_B_raw_cancellederror_is_not_swallowed_and_aborts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A RAW asyncio.CancelledError reaching run_window is NOT caught by
    `except Exception` and aborts the run — which is precisely why `_drive`
    must convert cancellation to ReplayTaskFailed upstream (see the _drive tests)."""

    _install(monkeypatch, {_D1: asyncio.CancelledError(), _D2: DayDiff(day=_D2)})

    with pytest.raises(asyncio.CancelledError):
        _run(tmp_path)


# ── C. KeyboardInterrupt -> propagate, abort, never RED ───────────────────────
def test_C_keyboardinterrupt_propagates_and_aborts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(monkeypatch, {_D1: KeyboardInterrupt(), _D2: DayDiff(day=_D2)})

    with pytest.raises(KeyboardInterrupt):
        _run(tmp_path)


# ── D. SystemExit -> propagate, abort, never RED ──────────────────────────────
def test_D_systemexit_propagates_and_aborts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(monkeypatch, {_D1: SystemExit(2), _D2: DayDiff(day=_D2)})

    with pytest.raises(SystemExit):
        _run(tmp_path)
