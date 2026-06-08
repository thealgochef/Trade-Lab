# Research Integration Contract — Strategy-Core Runtime Boundary

Trade-Lab consumes research/model outputs by versioned contract. Quant-Lab trains
models and produces artifacts. Trade-Lab must not copy research code into runtime;
shared semantics belong in Strategy-Core.

## Contract Identity

Current contract family:

```text
trade_lab_contract_v1
```

The contract must define:

- data schema version;
- canonical event assumptions;
- Strategy-Core session rules;
- Strategy-Core level/zone/touch rules;
- bar settings;
- feature schema and ordered feature list;
- label mapping;
- thresholds;
- artifact checksums.

## Runtime Semantics Source of Truth

Trade-Lab runtime bars, sessions, levels/zones, and touches are delegated to
`strategy_core.runtime.StrategyRuntime` through
`trade_lab.services.strategy_core_service.StrategyCoreService`.

Current Strategy-Core v3 runtime requirements:

- `US/Eastern` session logic;
- trading-day boundary at `18:00 ET`;
- sessions: Asia `19:00→02:45 ET`, London `03:00→08:00 ET`, NY `09:00→17:00 ET`;
- tick bars only for runtime display/decision timeframes such as `147t`, `987t`,
  and `2000t`;
- PDH/PDL from complete prior Strategy-Core trading-day summaries;
- levels within `3.0` points merge into zones;
- touch fires when a closed Strategy-Core decision bar range intersects the zone
  representative;
- first touch is tracked per zone per Strategy-Core trading day;
- quotes/top-of-book are context only and do not create bars or touches.

Any research artifact trained under older Trade-Lab Chicago/exact-price touch
semantics is not runtime-aligned and must be retrained, quarantined, or explicitly
measured before live precision claims are trusted.

## Strategy-Core Promotion and Trade-Lab Alignment

Trade-Lab alignment is controlled through an explicit Strategy-Core dependency pin,
not by silently following whatever local Strategy-Core checkout is present. When
research changes strategy/runtime behavior, Strategy-Core must be committed and
pushed first, then Trade-Lab must bump the pinned Strategy-Core commit in
`backend/pyproject.toml`, reinstall the backend, and rerun backend/frontend/replay
validation before the runtime is called aligned.

The operational runbook is:

```text
docs/strategy-core-alignment-runbook.md
```

That runbook defines the aligned/not-aligned states, pin-bump process, validation
commands, historical replay evidence requirements, and model-serving gate.

## Artifact Layout

A model artifact package should contain:

```text
artifact.json or strategy.json
feature_schema.json or embedded feature_set
model.cbm
training_report/
  evaluation.*
  metrics.*
  plots_or_tables.*
checksums.*
```

The model file name may vary by model family, but `model.cbm` is the expected
CatBoost artifact name if CatBoost is used.

## Artifact Metadata Requirements

The artifact/strategy metadata should include:

- artifact id and version;
- contract name/version;
- data schema version;
- model type;
- training code version or commit id;
- training data date range;
- instrument/product;
- tick definition;
- bar type and bar sizes;
- session rules;
- level/zone/touch rules;
- label mapping;
- decision thresholds;
- feature schema reference or embedded schema;
- model file reference;
- evaluation report reference;
- checksums for all required files.

## Feature Schema Requirements

The feature schema should define:

- ordered feature column list;
- feature names, types, units, and nullability;
- categorical feature declarations;
- default/missing-value handling;
- required runtime inputs;
- incompatible schema changes;
- schema checksum.

Feature order is part of the contract. Runtime inference must reject artifacts
whose feature order or schema checksum does not match the registered contract.

## Runtime Validation Requirements

Trade-Lab should validate:

- contract name/version is supported;
- data schema version is supported;
- artifact checksum matches;
- model checksum matches;
- feature schema checksum matches;
- runtime session/level/touch/bar settings match Strategy-Core v3 or an explicitly
  supported future Strategy-Core variant;
- class mapping and thresholds are present;
- model was trained on compatible instrument and tick definition.

Invalid artifacts must fail closed and produce a visible data/model-quality
warning.

## Architecture Boundary

Runtime integration uses ports/services such as:

- `ModelRegistry`: discovers and validates artifacts;
- `FeatureBuilder`/feature registry: builds contract-versioned features from
  runtime observations and L1/L0 context;
- `InferenceEngine`: scores validated observations;
- `PredictionPublisher`/WebSocket broadcaster: emits predictions and diagnostics.

Research alignment depends on the same Strategy-Core engine boundaries as
runtime:

- Candles and candle-derived features used by research must come from
  Strategy-Core candle contracts or bit-for-bit equivalent implementations.
- Session, level, zone, touch, and observation features must match the runtime
  contracts used by Strategy-Core and Trade-Lab’s `ObservationEngine`.
- Risk decisions, broker execution behavior, fills, rejects, slippage, and account
  outcomes are not part of model-training labels unless a future contract
  explicitly models those outcomes.
- Signal/inference artifacts may produce probabilities or signal intent only;
  execution and risk outcomes remain separate runtime concerns.

## Open Questions

- Final promoted feature schema for exact model-bundle parity fixtures.
- Promotion policy for old bundles trained before Strategy-Core v3 alignment.
- Model threshold ownership: research artifact only, runtime override, or both
  with audit trail.
- Required evaluation report format for model promotion.
