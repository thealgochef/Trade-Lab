"""Shared fake Databento Live SDK client for adapter tests.

WEDGE fix (see docs/archive/windows/WEDGE_CAPTURE.md): the SDK facade now (1) forces
connect+auth through the guarded private session handle before any subscription
and (2) marshals every subscribe/start onto the SDK session loop. Fakes therefore
expose the same shape the facade's guarded getattr chain expects — a ``_session``
with ``_connect(dataset=...)`` and ``_loop`` — where ``_loop`` is a REAL event
loop on a REAL daemon thread (mirroring the SDK's shared class-level loop) so the
``call_soon_threadsafe`` marshaling actually executes. Every recorded call carries
the executing thread's name, enabling the thread-identity assertions that pin the
wedge fix.
"""

import asyncio
import threading

FAKE_SESSION_THREAD_NAME = "fake_databento_live"

_session_loop = asyncio.new_event_loop()
_session_thread = threading.Thread(
    target=_session_loop.run_forever, name=FAKE_SESSION_THREAD_NAME, daemon=True
)
_start_lock = threading.Lock()


def fake_session_loop() -> asyncio.AbstractEventLoop:
    """The shared fake session loop, started lazily (like databento's Live._loop)."""

    with _start_lock:
        if not _session_thread.is_alive():
            _session_thread.start()
    return _session_loop


class FakeLiveSession:
    """The private session shape the facade's wedge-safe connect requires."""

    def __init__(self) -> None:
        self._loop = fake_session_loop()
        #: (dataset, executing thread name) per _connect call.
        self.connect_calls: list[tuple[str, str]] = []

    def _connect(self, *, dataset: str) -> None:
        self.connect_calls.append((dataset, threading.current_thread().name))


class FakeLiveClient:
    """Recording fake Live client; every call records its executing thread name."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.callbacks: list[object] = []
        self.subscriptions: list[dict[str, object]] = []
        #: (operation label, executing thread name) in call order.
        self.call_threads: list[tuple[str, str]] = []
        self.started = False
        self.stopped = False
        self._session = FakeLiveSession()

    def add_callback(self, callback: object) -> None:
        self.call_threads.append(("add_callback", threading.current_thread().name))
        self.callbacks.append(callback)

    def subscribe(self, **kwargs: object) -> None:
        self.call_threads.append(
            (f"subscribe:{kwargs.get('schema')}", threading.current_thread().name)
        )
        self.subscriptions.append(kwargs)

    def start(self) -> None:
        self.call_threads.append(("start", threading.current_thread().name))
        self.started = True

    def stop(self) -> None:
        self.stopped = True
