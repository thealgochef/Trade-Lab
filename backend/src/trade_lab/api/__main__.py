"""Run with: python -m trade_lab.api"""

import logging
import sys
import time

import uvicorn

from trade_lab.config import load_settings

#: Marker attribute so repeated configure_logging calls never stack handlers.
_HANDLER_MARKER = "_trade_lab_log_handler"


def configure_logging(level_name: str) -> None:
    """WARM-FIX P5: give the backend a real logging surface (WEDGE_CAPTURE.md §A.2).

    Pre-fix the dev server ran uvicorn with no log config: only uvicorn's own
    loggers had handlers, everything else (trade_lab.*, the databento SDK — the
    entire live subscribe handshake) fell through to Python's WARNING-only
    lastResort handler, so the live wedge was silent by construction.

    Shape: root -> stderr with UTC timestamps at ``level_name`` (default INFO,
    env ``TRADE_LAB_LOG_LEVEL``); ``uvicorn.access`` capped at WARNING (request
    noise); the ``databento`` logger at INFO, raised to DEBUG only when the env
    level is DEBUG (the full session-handshake trace used by the wedge capture).
    Idempotent: repeat calls re-level but never stack handlers. uvicorn's default
    dictConfig keeps ``disable_existing_loggers=False`` and never touches root
    handlers, so this survives ``uvicorn.run()``.
    """

    level = getattr(logging, level_name.strip().upper(), None)
    if not isinstance(level, int):
        level = logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    if not any(getattr(handler, _HANDLER_MARKER, False) for handler in root.handlers):
        handler = logging.StreamHandler(sys.stderr)
        formatter = logging.Formatter(
            "%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        formatter.converter = time.gmtime
        handler.setFormatter(formatter)
        setattr(handler, _HANDLER_MARKER, True)
        root.addHandler(handler)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("databento").setLevel(
        logging.DEBUG if level <= logging.DEBUG else logging.INFO
    )


def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    uvicorn.run(
        "trade_lab.api.app:create_app",
        factory=True,
        host=settings.backend_host,
        port=settings.backend_port,
    )


if __name__ == "__main__":
    main()
