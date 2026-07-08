"""WARM-FIX P3: the live warm-start drain must not predict, register, or journal.

WARM_PERF_RECON §2 proved the pre-fix behavior: the model predicts on warm-replay
touches, journals them as mode="live" rows byte-indistinguishable from real-time
rows, and every restart re-journals the two replayed prior days with fresh ids
(measured up to 10 duplicates per touch). The gate suppresses production
atomically at _run_inference while warming; everything else (bars, levels,
touches, observations, market context) still builds, and the flip event itself
predicts normally.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

from strategy_core import load_strategy_contract

from trade_lab.domain.events import TradeEvent, TradeSide
from trade_lab.domain.levels import LevelDirection, LevelKind, TouchEvent
from trade_lab.domain.sessions import SessionName
from trade_lab.services.inference.inference_engine import Prediction
from trade_lab.services.journal import PredictionJournal
from trade_lab.services.live import LiveConfig, LiveMarketDataService
from trade_lab.services.runtime import ApplicationRuntime

_FIXTURE_STRATEGY = Path(__file__).parent / "fixtures" / "strategy.json"
#: Live-start wall clock: 2026-01-05 (Monday) 15:00Z. Warm anchor == this instant.
_NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
#: Warm-day touch: one hour before "now", i.e. replayed history.
_WARM_TOUCH_TS = datetime(2026, 1, 5, 14, 0, tzinfo=UTC)
#: Near-live touch whose observation (+300 s) expires shortly AFTER the flip.
_LIVE_TOUCH_TS = _NOW - timedelta(seconds=250)
_LEVEL_TICKS = 68_000


class _FakeEngine:
    """Minimal always-predicting engine (duck-typed, as in the resolver tests)."""

    def __init__(self, contract) -> None:
        self.active_contract = contract
        self.has_active_model = True
        self.predict_calls = 0

    def active(self):
        return SimpleNamespace(
            contract=self.active_contract,
            section=None,
            model_id="m-1",
            validation_ok=True,
            validation_detail="fake",
        )

    def predict_for_observation(self, observation, market_context) -> Prediction:
        self.predict_calls += 1
        return Prediction(
            prediction_id=f"pred-{self.predict_calls}",
            touch_id=observation.originating_touch_id,
            observation_id=observation.observation_id,
            event_ts_utc=observation.scheduled_end_ts_utc,
            predicted_class="tradeable_reversal",
            probabilities=MappingProxyType({"tradeable_reversal": 1.0}),
            feature_values=MappingProxyType({}),
            level_kind=observation.level_kind.value,
            level_price_ticks=observation.level_price_ticks,
            direction="long",
            session="ny",
            is_eligible=True,
            model_id="m-1",
            contract_id="NQ_test",
            nan_count=0,
        )


def _trade(ts: datetime, price_ticks: int = _LEVEL_TICKS + 4) -> TradeEvent:
    return TradeEvent(
        event_ts_utc=ts,
        receive_ts_utc=None,
        instrument_id=1,
        requested_symbol="NQ.c.0",
        raw_symbol=None,
        price_ticks=price_ticks,
        size=1,
        side=TradeSide.UNKNOWN,
        source_schema="trades",
    )


def _touch(touch_id: str, ts: datetime) -> TouchEvent:
    return TouchEvent(
        touch_id=touch_id,
        event_ts_utc=ts,
        trading_day=ts.date(),
        session=SessionName.NY,
        level_kind=LevelKind.NY_LOW,
        level_price_ticks=_LEVEL_TICKS,
        trade_price_ticks=_LEVEL_TICKS,
        requested_symbol="NQ.c.0",
        raw_symbol=None,
        instrument_id=1,
        direction=LevelDirection.LONG,
    )


class _ScriptedFeed:
    """Yields a warm batch after ``begin``, a live batch after ``live_release``."""

    def __init__(
        self, warm: list[TradeEvent], live: list[TradeEvent] | None = None
    ) -> None:
        self._warm = warm
        self._live = live or []
        self.begin = asyncio.Event()
        self.live_release = asyncio.Event()
        self.hold = asyncio.Event()

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        self.begin.set()
        self.live_release.set()
        self.hold.set()

    async def events(self) -> AsyncIterator[object]:
        await self.begin.wait()
        for trade in self._warm:
            yield trade
        if self._live:
            await self.live_release.wait()
            for trade in self._live:
                yield trade
        await self.hold.wait()


def _service(runtime: ApplicationRuntime, factory) -> LiveMarketDataService:
    config = LiveConfig(
        requested_symbol="NQ.c.0",
        dataset="GLBX.MDP3",
        trade_schema="trades",
        quote_schema="mbp-1",
        context_schemas=("definition",),
        api_key_configured=True,
        enabled=True,
    )
    return LiveMarketDataService(runtime, config, factory, now_provider=lambda: _NOW)


def _journal_rows(root: Path) -> list[dict]:
    rows: list[dict] = []
    if not root.exists():
        return rows
    for file in sorted(root.glob("*.jsonl")):
        rows.extend(json.loads(line) for line in file.read_text().splitlines())
    return rows


async def _wait_for(predicate, *, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached within timeout")


def test_warm_drain_is_gated_and_the_flip_event_predicts_normally(tmp_path: Path) -> None:
    asyncio.run(_run_gate_then_flip(tmp_path))


async def _run_gate_then_flip(tmp_path: Path) -> None:
    engine = _FakeEngine(load_strategy_contract(_FIXTURE_STRATEGY))
    journal_root = tmp_path / "journal"
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0",
        tick_timeframes=(147,),
        observation_duration_seconds=300,
        inference_engine=engine,
        journal=PredictionJournal(journal_root),
    )
    feed = _ScriptedFeed(
        warm=[
            # Warm phase (ts < anchor): a print inside the warm observation window,
            # then the trade that EXPIRES the warm observation -> gated, nothing.
            _trade(_WARM_TOUCH_TS + timedelta(seconds=60)),
            _trade(_WARM_TOUCH_TS + timedelta(seconds=301)),
        ],
        live=[
            # Live phase: the flip event (>= anchor) clears the gate itself...
            _trade(_NOW + timedelta(seconds=1)),
            # ...and this one expires the near-live observation -> a real prediction.
            _trade(_NOW + timedelta(seconds=60)),
        ],
    )
    live = _service(runtime, lambda _config: feed)
    await live.start()

    # Seed both observations AFTER start() (its reset rebuilds the engine state).
    runtime.observations.start_from_touch(_touch("touch-warm", _WARM_TOUCH_TS))
    runtime.observations.start_from_touch(_touch("touch-live", _LIVE_TOUCH_TS))
    feed.begin.set()

    # Warm expiry happened once two warm events processed; nothing was produced.
    await _wait_for(lambda: live.status().warm_start_events == 2)
    assert engine.predict_calls == 0
    assert runtime.predictions == ()
    assert runtime._honest_resolver is not None
    assert runtime._honest_resolver.open_count == 0
    assert _journal_rows(journal_root) == []

    feed.live_release.set()
    # The live trades then flip warm->live and expire the near-live observation:
    # exactly one prediction, produced and journaled as a normal live row.
    await _wait_for(lambda: len(runtime.predictions) == 1)
    assert live.status().warm_start_state == "live"
    assert engine.predict_calls == 1
    assert runtime.predictions[0].touch_id == "touch-live"
    await _wait_for(
        lambda: any(row["type"] == "prediction" for row in _journal_rows(journal_root))
    )
    prediction_rows = [r for r in _journal_rows(journal_root) if r["type"] == "prediction"]
    assert len(prediction_rows) == 1
    assert prediction_rows[0]["mode"] == "live"
    assert prediction_rows[0]["touch_id"] == "touch-live"
    # No warm rows sneaked in for the warm touch.
    assert all(r["touch_id"] != "touch-warm" for r in _journal_rows(journal_root))
    await live.stop()


def test_two_consecutive_warm_starts_journal_zero_rows_for_the_replayed_days(
    tmp_path: Path,
) -> None:
    """The restart-duplicate kill, end-to-end: pre-fix each warm start re-journaled
    the replayed days' touches as fresh mode="live" rows (WARM_PERF_RECON §2, up to
    10 duplicates per touch); with the gate BOTH warm passes journal nothing."""

    asyncio.run(_run_double_warm_start(tmp_path))


async def _run_double_warm_start(tmp_path: Path) -> None:
    engine = _FakeEngine(load_strategy_contract(_FIXTURE_STRATEGY))
    journal_root = tmp_path / "journal"
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0",
        tick_timeframes=(147,),
        observation_duration_seconds=300,
        inference_engine=engine,
        journal=PredictionJournal(journal_root),
    )
    def warm_fixture() -> _ScriptedFeed:
        return _ScriptedFeed(
            warm=[
                _trade(_WARM_TOUCH_TS + timedelta(seconds=60)),
                _trade(_WARM_TOUCH_TS + timedelta(seconds=301)),
            ]
        )

    feeds = [warm_fixture(), warm_fixture()]
    calls = 0

    def factory(_config: LiveConfig):
        nonlocal calls
        calls += 1
        return feeds[calls - 1]

    live = _service(runtime, factory)
    for cycle in range(2):
        await live.start()
        # The same warm fixture day replays on every restart (fresh touch ids each
        # time — exactly the duplicate mechanism the gate kills).
        runtime.observations.start_from_touch(
            _touch(f"touch-warm-cycle-{cycle}", _WARM_TOUCH_TS)
        )
        feeds[cycle].begin.set()
        await _wait_for(lambda: live.status().warm_start_events == 2)
        await live.stop()
        assert engine.predict_calls == 0
        assert _journal_rows(journal_root) == [], f"cycle {cycle} journaled warm rows"

    assert calls == 2
    assert _journal_rows(journal_root) == []
