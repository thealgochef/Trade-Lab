# Trade-Lab Architecture Specification — Current Status

Trade-Lab is a new institutional-grade NQ trading and replay dashboard. It is separate from Trade-Dashboard and must not copy legacy implementation details or business logic blindly. Existing systems may be used only as light reference for user intent and operational lessons.

The primary architectural goals are:

- clean boundaries between domain, ports, adapters, services, and API;
- high-throughput live and replay processing with no avoidable hot-path bottlenecks;
- one canonical market-data pipeline shared by live and replay;
- research integration through explicit versioned contracts, not copied research code;
- a professional workstation UI that makes data quality and state visible.

## Current status

The original Phase 1-5 core plan is implemented. The backend domain/runtime path,
safe replay controls, frontend workstation, and explicit opt-in Databento live
market-data wiring are complete. The remaining original-plan work is manual live
validation during active market hours; this should not be treated as complete until
an operator performs and records the runbook checks.

Latest verification:

- Backend full pytest: `268 passed, 1 skipped`.
- Backend ruff: all checks passed.
- Benchmark gate: passed.
- Frontend lint, typecheck, test, and build: passed.
- Frontend tests: `97 passed`.

## Phase Scope

### Phase 1

- Define architecture, data contracts, session/level/touch semantics, research contract, and frontend workstation design.
- Establish project boundaries and implementation constraints.
- No ML inference, model upload, trading execution, account management, or broker order routing.

### Phase 2

- Implement Databento live ingestion and local historical/replay ingestion.
- Normalize to canonical events.
- Build integer-tick price handling, tick bars, session levels, exact-price touches, and observations.
- Stream batched/throttled updates to the frontend.

### Phase 3

- Build the professional frontend workstation.
- Display tick bars, levels, eligibility, touches, observations, feed health, replay state, and event blotter.

### Phase 4

- Harden replay/backtesting readiness and research contract validation.
- Prepare, but do not yet enable, model registry and inference ports.

### Phase 5 Current Implementation Scope

- Public replay controls expose allowlisted synthetic/in-memory replay sources and
  opaque allowlisted historical source ids discovered under the configured local
  `TRADE_LAB_DATA_PATH`.
- No public endpoint accepts caller-supplied local file paths or raw-data locations.
- Historical source labels and API responses must not expose local filenames,
  relative path components, temp directories, or configured data-root paths.
- Historical replay is limited to live-compatible schemas/projections such as
  trades, MBP-1, BBO, and supported local Databento-export MBP-10. Unsupported
  deeper schemas such as MBO and MBP-2 through MBP-9/depth-only files are not
  advertised as runtime replay sources.
- Opt-in live market-data controls exist for onboarding/config/UI validation; they
  never auto-start on backend startup.
- Live controls are market-data only and are guarded by localhost access by
  default, with an optional operator token for future non-local use.
- Phase 5B wires Databento SDK callbacks through a bounded adapter queue into the
  same canonical runtime path used by replay/fake feeds. Tests use fake SDK clients
  only and do not make provider network calls.
- Phase 5C provides the live Databento validation runbook and safe preflight helper.
- Manual live validation remains pending and must be run explicitly by an operator;
  live mode never auto-starts.
- Model inference, trading execution, risk/account management, and broker routing remain out of scope.

## Runtime Stack Direction

Backend target port: `8001`.

Frontend target port: `5174`.

Node is limited to frontend development and build tooling. It is not the backend or runtime engine for market-data ingestion, historical replay, candle/session/touch engines, or future ML inference.

Preferred backend stack direction:

- FastAPI/Starlette for HTTP and WebSocket API boundaries.
- Databento SDK for implemented opt-in live market data only. Historical replay
  uses the local Databento-export Parquet catalog/adapter under
  `TRADE_LAB_DATA_PATH`; it does not require or imply Databento SDK calls.
  Manual live validation remains pending and operator-run.
- PyArrow/Polars for columnar historical/replay processing outside the per-tick hot path.
- `orjson` or `msgspec` for fast serialization.
- `dataclasses` with `slots=True` or `msgspec.Struct` for hot-path domain events.
- Pydantic only at configuration and external API boundaries.

The default runtime stack is Python. A future Go or Rust hot service may be introduced only after benchmarks show a specific Python component cannot meet the documented acceptance criteria with the stack above. Do not prematurely move ingestion, replay, candle, session, touch, or inference code into a lower-level language without evidence and a narrow service boundary.

Avoid in hot paths:

- pandas per tick;
- Pydantic model creation per tick;
- floating-point price comparison;
- database writes per tick;
- raw tick spam over WebSocket;
- separate live and replay business logic.

## Backend Module Boundaries

The implementation should follow ports-and-adapters boundaries.

```text
backend/
  domain/      # canonical events, bars, sessions, levels, touches, observations
  ports/       # MarketDataFeed, EventPublisher, Clock, ArtifactRegistry interfaces
  adapters/    # Databento live, local historical parquet replay, websocket publisher
  services/    # pipeline orchestration, replay controller, snapshot service
  api/         # FastAPI routes, websocket endpoint, DTO translation
```

### Domain

Pure business rules. No Databento SDK objects, HTTP objects, database clients, or frontend DTO assumptions.

Owns:

- canonical market-data events;
- integer tick conversion;
- tick-bar aggregation;
- trading-day/session state;
- level calculation;
- touch eligibility and detection;
- observation lifecycle.

### Ports

Interfaces that express what the domain/services need without coupling to implementation.

Initial ports:

- `MarketDataFeed`: emits canonical events for live or replay.
- `RealtimePublisher`: publishes snapshots/deltas to connected clients.
- `ReplayController`: controls historical playback start, pause, resume, stop,
  speed, and end-of-stream; seek is not implemented and remains a future option.
- `ContractRegistry` or future `ModelRegistry`: validates research artifacts when ML is added.

### Adapters

Adapters translate external systems into ports. The public replay control surface is limited to allowlisted synthetic/in-memory sources plus opaque historical source ids discovered from the configured local `TRADE_LAB_DATA_PATH`. The API never accepts arbitrary caller-supplied paths, never exposes full paths or filenames, and historical discovery advertises only live-compatible schemas/projections. Local Databento-export Parquet replay is separate from live Databento SDK subscription wiring. Opt-in live controls start Databento only after an explicit operator action, bridge SDK callbacks through a bounded queue, and emit canonical events/status/warnings.

- Databento live feed adapter;
- local Databento-export Parquet replay adapter/catalog;
- WebSocket adapter;
- optional file/parquet reader for read-only historical data.

### Services

Services orchestrate domain components and adapters:

```text
MarketDataFeed source (Phase 4B: synthetic replay or allowlisted local historical replay)
  -> canonical event normalization
  -> integer tick conversion
  -> tick bars
  -> session/level updater
  -> touch detector
  -> observation tracker
  -> batched/throttled websocket publishing
```

Live and replay must use the same `MarketDataFeed` contract and canonical event pipeline so semantics match.

Phase 2C implements this as `ApplicationRuntime`, which owns `CandleEngine`,
`SessionLevelEngine`, `ObservationEngine`, feed status, and warning state behind a
single `process_market_event()` method. `HistoricalReplayService` consumes a
`HistoricalMarketDataSource` and feeds only canonical live-compatible events into
that runtime; warnings are recorded separately and broadcast as data-quality
deltas. WebSocket fan-out serializes domain snapshots/deltas at the API boundary
and uses bounded per-client queues so slow clients do not force raw tick buffering.

## Bounded Engines and Services

Trade-Lab separates market-data processing, signal generation, risk policy, and execution into bounded engines. Engines communicate through explicit domain events on an `EventBus` or equivalent port boundary. They must not form direct cyclic dependencies or exchange frontend/UI payloads.

Phase 1-5 implements only the market-data foundations, `CandleEngine`, `SessionLevelEngine`, `ObservationEngine`, runtime/replay services, frontend workstation, and opt-in live market-data wiring. `RiskEngine`, `TradingEngine`/`ExecutionEngine`, and live inference are future modules, but their boundaries are reserved now so early engines do not grow execution or risk responsibilities.

### MarketDataEngine / Adapter Layer

- Normalizes Databento live inputs and local historical/replay inputs into canonical market-data events.
- Owns feed-specific translation, sequencing, and data-quality reporting.
- Emits canonical `TradeEvent` records that live and replay consumers handle identically.
- Historical MBP-10 replay emits `TradeEvent` records only from trade action rows
  (`T`/`Trade` variants), so candles and touches use last traded price only. It
  may emit optional `TopOfBookEvent` context from level 0 bid/ask. Deeper MBP-10
  levels are intentionally ignored as runtime features.

### CandleEngine

- Builds tick bars from canonical `TradeEvent` records only.
- Owns deterministic bar aggregation for configured tick sizes such as `147t`, `987t`, and `2000t`.
- Does not make trading, risk, or model decisions.
- Must be deterministic and replayable: the same canonical trade stream must produce the same candle stream in live and replay.

### SessionLevelEngine

- Owns session state, developing levels, final level eligibility, and touch detection.
- Uses canonical `TradeEvent` records as the authoritative input for session highs/lows and touch detection.
- May consume candle snapshots only for display context, derived analytics, or other non-authoritative uses unless a future versioned contract explicitly changes level/touch semantics.
- Must avoid timeframe-dependent level or touch behavior; changing the displayed candle size must not change authoritative session levels or touches.
- Emits level, eligibility, and touch domain events.
- Does not perform execution, account-risk checks, or order intent mutation.

### ObservationEngine

- Owns the post-touch observation lifecycle and, in later phases, feature preparation from observations.
- Consumes touch, level, candle, and market-state events as needed.
- Emits observation and feature-ready domain events.
- Does not place orders or apply account-risk policy.

### SignalEngine / Future InferenceEngine

- Produces signal intent, probabilities, diagnostics, and model-quality events from validated observations/features.
- Does not place broker orders.
- Cannot bypass risk; any order intent derived from a signal must go through `RiskEngine`.

### RiskEngine

- Independent policy gate for account, position, market-state, signal, and order-intent checks.
- Consumes the authoritative account/position view from `Portfolio/PositionService`.
- Owns risk limits and returns approved, rejected, or modified order intent with explicit reasons.
- Does not send broker orders or own broker execution state.

### TradingEngine / ExecutionEngine

- Converts risk-approved order intent into future broker/Rithmic orders.
- Owns order lifecycle, fills, cancels, execution reconciliation, and broker-facing error handling.
- Does not define, override, or reinterpret account-risk policy.
- May enforce mechanical broker/order-state constraints, such as order validity, duplicate prevention, and exchange/broker state compatibility, while executing only risk-approved intent.
- Cannot place orders without a `RiskApproval` event or equivalent approved order-intent contract.

### Portfolio/PositionService

- Provides the authoritative account, cash/equity, position, exposure, and fill-derived state.
- Is consumed by both `RiskEngine` and `TradingEngine`/`ExecutionEngine`.
- Does not decide signals or bypass risk approval.

### Boundary Invariants

- `CandleEngine` never imports or calls `RiskEngine`, `TradingEngine`/`ExecutionEngine`, or ML/inference modules.
- `RiskEngine` never sends broker orders.
- `TradingEngine`/`ExecutionEngine` cannot place orders without `RiskApproval`.
- `SignalEngine`/`InferenceEngine` cannot bypass `RiskEngine`.
- Replay and live use the same `CandleEngine` and `SessionLevelEngine` semantics.
- Engine outputs are domain events, not UI payloads. API/WebSocket adapters translate domain events into frontend DTOs.

## API Direction

The API provides:

- health/status endpoint;
- session and feed metadata endpoint;
- current snapshot endpoint for frontend bootstrap;
- WebSocket stream for bars, level updates, touches, observations, event log entries, and data-quality warnings;
- replay control endpoints.

API DTOs should be explicit and versioned where practical. Internal domain objects should not leak directly to the frontend.

## State and Persistence

Phase 1-5 does not require per-tick database persistence. Runtime state can be in memory with explicit snapshots for frontend bootstrap.

Historical replay may be enabled only through the guarded local `TRADE_LAB_DATA_PATH` setting. Replay endpoints expose opaque allowlisted source ids discovered from that root, never caller-supplied filesystem paths. Discovery and API payloads must avoid leaking local filenames or path components, and only live-compatible historical schemas/projections are advertised. Root/child symlink and reparse-point traversal is blocked, source ids reject path-like and Windows drive-like forms, and invalid-row warnings are sanitized without raw values, paths, filenames, secret-like source labels, sampled historical-only columns, or schema/column names. The `max_events` cap applies to replay items/updates, including warnings, not only successfully processed market events. Phase 5B live endpoints are opt-in guarded market-data controls; real Databento subscription startup occurs only after explicit operator start.

## Performance Principles

- Convert prices to integer ticks immediately after normalization.
- Compare prices with integer tick equality/ranges only.
- Keep per-event allocation low in the hot path.
- Batch and throttle frontend WebSocket messages.
- Send semantic deltas and compact snapshots, not raw tick floods.
- Use columnar libraries for bulk historical work, not per-tick processing.
- Keep live and replay code paths unified to avoid interpretation drift.

## Performance Acceptance and Benchmarks

Phase 2 benchmark coverage is implemented and currently passes. Benchmarks use synthetic or sanitized inputs and must not require secrets, raw production data, or model binaries.

For Phase 2A specifically, the opt-in backend domain hot-path benchmark processes synthetic `CandleEngine` + `SessionLevelEngine` events at `>= 100,000 events/sec` on the development workstation.

The initial gates below are provisional but pass/fail. They may be recalibrated only with documented benchmark evidence that records the machine, Python version, dependency versions, dataset shape, measured live NQ peak rate where applicable, and the old and new thresholds.

Initial acceptance criteria and phase ownership:

- Phase 2A domain hot path: process synthetic canonical `TradeEvent` streams through `CandleEngine` and `SessionLevelEngine` at `>= 100,000 events/sec` on the development workstation while also passing normal unit tests. This includes tick-bar construction for the configured `147t`, `987t`, and `2000t` bars.
- Replay streaming gate: read selected Parquet columns only. Replay code must not load full-day MBP files into memory to build runtime events. A one-day replay scan must stay below `1 GB` RSS on the development workstation. Replay throughput is measured during the replay implementation phase; until a baseline exists, the gate is `>= 5x` realtime for selected-column replay mode.
- WebSocket output gate: do not broadcast raw ticks by default. Publish chart/status updates through capped or coalesced messages at no more than `10-20 Hz` per client. Under synthetic load, p95 server serialization plus enqueue latency must be `<= 50 ms`.
- Queue and backpressure gate: runtime queues must be bounded with configured maximum depths. If a queue exceeds its threshold, the system must emit a data-quality/backpressure event and apply the configured policy, such as throttling, coalescing, or controlled dropping. Unbounded queue growth fails the gate.
- Frontend large-history gate: during the frontend phase, the workstation UI must load `50,000` tick bars and sustain `10 Hz` chart/status updates without browser long tasks over `100 ms`. This is a frontend contract, not a Phase 2 backend implementation requirement.

Benchmark results should be recorded with the machine, runtime versions, dependency versions, dataset shape, measured throughput, memory ceiling, and pass/fail threshold so stack decisions remain evidence-based.

## Future Extension Points

The architecture must support later additions beyond Phase 1-5:

- model registry and inference port;
- replay/backtest runner;
- prediction stream and model diagnostics;
- order execution and account-risk ports;
- durable event storage.

These are extension points only. Phase 1-5 does not load models, upload artifacts, place trades, or manage accounts. Future risk and execution modules must attach at the reserved engine boundaries above instead of being embedded in market-data, candle, session/level, or observation code.

## Future roadmap

- Phase 6 — stabilization and live market validation.
- Phase 7 — persistence and production hardening.
- Phase 8 — research/model integration through the existing versioned contracts.
- Phase 9 — trading, risk, and execution layer only when explicitly requested.

## Remaining original-plan validation

- Complete the manual live Databento validation runbook during active market hours.
- Configure `TRADE_LAB_DATA_PATH` and manually verify that supported MBP-10 local
  historical sources appear in the replay catalog and replay correctly.
- Confirm live status transitions, chart updates, timeframe switching, and stop
  behavior with sanitized evidence only.
