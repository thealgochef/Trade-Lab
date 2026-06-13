"""W3b P0 — headless replay of one trading day through the real serving stack.

Drives the SAME path POST /api/v1/replay/start drives — ``ApplicationRuntime``
+ activated bundle + ``StreamingHonestResolver`` + ``PredictionJournal`` via
``HistoricalReplayService`` over the canonical ``DatabentoParquetSource`` day
stream — but with no FastAPI/websocket server. The journal it writes is the
serving-side evidence the parity harness (P1/P2) joins against the QL cache.

Composition mirrors ``trade_lab.api.app.create_app`` exactly (same runtime,
ModelRegistry+ServingCapabilities, InferenceEngine, activation glue). Two
deliberate, documented differences from a bare replay:

  1. Bundle ACTIVATED before the drive (an operator action the API exposes via
     POST /api/v1/models/{id}/activate). ``set_inference_engine`` after activate
     rebuilds the contract-driven honest resolver + buffer retention.
  2. Prior-day PDH/PDL SEEDED via ``load_prior_day_summary`` (the live warm-start
     path), injected inside the source generator so it lands AFTER the runtime
     reset that ``HistoricalReplayService.start`` performs and BEFORE the first
     event — the seed value is the QL ``prev_full_hl`` for this day (see
     ``window.seed_for_day``).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from trade_lab.adapters.historical_parquet import HistoricalParquetAdapter
from trade_lab.adapters.replay_catalog import SUPPORTED_SCHEMAS
from trade_lab.config import Settings
from trade_lab.ports.market_data import HistoricalMarketDataSource
from trade_lab.services.inference.features import DEFAULT_FEATURE_REGISTRY
from trade_lab.services.inference.inference_engine import InferenceEngine
from trade_lab.services.journal import PredictionJournal
from trade_lab.services.model_registry import ModelRegistry, ServingCapabilities
from trade_lab.services.replay import HistoricalReplayService, ReplayConfig, ReplayState
from trade_lab.services.runtime import ApplicationRuntime
from w3b.window import (
    BUNDLE_ID,
    TICK_SIZE,
    W3Window,
    prior_trading_day,
    resolve_window,
    seed_for_day,
)

logger = logging.getLogger(__name__)

# The day files are ``mbp10.parquet``; for_trading_day auto-detects the file, so
# this is only the feed-status label (the activation gate checks the contract's
# data_requirements, not this).
_REPLAY_SCHEMA = "mbp-10"
#: requested_symbol the QL cache build used (for_trading_day(requested_symbol="NQ"),
#: front_month_only=True). Must match or the trade set — and thus touches — differ.
_REQUESTED_SYMBOL = "NQ"

_TERMINAL_STATES = {
    ReplayState.COMPLETED,
    ReplayState.FAILED,
    ReplayState.STOPPED,
    ReplayState.CANCELLED,
}


@dataclass(frozen=True)
class ReplayResult:
    trading_day: str
    journal_path: Path
    seeded_pdh_pdl: tuple[float, float] | None
    predictions: int
    outcomes: int
    dropped: int
    events_processed: int
    state: str


class _SeedingSource(HistoricalMarketDataSource):
    """Wrap the day source so the PDH/PDL seed fires at the right instant.

    ``HistoricalReplayService.start`` resets the runtime (rebuilding the
    Strategy-Core service) and THEN pulls events from this source inside the
    replay task. Seeding in ``scan`` — the first thing the task does before the
    first event — lands the prior-day summary on the post-reset service, exactly
    where the live warm-start would seed it ahead of the intraday stream.
    """

    def __init__(self, inner: HistoricalMarketDataSource, seed_fn) -> None:
        self._inner = inner
        self._seed_fn = seed_fn

    def scan(self, paths: Iterable[Path], **kwargs) -> Iterator:
        self._seed_fn()
        yield from self._inner.scan(paths, **kwargs)


def build_serving_stack(
    *, models_path: Path, journal_dir: Path, retention_minutes: int | None = None
) -> tuple[ApplicationRuntime, ModelRegistry, InferenceEngine]:
    """Compose runtime + registry + engine exactly as ``create_app`` does."""

    overrides: dict = {"models_path": models_path, "journal_path": journal_dir}
    if retention_minutes is not None:
        overrides["market_context_retention_minutes"] = retention_minutes
    settings = Settings(**overrides)

    runtime = ApplicationRuntime(
        requested_symbol=settings.front_month_symbol,
        tick_timeframes=settings.tick_timeframes,
        observation_duration_seconds=settings.observation_duration_seconds,
        seed_bar_limit_per_timeframe=settings.seed_max_bars_per_timeframe,
        market_context_retention_minutes=settings.market_context_retention_minutes,
        journal=PredictionJournal(settings.journal_path),
    )
    registry = ModelRegistry(
        settings.models_path,
        serving_strategy_id=runtime.strategy_core_service.plugin_strategy_id,
        serving_capabilities=ServingCapabilities(
            computable_features=DEFAULT_FEATURE_REGISTRY.names,
            market_context_retention_minutes=settings.market_context_retention_minutes,
            instrument_root=settings.instrument_root,
            observation_duration_seconds=settings.observation_duration_seconds,
            decision_timeframe_ticks=min(settings.tick_timeframes),
            supported_live_schemas=frozenset(
                {settings.databento_trade_schema, settings.databento_quote_schema}
            ),
            supported_replay_schemas=frozenset(SUPPORTED_SCHEMAS),
        ),
    )
    engine = InferenceEngine(registry)
    runtime.set_inference_engine(engine)
    return runtime, registry, engine


async def _drive(
    replay: HistoricalReplayService, source: HistoricalMarketDataSource, config: ReplayConfig
) -> ReplayState:
    await replay.start(source, config)
    while replay.status().state not in _TERMINAL_STATES:
        await asyncio.sleep(0)
    return replay.status().state


def replay_day_to_journal(
    date_str: str,
    *,
    journal_dir: Path,
    window: W3Window | None = None,
    bundle_id: str = BUNDLE_ID,
    clear: bool = True,
) -> ReplayResult:
    """Replay one trading day with ``bundle_id`` active; write the day's journal.

    ``journal_dir`` is this day's scratch journal root (cleared per run so stale
    append-only records cannot poison the parity join). The produced JSONL lives
    at ``journal_dir / f"{date_str}.jsonl"`` (one trading-day file, 18:00 ET roll).
    """

    window = window or resolve_window()
    journal_dir = Path(journal_dir)
    if clear and journal_dir.exists():
        shutil.rmtree(journal_dir)
    journal_dir.mkdir(parents=True, exist_ok=True)

    runtime, registry, engine = build_serving_stack(
        models_path=window.models_path, journal_dir=journal_dir
    )
    # Operator activation: validates against the 8-check serving gate + loads the
    # .cbm, then rebinds the engine so the runtime builds the contract-driven
    # honest resolver and buffer retention.
    registry.activate(bundle_id)
    runtime.set_inference_engine(engine)

    seed = seed_for_day(date_str)

    def _seed_fn() -> None:
        if seed is None:
            return
        high_pts, low_pts = seed
        runtime.strategy_core_service.load_prior_day_summary(
            prior_trading_day(date_str),
            high_ticks=round(high_pts / TICK_SIZE),
            low_ticks=round(low_pts / TICK_SIZE),
        )

    source = _SeedingSource(HistoricalParquetAdapter(front_month_only=True), _seed_fn)
    replay = HistoricalReplayService(runtime)
    config = ReplayConfig(
        paths=(window.symbol_dir,),
        requested_symbol=_REQUESTED_SYMBOL,
        schema=_REPLAY_SCHEMA,
        trading_day=date.fromisoformat(date_str),
        symbol_dir=window.symbol_dir,
        speed=0.0,
    )
    state = asyncio.run(_drive(replay, source, config))
    status = replay.status()
    snap = runtime.snapshot()
    return ReplayResult(
        trading_day=date_str,
        journal_path=journal_dir / f"{date_str}.jsonl",
        seeded_pdh_pdl=seed,
        predictions=len(snap.predictions),
        outcomes=len(snap.outcomes),
        dropped=len(snap.dropped),
        events_processed=status.events_processed,
        state=state.value,
    )


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", help="trading day YYYY-MM-DD (must be in the D-036 window)")
    parser.add_argument(
        "--journal-dir",
        type=Path,
        default=None,
        help="scratch journal root (default: backend/data/w3b_journal/<date>)",
    )
    parser.add_argument("--bundle", default=BUNDLE_ID)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    window = resolve_window()
    if args.date not in window.window_dates:
        raise SystemExit(
            f"{args.date} is not in the D-036 window ({len(window.window_dates)} days)"
        )
    journal_dir = args.journal_dir or (
        Path(__file__).resolve().parents[2] / "data" / "w3b_journal" / args.date
    )
    result = replay_day_to_journal(
        args.date, journal_dir=journal_dir, window=window, bundle_id=args.bundle
    )
    has_cache = window.has_cache(args.date)
    print(f"day={result.trading_day} state={result.state} events={result.events_processed:,}")
    print(f"  seed_pdh_pdl={result.seeded_pdh_pdl}")
    print(f"  predictions={result.predictions} outcomes={result.outcomes} dropped={result.dropped}")
    print(f"  cache_present={has_cache} journal={result.journal_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
