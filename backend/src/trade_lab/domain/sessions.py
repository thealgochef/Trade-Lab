"""Session vocabulary for Trade-Lab domain/display types.

Session CLASSIFICATION is owned by Strategy-Core (the ET engine-v3 scheme); the
adapter seam (``services/strategy_core_service.py``) maps engine session strings
onto this enum by value. The Chicago-clock classifier that used to live here
(``classify_session`` / ``SessionClassifier`` / ``to_ct``) was removed in the
2026-07 cleanup: it had no callers in the serving path and its CT windows
diverged from the canonical ET scheme (documented as
``TRADE_LAB_CT_SESSION_SCHEME`` in strategy_core.constants).
"""

from enum import StrEnum


class SessionName(StrEnum):
    ASIA = "asia"
    LONDON = "london"
    NY = "ny"
    CLOSED = "closed"
