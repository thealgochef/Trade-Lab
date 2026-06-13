"""W3b shared inputs, reused verbatim from the QL cache-build provenance.

The training cache ``ml_utility_7850272e.parquet`` was built by QL's
``build_utility_dataset`` over a fixed 73-day window, threading a rolling
``prev_full_hl`` PDH/PDL seed through the days (``load_prior_day_summary`` — the
cold-start path Trade-Lab's live warm-start also uses). Two of those are shared
BOOTSTRAP INPUTS rather than things the serving engine rediscovers from a single
day's stream:

  * the window DAY SET (which dates exist / are in scope), and
  * the prior-day PDH/PDL seed entering each day.

The W3b gate CONTROLS these two inputs — by reusing the EXACT QL machinery that
produced the cache — so it can ISOLATE the question it actually asks: given the
same bootstrap, does the TL serving engine reproduce each surviving touch's
features, label, level, and model score? Everything downstream of the seed (bar
formation, touch detection, the 5 features, the honest-resolution label, the
CatBoost scoring) is reproduced INDEPENDENTLY by serving from the raw mbp10
stream — only the prior-day high/low number is fed in, and production serving
derives the same number via its own (separately tested) live warm-start seam.

This module is import-light; resolving the window pulls in the QL experiment CLI
once and memoizes the result.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

# ── Bundle / contract constants (the bundle under test) ──────────────────────
BUNDLE_ID = "NQ_W3_20260613T055600Z"
EXPECTED_CACHE_TAG = "7850272e"
CACHE_FILENAME = f"ml_utility_{EXPECTED_CACHE_TAG}.parquet"
TICK_SIZE = 0.25

#: Contractual feature order (strategy.json feature_set.names) — the order the
#: CatBoost model was trained on; offline scoring (P2) must use exactly this.
CONTRACT_FEATURES: tuple[str, ...] = (
    "int_time_within_2pts",
    "int_absorption_ratio",
    "app_avg_trade_size",
    "app_large_trade_vol_pct",
    "app_max_spread",
)

#: The three honest-resolution classes (class_map order).
CLASS_MAP: dict[int, str] = {
    0: "tradeable_reversal",
    1: "trap_reversal",
    2: "aggressive_blowthrough",
}

#: The exact ratified D-036 launch argv (from QL scripts/w3_cache_warmer.py).
#: Model/fold args don't affect the dataset cache tag but are passed verbatim so
#: the resolved DashboardUtilityConfig is byte-identical to the train run.
_EXP_ARGV = [
    "--preset", "all_to_ny",
    "--symbol", "NQ",
    "--bar-type", "147t",
    "--start", "2025-11-21",
    "--end", "2026-02-13",
    "--tp", "15",
    "--sl", "15",
    "--interaction-window", "5",
    "--include-approach-features",
    "--approach-window", "15",
    "--fold-scheme", "purged-days",
    "--fold-train-days", "40",
    "--fold-test-days", "5",
    "--fold-step-days", "5",
    "--fold-purge-days", "2",
    "--min-train-events", "30",
    "--pin-features",
    "int_time_within_2pts,int_absorption_ratio,app_avg_trade_size,app_large_trade_vol_pct,app_max_spread",
    "--iterations", "1000",
    "--depth", "6",
]

#: QL repo root (local validation dependency; override with W3B_QL_REPO).
QL_REPO = Path(os.environ.get("W3B_QL_REPO", r"C:\Users\gonza\Documents\Claude-Quant-Lab"))


@dataclass(frozen=True)
class W3Window:
    """Resolved W3 (D-036) cache-build provenance."""

    cache_tag: str
    util_kwargs: dict
    symbol: str
    data_dir: Path
    window_dates: tuple[str, ...]
    models_path: Path

    @property
    def symbol_dir(self) -> Path:
        return self.data_dir / self.symbol

    def cache_path(self, date_str: str) -> Path:
        return self.data_dir / self.symbol / date_str / CACHE_FILENAME

    def has_cache(self, date_str: str) -> bool:
        return self.cache_path(date_str).is_file()


def _ensure_ql_on_path() -> None:
    for p in (QL_REPO / "src", QL_REPO / "scripts"):
        sp = str(p)
        if sp not in sys.path:
            sys.path.insert(0, sp)


@lru_cache(maxsize=1)
def resolve_window() -> W3Window:
    """Resolve the D-036 window exactly as the QL serial train / warmer does.

    Reuses the experiment CLI's parser + resolver so the cache tag and day list
    are byte-identical to the run that built ``ml_utility_7850272e.parquet``.
    Memoized: the QL import + DuckDB date scan run once per process.
    """

    _ensure_ql_on_path()
    import run_dashboard_session_experiment as exp  # QL scripts/

    ns = exp._build_parser().parse_args(_EXP_ARGV)
    config = exp._resolve_config(ns)
    cache_tag = config.dataset_config_hash()
    if cache_tag != EXPECTED_CACHE_TAG:
        raise RuntimeError(
            f"resolved cache tag {cache_tag!r} != expected {EXPECTED_CACHE_TAG!r}; "
            "the QL config drifted from the W3 bundle's training inputs"
        )
    util_kwargs = config.dashboard_utility.model_dump()
    data_dir = Path(ns.data_dir)
    available = exp.get_available_dates(ns.symbol, data_dir)
    window_dates = tuple(exp._date_slice(available, ns.start, ns.end))
    return W3Window(
        cache_tag=cache_tag,
        util_kwargs=util_kwargs,
        symbol=ns.symbol,
        data_dir=data_dir,
        window_dates=window_dates,
        models_path=QL_REPO / "models",
    )


@lru_cache(maxsize=256)
def seed_for_day(date_str: str) -> tuple[float, float] | None:
    """The ``prev_full_hl`` (high, low) in points entering ``date_str``.

    Reproduces ``build_utility_dataset``'s rolling carry exactly (QL warmer's
    ``_seed_for_day``): the full (high, low) of the most-recent NON-EMPTY prior
    window day, or ``None`` for the first day. Used to seed PDH/PDL via
    ``load_prior_day_summary`` before the serving replay drives the day.
    """

    _ensure_ql_on_path()
    from alpha_lab.agents.data_infra.ml.config import DashboardUtilityConfig
    from alpha_lab.agents.data_infra.ml.dashboard_utility_builder import (
        _get_session_hl_for_date,
    )

    window = resolve_window()
    util_cfg = DashboardUtilityConfig(**window.util_kwargs)
    try:
        idx = window.window_dates.index(date_str)
    except ValueError:
        return None
    for k in range(idx - 1, -1, -1):
        hl = _get_session_hl_for_date(
            window.data_dir, window.symbol, window.window_dates[k], util_cfg, None
        )
        if hl is not None:
            return hl
    return None


def prior_trading_day(date_str: str) -> date:
    """The (calendar) prior day used as the PDH/PDL summary key, matching QL's
    ``load_prior_day_summary(td - timedelta(days=1), ...)``."""

    return date.fromisoformat(date_str) - timedelta(days=1)
