"""Runtime composition for live-compatible market events.

The application runtime delegates bars, sessions, levels, and touches to
Strategy-Core so replay and live feeds cannot accidentally diverge. Adapters may
know where bytes came from, but only canonical events are allowed into this hot
path and DTO mapping remains at the API edge.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from types import MappingProxyType
from typing import Any

from strategy_core import StreamDrop, StreamingHonestResolver, StreamResolution

from trade_lab.domain.candles import Candle
from trade_lab.domain.data_quality import DataQualityWarning
from trade_lab.domain.events import (
    DailyStatisticEvent,
    InstrumentDefinitionEvent,
    MarketEvent,
    MarketStatusEvent,
    TopOfBookEvent,
    TradeEvent,
)
from trade_lab.domain.feed import FeedConnectionState, FeedStatus
from trade_lab.domain.levels import DisplayLevel, TouchEvent
from trade_lab.domain.market_context import DEFAULT_RETENTION_MINUTES, MarketContextBuffer
from trade_lab.domain.observations import Observation, ObservationEngine, ObservationStatus
from trade_lab.domain.outcomes import Outcome
from trade_lab.services.inference.inference_engine import InferenceEngine, Prediction
from trade_lab.services.inference.outcome_tracker import OutcomeTracker, _parse_bar_type
from trade_lab.services.strategy_core_service import StrategyCoreService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RuntimeUpdate:
    feed_status: FeedStatus | None = None
    warnings: tuple[DataQualityWarning, ...] = ()
    current_bars: tuple[Candle, ...] = ()
    closed_bars: tuple[Candle, ...] = ()
    display_levels: tuple[DisplayLevel, ...] = ()
    touches: tuple[TouchEvent, ...] = ()
    observations: tuple[Observation, ...] = ()
    predictions: tuple[Prediction, ...] = ()
    outcomes: tuple[Outcome, ...] = ()

    def has_deltas(self) -> bool:
        return any(
            (
                self.feed_status is not None,
                self.warnings,
                self.current_bars,
                self.closed_bars,
                self.display_levels,
                self.touches,
                self.observations,
                self.predictions,
                self.outcomes,
            )
        )


@dataclass(frozen=True, slots=True)
class ModelStatus:
    """Path-free, secret-free view of the active inference model for the API edge.

    Built from the active model's strategy contract so the UI can show which model
    is serving predictions without ever learning a filesystem path. ``loaded`` is
    ``False`` (with all detail fields ``None``/empty) when no model is active.
    """

    loaded: bool
    model_id: str | None = None
    strategy_id: str | None = None
    training_mode: str | None = None
    instrument: str | None = None
    feature_names: tuple[str, ...] = ()
    class_map: MappingProxyType[int, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    validation_ok: bool = False
    validation_detail: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    current_bars: tuple[Candle, ...]
    recent_closed_bars: tuple[Candle, ...]
    display_levels: tuple[DisplayLevel, ...]
    active_observations: tuple[Observation, ...]
    feed_status: FeedStatus
    warnings: tuple[DataQualityWarning, ...]
    predictions: tuple[Prediction, ...] = ()
    outcomes: tuple[Outcome, ...] = ()
    model_status: ModelStatus = field(default_factory=lambda: ModelStatus(loaded=False))
    session: str | None = None
    trading_day: date | None = None
    metadata: MappingProxyType[str, Any] = field(default_factory=lambda: MappingProxyType({}))


class ApplicationRuntime:
    """Compose the Strategy-Core adapter and Trade-Lab DTO/inference state."""

    def __init__(
        self,
        *,
        requested_symbol: str | None,
        tick_timeframes: tuple[int, ...],
        observation_duration_seconds: int,
        warning_limit: int = 100,
        recent_closed_bar_limit: int = 500,
        seed_bar_limit_per_timeframe: int = 2500,
        market_context_retention_minutes: int = DEFAULT_RETENTION_MINUTES,
        prediction_limit: int = 500,
        outcome_limit: int = 500,
        inference_engine: InferenceEngine | None = None,
    ) -> None:
        if warning_limit <= 0:
            raise ValueError("warning_limit must be positive")
        if recent_closed_bar_limit <= 0:
            raise ValueError("recent_closed_bar_limit must be positive")
        if seed_bar_limit_per_timeframe <= 0:
            raise ValueError("seed_bar_limit_per_timeframe must be positive")
        if market_context_retention_minutes <= 0:
            raise ValueError("market_context_retention_minutes must be positive")
        if prediction_limit <= 0:
            raise ValueError("prediction_limit must be positive")
        if outcome_limit <= 0:
            raise ValueError("outcome_limit must be positive")
        self.requested_symbol = requested_symbol
        self.strategy_core_service = StrategyCoreService(
            requested_symbol=requested_symbol,
            tick_timeframes=tick_timeframes,
            recent_closed_bar_limit=recent_closed_bar_limit,
            warning_limit=warning_limit,
        )
        # Compatibility handles for tests/callers that load prior-day summaries through
        # the runtime. New runtime semantics live in Strategy-Core.
        self.levels = self.strategy_core_service
        self.observations = ObservationEngine(timedelta(seconds=observation_duration_seconds))
        # Rolling L1/L0 context for pre-touch order-flow features. Structurally cannot
        # hold depth, so inference downstream can never read more than trades + BBO.
        self.market_context = MarketContextBuffer(
            retention=timedelta(minutes=market_context_retention_minutes)
        )
        self._market_context_retention_minutes = market_context_retention_minutes
        # Optional inference seam: when an InferenceEngine with an active model is set,
        # completed observations produce Predictions attached to the RuntimeUpdate.
        self._inference_engine = inference_engine
        self._prediction_limit = prediction_limit
        self._outcome_limit = outcome_limit
        self._predictions: list[Prediction] = []
        self._outcomes: list[Outcome] = []
        # MAE-first forward-outcome tracker, contract-specific. Built from the active
        # model's contract so a hot-swap re-derives forward bar type + thresholds.
        self._outcome_tracker: OutcomeTracker | None = self._build_outcome_tracker(
            inference_engine
        )
        self._warning_limit = warning_limit
        self._recent_closed_bar_limit = recent_closed_bar_limit
        self._seed_bar_limit_per_timeframe = seed_bar_limit_per_timeframe
        self._tick_timeframes = tick_timeframes
        self._observation_duration_seconds = observation_duration_seconds
        # D1a DARK seat: the SC streaming honest resolver runs alongside the tracker at
        # the same lifecycle points, accumulating into a parallel dark ring (same cap as
        # outcomes) consumed ONLY by the gate-B characterization harness — no
        # RuntimeUpdate/snapshot/DTO/WS surface reads it.
        self._dark_outcomes: list[StreamResolution | StreamDrop] = []
        self._honest_resolver: StreamingHonestResolver | None = self._build_honest_resolver(
            inference_engine
        )
        self._warnings: list[DataQualityWarning] = []
        self._recent_closed_bars: list[Candle] = []
        # Historical warm-up bars kept separate from the rolling live buffer so live
        # deltas never evict the initial "last N sessions" context and so the snapshot
        # can serve history to clients that connect after seeding completes.
        self._seed_closed_bars: list[Candle] = []
        self._metadata: dict[str, Any] = {}
        self._feed_status = FeedStatus(
            state=FeedConnectionState.DISCONNECTED,
            mode="idle",
            requested_symbol=requested_symbol,
            last_message="Market-data feed is not started.",
        )

    def reset(
        self,
        *,
        requested_symbol: str | None = None,
        preserve_warnings: bool = False,
        feed_message: str = "Runtime reset for replay.",
    ) -> RuntimeUpdate:
        """Reset all derived runtime state before a new replay session.

        Warnings are cleared by default so each replay is deterministic and does not
        carry data-quality state from a prior source. Callers may explicitly preserve
        warnings for a future operator-audit workflow.
        """

        if requested_symbol is not None:
            self.requested_symbol = requested_symbol
        self.strategy_core_service = StrategyCoreService(
            requested_symbol=self.requested_symbol,
            tick_timeframes=self._tick_timeframes,
            recent_closed_bar_limit=self._recent_closed_bar_limit,
            warning_limit=self._warning_limit,
        )
        self.levels = self.strategy_core_service
        self.observations = ObservationEngine(
            timedelta(seconds=self._observation_duration_seconds)
        )
        self.market_context.reset()
        self._predictions.clear()
        self._outcomes.clear()
        if self._outcome_tracker is not None:
            self._outcome_tracker.reset()
        if self._honest_resolver is not None:
            self._honest_resolver.reset()
        self._dark_outcomes.clear()
        if not preserve_warnings:
            self._warnings.clear()
        self._recent_closed_bars.clear()
        self._seed_closed_bars.clear()
        self._metadata.clear()
        self._feed_status = FeedStatus(
            state=FeedConnectionState.DISCONNECTED,
            mode="idle",
            requested_symbol=self.requested_symbol,
            last_message=feed_message,
        )
        return RuntimeUpdate(feed_status=self._feed_status)

    @property
    def feed_status(self) -> FeedStatus:
        return self._feed_status

    def set_feed_status(self, status: FeedStatus) -> RuntimeUpdate:
        self._feed_status = status
        return RuntimeUpdate(feed_status=status)

    def set_inference_engine(self, engine: InferenceEngine | None) -> None:
        """Attach (or detach) the inference engine and clear prediction state.

        Called on model hot-swap: an activation switches the engine's active model,
        so prior predictions belong to a different contract and must be dropped to
        avoid mixing bundles in one session. Market-data state is untouched.
        """

        self._inference_engine = engine
        self._predictions.clear()
        self._outcomes.clear()
        self._dark_outcomes.clear()
        self._outcome_tracker = self._build_outcome_tracker(engine)
        self._honest_resolver = self._build_honest_resolver(engine)

    @staticmethod
    def _build_outcome_tracker(engine: InferenceEngine | None) -> OutcomeTracker | None:
        """Construct a contract-specific OutcomeTracker for the active model, if any.

        Returns ``None`` when no engine/model is active so the runtime tracks no
        outcomes until a model is loaded.
        """

        if engine is None:
            return None
        contract = engine.active_contract
        if contract is None:
            return None
        return OutcomeTracker(contract)

    def _build_honest_resolver(
        self, engine: InferenceEngine | None
    ) -> StreamingHonestResolver | None:
        """Construct the contract-specific SC streaming honest resolver (D1a, DARK).

        Mirrors ``_build_outcome_tracker``'s lifecycle exactly (same activation /
        hot-swap / reset points, same active contract). Entry prints come from the
        Strategy-Core runtime's trade ring via a late-binding closure so the seam
        survives ``reset()`` swapping the service. Construction FAILS LOUD when the
        contract's forward bar type is not among the runtime's configured tick
        timeframes — the recon's silent-never-resolve hole — so a bad activation is
        rejected instead of silently tracking nothing. Returns ``None`` when no
        engine/model is active.
        """

        if engine is None:
            return None
        contract = engine.active_contract
        if contract is None:
            return None
        policy = contract.label_policy
        if policy.decision_offset_minutes * 60 != self._observation_duration_seconds:
            logger.warning(
                "contract label_policy.decision_offset_minutes (%s min) != configured "
                "observation_duration_seconds (%s s): the dark resolver anchors decisions "
                "on the contract offset while predictions register on the config window",
                policy.decision_offset_minutes,
                self._observation_duration_seconds,
            )
        return StreamingHonestResolver(
            forward_timeframe_ticks=_parse_bar_type(policy.forward_bar_type),
            tick_size=contract.tick_size,
            tp_points=policy.tp_points,
            sl_points=policy.sl_points,
            trap_mfe_min=policy.trap_mfe_min,
            decision_offset_minutes=policy.decision_offset_minutes,
            trade_price_at=lambda ts_utc: self.strategy_core_service.trade_price_at(ts_utc),
            available_timeframes=self._tick_timeframes,
        )

    @property
    def dark_outcomes(self) -> tuple[StreamResolution | StreamDrop, ...]:
        """The D1a dark ring (gate-B characterization harness only; no DTO/WS surface)."""

        return tuple(self._dark_outcomes)

    def _append_dark(self, emitted: tuple[StreamResolution | StreamDrop, ...]) -> None:
        if not emitted:
            return
        self._dark_outcomes.extend(emitted)
        if len(self._dark_outcomes) > self._outcome_limit:
            del self._dark_outcomes[: len(self._dark_outcomes) - self._outcome_limit]

    def _register_dark(
        self,
        resolver: StreamingHonestResolver,
        prediction: Prediction,
        observation: Observation,
    ) -> None:
        """Register one prediction with the dark resolver off its TOUCH anchors.

        The decision anchor is the touch's bar-close instant + trading day carried on
        the observation/touch chain (``Observation.start_ts_utc`` = the SC touch's
        ``bar_ts_utc``) — NEVER the prediction's ``event_ts_utc`` (= touch + the
        observation window). Fails loud if the chain did not carry them.
        """

        if observation.start_ts_utc is None or observation.trading_day is None:
            raise ValueError(
                "dark resolver registration requires the touch bar ts + trading_day "
                "carried on the observation chain; refusing to approximate with the "
                "prediction event_ts"
            )
        try:
            drop = resolver.register(
                prediction.prediction_id,
                touch_bar_ts_utc=observation.start_ts_utc,
                trading_day=observation.trading_day,
                direction=prediction.direction,
            )
        except Exception:
            # The dark seat must never break the market-data hot path.
            logger.warning("dark honest-resolver registration failed", exc_info=False)
            return
        if drop is not None:
            self._append_dark((drop,))

    def clear_predictions(self) -> None:
        """Drop accumulated predictions + outcomes (e.g. on model hot-swap)."""

        self._predictions.clear()
        self._outcomes.clear()
        if self._outcome_tracker is not None:
            self._outcome_tracker.reset()
        if self._honest_resolver is not None:
            self._honest_resolver.reset()
        self._dark_outcomes.clear()

    @property
    def predictions(self) -> tuple[Prediction, ...]:
        return tuple(self._predictions)

    @property
    def outcomes(self) -> tuple[Outcome, ...]:
        return tuple(self._outcomes)

    def model_status(self) -> ModelStatus:
        """Path-free status of the active inference model for the API edge.

        Returns an unloaded status when no engine/model is active, so the snapshot
        and ``model.status`` message always have a stable, serializable shape.
        """

        engine = self._inference_engine
        active = engine.active() if engine is not None else None
        if active is None:
            return ModelStatus(loaded=False)
        contract = active.contract
        return ModelStatus(
            loaded=True,
            model_id=active.model_id,
            strategy_id=contract.strategy_id,
            training_mode=contract.training_mode,
            instrument=contract.instrument,
            feature_names=tuple(contract.feature_set.names),
            class_map=MappingProxyType(dict(contract.class_map.mapping)),
            validation_ok=True,
            validation_detail="active model validated against its contract",
        )

    def session_state(self) -> tuple[str | None, date | None]:
        """Derive the current session label + trading day from the latest event.

        Uses the wall-clock session classifier against the most recent event
        timestamp so the frontend placeholders reflect the live/replay clock. When
        no event has been processed yet (no timestamp), both are ``None`` rather
        than a fabricated value.
        """

        snapshot = self.strategy_core_service.snapshot()
        return snapshot.session, snapshot.trading_day

    def _run_inference(
        self, changed_observations: tuple[Observation, ...]
    ) -> tuple[Prediction, ...]:
        """Produce predictions for observations that just completed.

        An observation "completes" when the engine expires it at its scheduled end
        (its full interaction window has elapsed). With no active model this yields
        nothing and the runtime keeps serving market data unchanged.
        """

        engine = self._inference_engine
        if engine is None or not engine.has_active_model:
            return ()
        produced: list[Prediction] = []
        # (prediction, observation) pairs: the dark resolver registers off the TOUCH
        # anchors carried on the observation chain, not off the prediction timestamps.
        produced_pairs: list[tuple[Prediction, Observation]] = []
        for observation in changed_observations:
            if observation.status is not ObservationStatus.EXPIRED:
                continue
            try:
                prediction = engine.predict_for_observation(observation, self.market_context)
            except Exception:
                # Inference must never break the market-data hot path.
                logger.warning("inference failed for a completed observation", exc_info=False)
                continue
            if prediction is not None:
                produced.append(prediction)
                produced_pairs.append((prediction, observation))
        if produced:
            self._predictions.extend(produced)
            if len(self._predictions) > self._prediction_limit:
                del self._predictions[: len(self._predictions) - self._prediction_limit]
            tracker = self._outcome_tracker
            if tracker is not None:
                for prediction in produced:
                    tracker.register(prediction)
            resolver = self._honest_resolver
            if resolver is not None:
                # D1a dark seat: mirror every tracker registration one-for-one.
                for prediction, observation in produced_pairs:
                    self._register_dark(resolver, prediction, observation)
        return tuple(produced)

    def _track_outcomes(self, closed_bars: tuple[Candle, ...]) -> tuple[Outcome, ...]:
        """Advance open outcome trackers on each just-closed contract bar.

        Only forward-bar-type closes resolve predictions; non-matching timeframes are
        ignored inside the tracker. Outcome tracking must never break the market-data
        hot path, so failures are swallowed.
        """

        if not closed_bars:
            return ()
        resolver = self._honest_resolver
        if resolver is not None:
            # D1a dark seat: advance the SC streaming honest resolver from the same
            # closed-bars hook the tracker consumes; emissions land in the dark ring only.
            for bar in closed_bars:
                try:
                    self._append_dark(resolver.on_bar(bar))
                except Exception:
                    logger.warning(
                        "dark honest-resolver advance failed for a closed bar", exc_info=False
                    )
        tracker = self._outcome_tracker
        if tracker is None:
            return ()
        resolved: list[Outcome] = []
        for bar in closed_bars:
            try:
                resolved.extend(tracker.on_bar_close(bar))
            except Exception:
                logger.warning("outcome tracking failed for a closed bar", exc_info=False)
        if resolved:
            self._outcomes.extend(resolved)
            if len(self._outcomes) > self._outcome_limit:
                del self._outcomes[: len(self._outcomes) - self._outcome_limit]
        return tuple(resolved)

    def record_warning(self, warning: DataQualityWarning) -> RuntimeUpdate:
        warning = _safe_warning(warning)
        self._warnings.append(warning)
        if len(self._warnings) > self._warning_limit:
            del self._warnings[: len(self._warnings) - self._warning_limit]
        if self._feed_status.state != FeedConnectionState.DEGRADED:
            self._feed_status = FeedStatus(
                state=FeedConnectionState.DEGRADED,
                mode=self._feed_status.mode,
                requested_symbol=self._feed_status.requested_symbol,
                raw_symbol=self._feed_status.raw_symbol,
                dataset=self._feed_status.dataset,
                schema=self._feed_status.schema,
                last_event_ts_utc=warning.event_ts_utc or self._feed_status.last_event_ts_utc,
                last_message=warning.message,
                metadata=dict(self._feed_status.metadata),
            )
        return RuntimeUpdate(feed_status=self._feed_status, warnings=(warning,))

    def process_market_event(self, event: MarketEvent) -> RuntimeUpdate:
        """Process one canonical event; only trades advance bars/touches.

        Top-of-book, definitions, status, and daily statistics update contextual
        state only. This prevents quote traffic or historical-only records from
        incrementing candles or creating touches in replay.
        """

        if isinstance(event, TradeEvent):
            return self._process_trade(event)
        if isinstance(event, TopOfBookEvent):
            return self._process_quote(event)
        if isinstance(event, InstrumentDefinitionEvent):
            self._metadata["instrument"] = {
                "instrument_id": event.instrument_id,
                "requested_symbol": event.requested_symbol,
                "raw_symbol": event.raw_symbol,
                "tick_size": str(event.tick_size),
            }
            self._feed_status = FeedStatus(
                state=self._feed_status.state,
                mode=self._feed_status.mode,
                requested_symbol=event.requested_symbol,
                raw_symbol=event.raw_symbol,
                dataset=self._feed_status.dataset,
                schema=self._feed_status.schema,
                last_event_ts_utc=event.event_ts_utc,
                last_message="instrument definition received",
                metadata=dict(self._feed_status.metadata),
            )
            return RuntimeUpdate()
        if isinstance(event, MarketStatusEvent):
            self._metadata["market_status"] = event.status.value
            self._feed_status = FeedStatus(
                state=self._feed_status.state,
                mode=self._feed_status.mode,
                requested_symbol=self._feed_status.requested_symbol,
                raw_symbol=self._feed_status.raw_symbol,
                dataset=self._feed_status.dataset,
                schema=self._feed_status.schema,
                last_event_ts_utc=event.event_ts_utc,
                last_message=event.reason or f"market status: {event.status.value}",
                metadata={**dict(self._feed_status.metadata), "market_status": event.status.value},
            )
            return RuntimeUpdate()
        if isinstance(event, DailyStatisticEvent):
            self._metadata.setdefault("daily_statistics", {})[event.statistic_type] = {
                "price_ticks": event.price_ticks,
                "value": event.value,
            }
            return self._update_feed_context(event.event_ts_utc, schema=event.source_schema)
        raise TypeError(f"unsupported market event type: {type(event).__name__}")

    def seed_closed_bars(self, bars: tuple[Candle, ...]) -> RuntimeUpdate:
        """Inject historical warm-up bars into the snapshot and broadcast them.

        Bars are stored apart from the rolling live buffer and bounded per timeframe so
        the chart shows the last N sessions immediately without live deltas evicting
        them. Returns a delta the caller can fan out as ``market.bar.closed``.
        """

        if not bars:
            return RuntimeUpdate()
        self._seed_closed_bars.extend(bars)
        self._trim_seed_bars()
        return RuntimeUpdate(closed_bars=tuple(bars))

    def _trim_seed_bars(self) -> None:
        limit = self._seed_bar_limit_per_timeframe
        kept_by_timeframe: dict[int, int] = {}
        trimmed: list[Candle] = []
        # Walk newest-first so the most recent bars per timeframe are retained.
        for bar in reversed(self._seed_closed_bars):
            kept = kept_by_timeframe.get(bar.timeframe_ticks, 0)
            if kept >= limit:
                continue
            kept_by_timeframe[bar.timeframe_ticks] = kept + 1
            trimmed.append(bar)
        trimmed.reverse()
        self._seed_closed_bars = trimmed

    def snapshot(self) -> RuntimeSnapshot:
        core_snapshot = self.strategy_core_service.snapshot()
        session, trading_day = core_snapshot.session, core_snapshot.trading_day
        return RuntimeSnapshot(
            current_bars=core_snapshot.current_bars,
            recent_closed_bars=tuple((*self._seed_closed_bars, *core_snapshot.recent_closed_bars)),
            display_levels=core_snapshot.display_levels,
            active_observations=self.observations.active(),
            feed_status=self._feed_status,
            warnings=tuple(self._warnings),
            predictions=tuple(self._predictions),
            outcomes=tuple(self._outcomes),
            model_status=self.model_status(),
            session=session,
            trading_day=trading_day,
            metadata=MappingProxyType(dict(self._metadata)),
        )

    def _process_quote(self, quote: TopOfBookEvent) -> RuntimeUpdate:
        """Retain best bid/ask for context features, then update feed status only.

        Quotes never advance bars/touches/observations; the feed-status side effect is
        identical to the prior behaviour so existing outputs are unchanged.
        """

        self.market_context.append_quote(
            quote.event_ts_utc, quote.bid_price_ticks, quote.ask_price_ticks
        )
        was_disconnected = self._feed_status.state == FeedConnectionState.DISCONNECTED
        core_update = self.strategy_core_service.process_market_event(quote)
        if core_update.feed_status is not None:
            mapped = self._feed_status_for_mode(core_update.feed_status)
            next_state = (
                FeedConnectionState.CONNECTED if was_disconnected else self._feed_status.state
            )
            self._feed_status = FeedStatus(
                state=next_state,
                mode=mapped.mode,
                requested_symbol=mapped.requested_symbol,
                raw_symbol=mapped.raw_symbol,
                dataset=mapped.dataset,
                schema=mapped.schema,
                last_event_ts_utc=mapped.last_event_ts_utc,
                last_message=mapped.last_message,
                metadata=dict(mapped.metadata),
            )
            if was_disconnected:
                return RuntimeUpdate(feed_status=self._feed_status)
            return RuntimeUpdate()
        # audit #9: one-sided quotes (_quote_to_core returns None, so the core adapter
        # emits no feed_status) still arrived from the feed. Preserve the pre-migration
        # behaviour for ANY quote: advance feed liveness (last_event_ts) and promote a
        # DISCONNECTED feed to CONNECTED, without advancing bars/touches. Reuses
        # _update_feed_context, which preserves replaying/connected state and only emits
        # a delta on the disconnected->connected promotion.
        return self._update_feed_context(quote.event_ts_utc, schema=quote.source_schema)

    def _process_trade(self, trade: TradeEvent) -> RuntimeUpdate:
        self.market_context.append_trade(
            trade.event_ts_utc, trade.price_ticks, trade.size, trade.side
        )
        core_update = self.strategy_core_service.process_market_event(trade)
        changed_observations = list(self.observations.refresh(trade.event_ts_utc))
        # Run inference before appending the just-started observations: only the
        # observations completed by this trade are eligible for a prediction now.
        predictions = self._run_inference(tuple(changed_observations))
        # Resolve any open predictions against bars that just closed on this trade.
        outcomes = self._track_outcomes(core_update.closed_bars)
        for touch in core_update.touches:
            changed_observations.append(self.observations.start_from_touch(touch))
        self._feed_status = self._feed_status_for_mode(core_update.feed_status, trade=trade)
        return RuntimeUpdate(
            feed_status=self._feed_status,
            current_bars=core_update.current_bars,
            closed_bars=core_update.closed_bars,
            display_levels=core_update.display_levels,
            touches=core_update.touches,
            observations=tuple(changed_observations),
            predictions=predictions,
            outcomes=outcomes,
        )

    def _feed_status_for_mode(
        self, status: FeedStatus | None, *, trade: TradeEvent | None = None
    ) -> FeedStatus:
        base = status or self._feed_status
        mode = self._feed_status.mode if self._feed_status.mode != "idle" else base.mode
        state = FeedConnectionState.CONNECTED if mode == "live" else FeedConnectionState.REPLAYING
        schema = (
            (None if trade is None else trade.source_schema)
            or base.schema
            or self._feed_status.schema
        )
        return FeedStatus(
            state=state,
            mode=mode if mode != "idle" else "runtime",
            requested_symbol=base.requested_symbol or self.requested_symbol,
            raw_symbol=self._feed_status.raw_symbol if trade is None else trade.raw_symbol,
            dataset=self._feed_status.dataset,
            schema=schema,
            last_event_ts_utc=base.last_event_ts_utc,
            last_message=base.last_message,
            metadata={**dict(self._feed_status.metadata), **dict(base.metadata)},
        )

    def _update_feed_context(self, event_ts_utc, *, schema: str | None) -> RuntimeUpdate:
        was_disconnected = self._feed_status.state == FeedConnectionState.DISCONNECTED
        self._feed_status = FeedStatus(
            state=self._feed_status.state
            if self._feed_status.state != FeedConnectionState.DISCONNECTED
            else FeedConnectionState.CONNECTED,
            mode=self._feed_status.mode if self._feed_status.mode != "idle" else "runtime",
            requested_symbol=self._feed_status.requested_symbol,
            raw_symbol=self._feed_status.raw_symbol,
            dataset=self._feed_status.dataset,
            schema=schema or self._feed_status.schema,
            last_event_ts_utc=event_ts_utc,
            last_message="market context updated",
            metadata=dict(self._feed_status.metadata),
        )
        return RuntimeUpdate(feed_status=self._feed_status) if was_disconnected else RuntimeUpdate()


_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s,;]+")
_POSIX_PATH_RE = re.compile(r"/(?:[^\s,;]+/)+[^\s,;]+")
_SECRET_RE = re.compile(r"(?i)(secret|token|password|api[_-]?key)\s*[:=]\s*[^\s,;]+")
_SECRET_WORD_RE = re.compile(r"(?i)secret|token|password|api[_-]?key")


def _safe_text(value: str) -> str:
    value = _WINDOWS_PATH_RE.sub("<path>", value)
    value = _POSIX_PATH_RE.sub("<path>", value)
    value = _SECRET_RE.sub("<redacted>", value)
    return _SECRET_WORD_RE.sub("<redacted>", value)


def _safe_metadata(value: Any) -> Any:
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, dict):
        return {_safe_key(key): _safe_metadata(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_metadata(item) for item in value]
    return value


def _safe_key(key: object) -> str:
    text = _safe_text(str(key))
    return "<redacted_key>" if _SECRET_WORD_RE.search(text) else text


def _safe_source(source: str | None) -> str | None:
    if source is None:
        return None
    sanitized = _safe_text(source)
    if sanitized == source and ("/" in source or "\\" in source):
        return source.replace("\\", "/").rsplit("/", 1)[-1]
    return sanitized


def _safe_warning(warning: DataQualityWarning) -> DataQualityWarning:
    return DataQualityWarning(
        code=warning.code,
        message=_safe_text(warning.message),
        severity=warning.severity,
        source=_safe_source(warning.source),
        event_ts_utc=warning.event_ts_utc,
        metadata=_safe_metadata(dict(warning.metadata)),
    )
