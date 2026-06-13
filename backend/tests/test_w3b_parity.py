"""W3b P1/P2 — batch<->serving parity standing test (local, data-gated).

The per-touch parity gate's acceptance bound is ``DayDiff.green`` (zero mismatches
on every axis). Proving it requires replaying the real serving stack over the
QL mbp10 store (multi-GB per day) with the W3 bundle staged — a local, heavy
dependency, exactly like the Strategy-Core ``validation/`` parity tests. So:

  * tests skip cleanly when the QL store / bundle is absent;
  * the replay-driven parity assertion is additionally gated behind
    ``W3B_RUN_REPLAY=1`` (a full day is event-decode-bound, minutes of wall time);
    the full 73-day gate is run via ``python -m w3b.run_window``.

The non-replay tests (provenance + offline scoring) run whenever the store is
present and are fast.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _window_or_skip():
    try:
        from w3b.window import BUNDLE_ID, resolve_window
    except Exception as exc:  # pragma: no cover - import guard
        pytest.skip(f"w3b harness unavailable: {exc}")
    try:
        window = resolve_window()
    except Exception as exc:
        pytest.skip(f"QL D-036 window unresolvable (store absent?): {exc}")
    if not (window.models_path / BUNDLE_ID / "model.cbm").is_file():
        pytest.skip(f"bundle {BUNDLE_ID} not staged at {window.models_path}")
    if not any(window.has_cache(d) for d in window.window_dates):
        pytest.skip("no D-036 caches present in the QL store")
    return window


_SMOKE_DAY = os.environ.get("W3B_SMOKE_DAY", "2026-02-13")


def test_window_provenance():
    """The reused QL machinery resolves the exact W3 cache build (73 days, tag)."""
    window = _window_or_skip()
    from w3b.window import EXPECTED_CACHE_TAG

    assert window.cache_tag == EXPECTED_CACHE_TAG
    assert len(window.window_dates) == 73
    assert window.window_dates[0] == "2025-11-21"
    assert window.window_dates[-1] == "2026-02-13"


def test_offline_scorer_loads_and_scores_cache_rows():
    """P2 wiring: the bundle's CatBoost binary scores cached feature vectors,
    yielding a proper 3-class distribution over the contract labels."""
    window = _window_or_skip()
    from w3b.parity import CLASS_MAP, OfflineScorer, load_training_touches

    day = _SMOKE_DAY if window.has_cache(_SMOKE_DAY) else next(
        d for d in window.window_dates if window.has_cache(d)
    )
    touches = load_training_touches(window, day)
    assert touches, f"expected cached touches for {day}"
    scorer = OfflineScorer.for_bundle(window)
    proba = scorer.score(touches[0].features)
    assert set(proba) == set(CLASS_MAP.values())
    assert abs(sum(proba.values()) - 1.0) < 1e-6
    assert all(0.0 <= p <= 1.0 for p in proba.values())


@pytest.mark.skipif(
    os.environ.get("W3B_RUN_REPLAY") != "1",
    reason="set W3B_RUN_REPLAY=1 to run the replay-driven parity assertion "
    "(heavy: full-day mbp10 decode); full window via `python -m w3b.run_window`",
)
def test_smoke_day_parity_is_green(tmp_path):
    """Replay one D-036 day through the serving stack and assert per-touch parity
    (features bit-exact, label/level/excursions equal, model scoring equal)."""
    window = _window_or_skip()
    if not window.has_cache(_SMOKE_DAY):
        pytest.skip(f"{_SMOKE_DAY} has no cache (thin day)")
    from w3b.headless_replay import replay_day_to_journal
    from w3b.parity import OfflineScorer, diff_day

    journal_dir = tmp_path / _SMOKE_DAY
    replay_day_to_journal(_SMOKE_DAY, journal_dir=journal_dir, window=window)
    diff = diff_day(
        _SMOKE_DAY,
        journal_dir / f"{_SMOKE_DAY}.jsonl",
        window=window,
        scorer=OfflineScorer.for_bundle(window),
    )
    assert diff.matched == diff.training_touches, diff.summary()
    assert diff.green, diff.summary()
