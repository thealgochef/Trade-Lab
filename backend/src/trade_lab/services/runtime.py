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

from strategy_core import StreamingHonestResolver, StreamResolution

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
from trade_lab.domain.outcomes import DroppedPrediction, Outcome
from trade_lab.services.inference.features import FeatureComputationError
from trade_lab.services.inference.inference_engine import InferenceEngine, Prediction
from trade_lab.services.inference.resolution_adapter import (
    drop_to_dropped,
    parse_bar_type,
    resolution_to_outcome,
)
from trade_lab.services.journal import PredictionJournal
from trade_lab.services.strategy_core_service import StrategyCoreService

logger = logging.getLogger(__name__)

#: W1 P3b: slack added on top of approach + interaction when deriving the
#: contract-driven market-context retention at activation.
MARKET_CONTEXT_RETENTION_SLACK_MINUTES = 10


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
    dropped: tuple[DroppedPrediction, ...] = ()
    #: W2 P2c: when set, the broadcaster emits a typed ``model.reset`` frame with
    #: this reason ("activation" | "replay_reset" | "live_reset") — replacing the
    #: frontend's 'runtime reset' feed-message substring trigger.
    model_reset_reason: str | None = None

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
                self.dropped,
                self.model_reset_reason is not None,
            )
        )


@dataclass(frozen=True, slots=True)
class _PreparedRebind:
    """W2 P2b: a fully-constructed engine rebind awaiting its atomic commit."""

    engine: InferenceEngine | None
    resolver: StreamingHonestResolver | None
    market_context_retention: timedelta


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
    dropped: tuple[DroppedPrediction, ...] = ()
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
        journal: PredictionJournal | None = None,
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
        # D1b: drops are surfaced explicitly, ring-bounded like outcomes (same cap).
        self._dropped: list[DroppedPrediction] = []
        # Open registrations awaiting a resolver emission, keyed by prediction_id —
        # cleared in lockstep with the resolver (reset / hot-swap / clear).
        self._open_predictions: dict[object, Prediction] = {}
        self._warning_limit = warning_limit
        self._recent_closed_bar_limit = recent_closed_bar_limit
        self._seed_bar_limit_per_timeframe = seed_bar_limit_per_timeframe
        self._tick_timeframes = tick_timeframes
        self._observation_duration_seconds = observation_duration_seconds
        # D1b: the SC streaming honest resolver IS the serving outcome path (the D1a
        # dark seat promoted; the legacy level-anchored OutcomeTracker is retired).
        # W1 P3b: a constructor-supplied engine gets the same contract-driven
        # retention a hot-swap would apply.
        ctor_active = inference_engine.active() if inference_engine is not None else None
        self._honest_resolver: StreamingHonestResolver | None = self._build_honest_resolver(
            None if ctor_active is None else ctor_active.contract
        )
        self.market_context.set_retention(self._market_context_retention_for(ctor_active))
        # W2 P2e (D-P-07): append-only prediction/outcome/drop journaling so
        # serving evidence survives restarts. Write-only this window.
        self._journal = journal
        # W2 P2d: named-feature inference failure diagnostics, surfaced on the
        # status endpoint. Deliberately NOT cleared by reset() — they are
        # process-lifetime serving health, not per-session market state.
        self._inference_error_count = 0
        self._last_inference_error: dict[str, str | None] | None = None
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
        reset_reason: str | None = None,
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
        self._dropped.clear()
        self._open_predictions.clear()
        if self._honest_resolver is not None:
            self._honest_resolver.reset()
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
        return RuntimeUpdate(feed_status=self._feed_status, model_reset_reason=reset_reason)

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

        W2 P2b: composed from :meth:`prepare_inference_rebind` (everything that can
        raise) + :meth:`commit_inference_rebind` (the assignment, cannot raise) so
        the activation endpoint can validate fully BEFORE the registry swap and
        commit both together with no awaits between them.
        """

        active = engine.active() if engine is not None else None
        self.commit_inference_rebind(self.prepare_inference_rebind(engine, active))

    def prepare_inference_rebind(
        self, engine: InferenceEngine | None, active: "Any"
    ) -> "_PreparedRebind":
        """Build everything an engine rebind needs, WITHOUT mutating the runtime.

        ``active`` is the CANDIDATE ActiveModel (may differ from the registry's
        current one during an atomic activation). Resolver construction fails loud
        here — before any swap — so a bad activation leaves the serving state
        untouched (F12).
        """

        resolver = (
            None if active is None else self._build_honest_resolver(active.contract)
        )
        return _PreparedRebind(
            engine=engine,
            resolver=resolver,
            market_context_retention=self._market_context_retention_for(active),
        )

    def commit_inference_rebind(self, prepared: "_PreparedRebind") -> None:
        """Apply a prepared rebind: pure assignment, no I/O, nothing can raise.

        Called together with the registry swap (no awaits between) so concurrent
        readers on the event loop always see a consistent (registry, resolver,
        retention) triple.
        """

        self._inference_engine = prepared.engine
        self._predictions.clear()
        self._outcomes.clear()
        self._dropped.clear()
        self._open_predictions.clear()
        self._honest_resolver = prepared.resolver
        self.market_context.set_retention(prepared.market_context_retention)

    def flush_resolver(self, now_ts_utc=None) -> RuntimeUpdate:
        """W2 P2a (F10): finalize open setups whose cutoff is at/before ``now``.

        Wired at replay stream end (last event instant), live stop (wall clock),
        and hot-swap (the OLD resolver, before the new one is built). Flushed
        drops flow through the existing drop -> DroppedPrediction adapter onto
        the same WS/status surfaces as in-stream drops, and are journaled.
        """

        resolver = self._honest_resolver
        if resolver is None:
            return RuntimeUpdate()
        if now_ts_utc is None:
            now_ts_utc = self._feed_status.last_event_ts_utc
        if now_ts_utc is None:
            return RuntimeUpdate()
        try:
            emitted = resolver.flush(now_ts_utc)
        except Exception:
            logger.warning("honest-resolver flush failed", exc_info=True)
            return RuntimeUpdate()
        dropped: list[DroppedPrediction] = []
        for drop in emitted:
            prediction = self._open_predictions.pop(drop.key, None)
            if prediction is None:
                logger.warning("honest-resolver flush emission has no open prediction")
                continue
            served = drop_to_dropped(drop, prediction)
            self._append_dropped(served)
            dropped.append(served)
        self._journal_records((), (), tuple(dropped))
        return RuntimeUpdate(dropped=tuple(dropped))

    def _market_context_retention_for(self, active: "Any") -> timedelta:
        """W1 P3b: buffer retention follows the ACTIVE contract's feature windows.

        Effective retention = approach + interaction + slack minutes (inference
        fires ~interaction after the touch while approach features reach back to
        touch - approach, so both windows stack; the slack absorbs scheduling
        jitter). Note this is deliberately >= the W1 spec's
        ``max(approach, interaction) + slack``, which under-retains whenever the
        interaction window exceeds the slack. No active section -> the configured
        baseline. The activation gate refuses contracts whose requirement exceeds
        the configured ceiling before this ever applies.
        """

        minutes = self._market_context_retention_minutes
        section = getattr(active, "section", None) if active is not None else None
        if section is not None:
            windows = section.feature_windows
            minutes = (
                windows.approach_window_minutes
                + windows.interaction_window_minutes
                + MARKET_CONTEXT_RETENTION_SLACK_MINUTES
            )
        return timedelta(minutes=minutes)

    def _build_honest_resolver(self, contract) -> StreamingHonestResolver | None:
        """Construct the contract-specific SC streaming honest resolver.

        The serving outcome path (D1b): built from the activating contract so a
        hot-swap re-derives forward bar type + thresholds. Entry prints come from
        the Strategy-Core runtime's trade ring via a late-binding closure so the
        seam survives ``reset()`` swapping the service. Construction FAILS LOUD
        when the contract's forward bar type is not among the runtime's configured
        tick timeframes — the recon's silent-never-resolve hole — so a bad
        activation is rejected instead of silently tracking nothing. Returns
        ``None`` when no contract is supplied.
        """

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
            forward_timeframe_ticks=parse_bar_type(policy.forward_bar_type),
            tick_size=contract.tick_size,
            tp_points=policy.tp_points,
            sl_points=policy.sl_points,
            trap_mfe_min=policy.trap_mfe_min,
            decision_offset_minutes=policy.decision_offset_minutes,
            trade_price_at=lambda ts_utc: self.strategy_core_service.trade_price_at(ts_utc),
            available_timeframes=self._tick_timeframes,
        )

    @property
    def dropped(self) -> tuple[DroppedPrediction, ...]:
        """The served drops ring (mirrors ``outcomes``; same cap)."""

        return tuple(self._dropped)

    def _append_dropped(self, dropped: DroppedPrediction) -> None:
        self._dropped.append(dropped)
        if len(self._dropped) > self._outcome_limit:
            del self._dropped[: len(self._dropped) - self._outcome_limit]

    def _register_prediction(
        self,
        resolver: StreamingHonestResolver,
        prediction: Prediction,
        observation: Observation,
    ) -> DroppedPrediction | None:
        """Register one prediction with the resolver off its TOUCH anchors.

        The decision anchor is the touch's bar-close instant + trading day carried on
        the observation/touch chain (``Observation.start_ts_utc`` = the SC touch's
        ``bar_ts_utc``) — NEVER the prediction's ``event_ts_utc`` (= touch + the
        observation window). Fails loud if the chain did not carry them. A
        registration-time drop (flatten/cutoff/no_fill) is adapted, ring-appended,
        and returned so the caller can broadcast it; a live registration is held in
        ``_open_predictions`` until the resolver emits for it.
        """

        if observation.start_ts_utc is None or observation.trading_day is None:
            raise ValueError(
                "honest-resolver registration requires the touch bar ts + trading_day "
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
            # Outcome tracking must never break the market-data hot path.
            logger.warning("honest-resolver registration failed", exc_info=False)
            return None
        if drop is not None:
            dropped = drop_to_dropped(drop, prediction)
            self._append_dropped(dropped)
            return dropped
        self._open_predictions[prediction.prediction_id] = prediction
        return None

    def clear_predictions(self) -> None:
        """Drop accumulated predictions + outcomes + drops (e.g. on model hot-swap)."""

        self._predictions.clear()
        self._outcomes.clear()
        self._dropped.clear()
        self._open_predictions.clear()
        if self._honest_resolver is not None:
            self._honest_resolver.reset()

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
            # E3 ledger (c): the REAL activation-time metadata cross-check result
            # carried on the active bundle — no longer hardcoded True.
            validation_ok=active.validation_ok,
            validation_detail=active.validation_detail,
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
    ) -> tuple[tuple[Prediction, ...], tuple[DroppedPrediction, ...]]:
        """Produce predictions for observations that just completed.

        An observation "completes" when the engine expires it at its scheduled end
        (its full interaction window has elapsed). With no active model this yields
        nothing and the runtime keeps serving market data unchanged. Each produced
        prediction is registered with the honest resolver off its TOUCH anchors;
        registration-time drops (flatten/cutoff/no_fill) are returned alongside the
        predictions so they ride the same RuntimeUpdate.
        """

        engine = self._inference_engine
        if engine is None or not engine.has_active_model:
            return (), ()
        produced: list[Prediction] = []
        # (prediction, observation) pairs: the resolver registers off the TOUCH
        # anchors carried on the observation chain, not off the prediction timestamps.
        produced_pairs: list[tuple[Prediction, Observation]] = []
        for observation in changed_observations:
            if observation.status is not ObservationStatus.EXPIRED:
                continue
            try:
                prediction = engine.predict_for_observation(observation, self.market_context)
            except FeatureComputationError as exc:
                # Inference must never break the market-data hot path; W2 P2d: the
                # failing FEATURE is named, counted, and surfaced on status.
                self._record_inference_error(exc.feature_name, observation)
                logger.warning(
                    "inference failed computing feature %r for a completed observation",
                    exc.feature_name,
                    exc_info=True,
                )
                continue
            except Exception:
                self._record_inference_error(None, observation)
                logger.warning(
                    "inference failed for a completed observation", exc_info=True
                )
                continue
            if prediction is not None:
                produced.append(prediction)
                produced_pairs.append((prediction, observation))
        dropped: list[DroppedPrediction] = []
        if produced:
            self._predictions.extend(produced)
            if len(self._predictions) > self._prediction_limit:
                del self._predictions[: len(self._predictions) - self._prediction_limit]
            resolver = self._honest_resolver
            if resolver is not None:
                for prediction, observation in produced_pairs:
                    drop = self._register_prediction(resolver, prediction, observation)
                    if drop is not None:
                        dropped.append(drop)
        return tuple(produced), tuple(dropped)

    def _track_outcomes(
        self, closed_bars: tuple[Candle, ...]
    ) -> tuple[tuple[Outcome, ...], tuple[DroppedPrediction, ...]]:
        """Advance the honest resolver on each just-closed contract bar.

        Only forward-bar-type closes advance setups; non-matching timeframes are
        ignored inside the resolver. Resolutions are adapted to served ``Outcome``s
        (correctness computed here); terminal drops (no_forward/no_resolution) to
        served ``DroppedPrediction``s. Outcome tracking must never break the
        market-data hot path, so failures are swallowed.
        """

        resolver = self._honest_resolver
        if resolver is None or not closed_bars:
            return (), ()
        resolved: list[Outcome] = []
        dropped: list[DroppedPrediction] = []
        for bar in closed_bars:
            try:
                emitted = resolver.on_bar(bar)
            except Exception:
                logger.warning(
                    "honest-resolver advance failed for a closed bar", exc_info=False
                )
                continue
            for item in emitted:
                # Per-item guard: a failing adaptation loses only THAT emission
                # (logged), never the bar's other emissions or the trade event.
                try:
                    prediction = self._open_predictions.pop(item.key, None)
                    if prediction is None:
                        # Lifecycle bug guard: the map clears in lockstep with the
                        # resolver, so an emission must always find its registration.
                        logger.warning("honest-resolver emission has no open prediction")
                        continue
                    if isinstance(item, StreamResolution):
                        resolved.append(resolution_to_outcome(item, prediction))
                    else:
                        drop = drop_to_dropped(item, prediction)
                        self._append_dropped(drop)
                        dropped.append(drop)
                except Exception:
                    logger.warning(
                        "honest-resolver emission could not be served", exc_info=False
                    )
        if resolved:
            self._outcomes.extend(resolved)
            if len(self._outcomes) > self._outcome_limit:
                del self._outcomes[: len(self._outcomes) - self._outcome_limit]
        return tuple(resolved), tuple(dropped)

    def _record_inference_error(self, feature_name: str | None, observation) -> None:
        self._inference_error_count += 1
        ts = getattr(observation, "scheduled_end_ts_utc", None)
        self._last_inference_error = {
            "feature_name": feature_name,
            "ts_utc": None if ts is None else ts.isoformat(),
        }

    def inference_health(self) -> dict[str, Any]:
        """W2 P2d: inference failure diagnostics for the status endpoint."""

        return {
            "error_count": self._inference_error_count,
            "last_error": self._last_inference_error,
        }

    def _journal_records(
        self,
        predictions: tuple[Prediction, ...],
        outcomes: tuple[Outcome, ...],
        dropped: tuple[DroppedPrediction, ...],
    ) -> None:
        """W2 P2e (D-P-07): append the served records to the prediction journal.

        Tagged with the runtime mode (replay|live via feed status) and the active
        bundle id. Journal failures are logged and never break the hot path.
        """

        journal = self._journal
        if journal is None or not (predictions or outcomes or dropped):
            return
        mode = self._feed_status.mode
        engine = self._inference_engine
        active = engine.active() if engine is not None else None
        bundle_id = None if active is None else active.model_id
        for prediction in predictions:
            journal.record_prediction(prediction, mode=mode)
        for outcome in outcomes:
            journal.record_outcome(outcome, mode=mode, bundle_id=bundle_id)
        for drop in dropped:
            journal.record_drop(drop, mode=mode, bundle_id=bundle_id)

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
            dropped=tuple(self._dropped),
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
        predictions, registration_drops = self._run_inference(tuple(changed_observations))
        # Resolve any open predictions against bars that just closed on this trade.
        outcomes, terminal_drops = self._track_outcomes(core_update.closed_bars)
        for touch in core_update.touches:
            changed_observations.append(self.observations.start_from_touch(touch))
        self._feed_status = self._feed_status_for_mode(core_update.feed_status, trade=trade)
        self._journal_records(
            predictions, outcomes, (*registration_drops, *terminal_drops)
        )
        return RuntimeUpdate(
            feed_status=self._feed_status,
            current_bars=core_update.current_bars,
            closed_bars=core_update.closed_bars,
            display_levels=core_update.display_levels,
            touches=core_update.touches,
            observations=tuple(changed_observations),
            predictions=predictions,
            outcomes=outcomes,
            dropped=(*registration_drops, *terminal_drops),
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
