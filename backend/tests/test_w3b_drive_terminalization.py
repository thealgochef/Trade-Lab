"""W3b regression — ``_drive`` must terminate on any worker-task outcome.

The 2025-12-18 wedge: the committed ``_drive`` decided completion solely by
busy-polling ``replay.status().state``. That state only advances to a terminal
value from inside the replay's background ``asyncio.Task``; if the task finishes
WITHOUT writing a terminal state — e.g. a ``BaseException`` escapes the core's
``except Exception`` — ``status()`` reports ``running`` forever and ``_drive``
spins on ``asyncio.sleep(0)`` (zero reads, ~1.4 cores, IOCP "other ops" flood).

These tests pin the fix WITHOUT a multi-GB replay by driving ``_drive`` against a
stub service whose background task ends in each pathological way. Every case is
wrapped in ``asyncio.wait_for(..., timeout=…)`` so a regression to the old
status-only busy-poll HANGS and trips ``TimeoutError`` -> the test fails loudly.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from trade_lab.services.replay import ReplayState, ReplayStatus  # noqa: E402
from w3b import headless_replay  # noqa: E402
from w3b.headless_replay import ReplayTaskFailed, _drive  # noqa: E402

# Generous relative to the stub's sub-second behaviour; small enough that a
# reverted busy-poll _drive trips it quickly instead of wedging the suite.
_GUARD_TIMEOUT = 5.0


class _BaseBoom(BaseException):
    """A BaseException (NOT an Exception) — mirrors what escapes the core's
    ``except Exception`` and strands the state machine at RUNNING."""


class _StubReplay:
    """Minimal surface ``_drive`` touches: ``start`` (spawns a Task), ``status``,
    ``_task``, ``strategy_core_replay`` (used only by the diagnostic dump)."""

    def __init__(self, worker) -> None:
        self._worker = worker
        self._task: asyncio.Task[None] | None = None
        self.strategy_core_replay = None
        self._state = ReplayState.IDLE
        self._events = 0

    def status(self) -> ReplayStatus:
        return ReplayStatus(
            state=self._state, events_processed=self._events, warnings_recorded=0
        )

    async def start(self, source, config) -> None:  # noqa: ANN001 - test stub
        self._state = ReplayState.RUNNING
        self._task = asyncio.create_task(self._worker(self))


async def _run(stub: _StubReplay):
    return await asyncio.wait_for(_drive(stub, None, None), timeout=_GUARD_TIMEOUT)


def test_drive_wraps_nonexception_baseexception_as_replaytaskfailed() -> None:
    """Worker dies via a (non-interrupt) BaseException, leaving state RUNNING (the
    wedge). _drive must terminate promptly AND wrap it as the catchable
    ReplayTaskFailed (preserving the cause) so run_window's `except Exception`
    can mark the day RED instead of the run aborting on a raw BaseException."""

    async def worker(stub: _StubReplay) -> None:
        await asyncio.sleep(0)
        raise _BaseBoom("simulated core abort")

    with pytest.raises(ReplayTaskFailed) as ei:
        asyncio.run(_run(_StubReplay(worker)))
    assert isinstance(ei.value.__cause__, _BaseBoom)


def test_drive_wraps_cancellederror_as_replaytaskfailed() -> None:
    """A cancelled replay task (asyncio.CancelledError, a BaseException) must
    become the catchable ReplayTaskFailed — NOT propagate raw and abort the run."""

    async def worker(stub: _StubReplay) -> None:
        await asyncio.sleep(0)
        raise asyncio.CancelledError()

    with pytest.raises(ReplayTaskFailed):
        asyncio.run(_run(_StubReplay(worker)))


def test_drive_reraises_ordinary_exception_raw() -> None:
    """An ordinary Exception from the task (e.g. the resolver-flush tail) is
    re-raised as-is — already catchable by run_window -> RED + continue."""

    async def worker(stub: _StubReplay) -> None:
        await asyncio.sleep(0)
        raise ValueError("tail failure")

    with pytest.raises(ValueError, match="tail failure"):
        asyncio.run(_run(_StubReplay(worker)))


def test_drive_propagates_keyboardinterrupt_raw() -> None:
    """A genuine interrupt must propagate raw (abort the run), never be wrapped
    into a catchable day-level failure."""

    async def worker(stub: _StubReplay) -> None:
        await asyncio.sleep(0)
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(_run(_StubReplay(worker)))
    # And it is NOT a ReplayTaskFailed (i.e. not swallowed as a RED-able day error).
    assert not issubclass(KeyboardInterrupt, ReplayTaskFailed)


def test_drive_propagates_systemexit_raw() -> None:
    """SystemExit must propagate raw (abort the run), never be wrapped."""

    async def worker(stub: _StubReplay) -> None:
        await asyncio.sleep(0)
        raise SystemExit(2)

    with pytest.raises(SystemExit):
        asyncio.run(_run(_StubReplay(worker)))


def test_drive_raises_on_silent_nonterminal_completion() -> None:
    """Worker returns normally but never wrote a terminal state. _drive must
    detect the non-terminal completion and raise, not spin."""

    async def worker(stub: _StubReplay) -> None:
        await asyncio.sleep(0)  # returns; stub._state stays RUNNING

    with pytest.raises(RuntimeError, match="non-terminal"):
        asyncio.run(_run(_StubReplay(worker)))


def test_drive_returns_terminal_state_on_clean_completion() -> None:
    """The happy path: worker reaches COMPLETED -> _drive returns it."""

    async def worker(stub: _StubReplay) -> None:
        await asyncio.sleep(0)
        stub._events = 1234
        stub._state = ReplayState.COMPLETED

    assert asyncio.run(_run(_StubReplay(worker))) is ReplayState.COMPLETED


def test_drive_watchdog_fires_on_frozen_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuinely-suspended worker (alive, frozen events, non-terminal) must be
    bounded by the no-progress watchdog rather than hang indefinitely."""

    monkeypatch.setattr(headless_replay, "_DRIVE_WATCHDOG_SECONDS", 0.3)
    monkeypatch.setattr(headless_replay, "_DRIVE_POLL_SECONDS", 0.02)

    async def worker(stub: _StubReplay) -> None:
        await asyncio.Event().wait()  # never set -> suspended forever, events frozen

    with pytest.raises(RuntimeError, match="watchdog"):
        asyncio.run(_run(_StubReplay(worker)))
