"""WARM-FIX P5: the backend logging surface (WEDGE_CAPTURE.md §A.2).

The wedge stayed invisible partly because the deployment had no logging config:
everything below WARNING from trade_lab.* and the databento SDK went nowhere.
These tests pin the configure_logging contract and restore global state after.
"""

import logging
import sys

import pytest

from trade_lab.api.__main__ import _HANDLER_MARKER, configure_logging
from trade_lab.config import Settings


@pytest.fixture()
def _restore_logging():
    root = logging.getLogger()
    saved_root_level = root.level
    saved_handlers = list(root.handlers)
    saved_access = logging.getLogger("uvicorn.access").level
    saved_databento = logging.getLogger("databento").level
    yield
    root.setLevel(saved_root_level)
    for handler in list(root.handlers):
        if handler not in saved_handlers:
            root.removeHandler(handler)
    logging.getLogger("uvicorn.access").setLevel(saved_access)
    logging.getLogger("databento").setLevel(saved_databento)


def _own_handlers() -> list[logging.Handler]:
    return [
        handler
        for handler in logging.getLogger().handlers
        if getattr(handler, _HANDLER_MARKER, False)
    ]


def test_configure_logging_sets_the_specified_levels(_restore_logging) -> None:
    configure_logging("INFO")

    root = logging.getLogger()
    assert root.level == logging.INFO
    # Databento stays INFO at the default level; uvicorn access noise is capped.
    assert logging.getLogger("databento").level == logging.INFO
    assert logging.getLogger("uvicorn.access").level == logging.WARNING
    handlers = _own_handlers()
    assert len(handlers) == 1
    assert isinstance(handlers[0], logging.StreamHandler)
    assert handlers[0].stream is sys.stderr
    # Timestamps are part of the format (the wedge capture depends on them).
    assert "%(asctime)s" in handlers[0].formatter._fmt


def test_debug_level_raises_the_databento_logger_and_calls_are_idempotent(
    _restore_logging,
) -> None:
    configure_logging("INFO")
    configure_logging("debug")  # case-insensitive env values

    assert logging.getLogger().level == logging.DEBUG
    assert logging.getLogger("databento").level == logging.DEBUG
    # Re-configuring re-levels but never stacks a second handler.
    assert len(_own_handlers()) == 1

    configure_logging("not-a-level")  # tolerant: falls back to INFO
    assert logging.getLogger().level == logging.INFO
    assert logging.getLogger("databento").level == logging.INFO
    assert len(_own_handlers()) == 1


def test_settings_expose_the_env_tunable_log_level() -> None:
    assert Settings(_env_file=None).log_level == "INFO"
    assert Settings(_env_file=None, log_level="DEBUG").log_level == "DEBUG"


def test_main_disables_uvicorn_log_config_so_the_caps_survive(
    monkeypatch, _restore_logging
) -> None:
    """Verify fix: uvicorn's DEFAULT dictConfig names uvicorn.access and would
    re-level it back to INFO (disable_existing_loggers=False only protects
    loggers it does NOT name). main() must pass log_config=None."""

    import trade_lab.api.__main__ as entrypoint

    captured: dict = {}

    def fake_run(*args, **kwargs) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(entrypoint.uvicorn, "run", fake_run)
    monkeypatch.setattr(
        entrypoint, "load_settings", lambda: Settings(_env_file=None)
    )
    entrypoint.main()
    assert "log_config" in captured
    assert captured["log_config"] is None
