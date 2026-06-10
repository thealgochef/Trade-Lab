"""Observation-engine lifecycle guards.

The SessionLevelEngine tests that lived here died with the engine in D2 (level
derivation is Strategy-Core's; see ``tests/test_strategy_core_acceptance.py`` for
the guard). Observation lifecycle behavior driven from REAL Strategy-Core touches
is covered by the runtime/inference suites (e.g. ``test_inference_engine.py``).
"""

from datetime import datetime, timedelta

import pytest

from trade_lab.domain.observations import ObservationEngine


def test_observation_engine_rejects_invalid_duration_and_naive_refresh_time() -> None:
    with pytest.raises(ValueError, match="positive"):
        ObservationEngine(timedelta(0))

    with pytest.raises(ValueError, match="timezone-aware"):
        ObservationEngine().refresh(datetime(2026, 1, 5, 0, 0))
