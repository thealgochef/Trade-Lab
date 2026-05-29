# Research Integration Contract — Phase 1

Trade-Lab consumes research outputs by versioned contract. Claude-Quant-Lab trains models and produces artifacts. Trade-Lab must not copy research code into runtime.

Phase 1-4 does not load ML models. This document defines the future integration boundary so runtime and research can align before inference is introduced.

## Contract Identity

Initial contract name:

```text
trade_lab_contract_v1
```

The contract must define:

- data schema version;
- canonical event assumptions;
- session rules;
- level rules;
- touch rules;
- bar settings;
- feature schema;
- label mapping;
- thresholds;
- artifact checksums.

## Current Alignment Warning

The existing research pipeline is not aligned with the Trade-Lab v1 runtime specification.

Known differences include:

- research sessions currently use Eastern-time assumptions;
- research PDH/PDL currently use prior RTH session only;
- research touch zones currently use 3-point zones and/or bar high-low touch logic;
- research touch policy currently uses first touch per zone/day.

Trade-Lab v1 requires:

- `America/Chicago` session logic;
- trading day `6:00 PM CT -> 4:00 PM CT`;
- PDH/PDL from prior full trading day;
- exact-price touch using last traded price only;
- integer tick equality;
- one touch per level per session;
- tick bars only: `147t`, `987t`, `2000t`.

Future research training must be updated to match `trade_lab_contract_v1` before models are considered aligned.

## Artifact Layout

A future model artifact package should contain:

```text
artifact.json
feature_schema.json
model.cbm
training_report/
  evaluation.*
  metrics.*
  plots_or_tables.*
checksums.*
```

The model file name may vary by model family, but `model.cbm` is the expected CatBoost artifact name if CatBoost is used.

## `artifact.json` Requirements

`artifact.json` should include:

- artifact id;
- artifact version;
- contract name and version;
- data schema version;
- model type;
- training code version or commit id;
- training data date range;
- instrument/product;
- bar type and bar sizes;
- tick definition;
- session rules;
- level rules;
- touch rules;
- label mapping;
- decision thresholds;
- feature schema file reference;
- model file reference;
- evaluation report reference;
- checksums for all files.

## `feature_schema.json` Requirements

`feature_schema.json` should define:

- ordered feature column list;
- feature names, types, units, and nullability;
- categorical feature declarations;
- default/missing-value handling;
- required runtime inputs;
- incompatible schema changes;
- schema checksum.

Feature order is part of the contract. Runtime inference must reject artifacts whose feature order or schema checksum does not match the registered contract.

## Runtime Validation Requirements

When ML is added later, Trade-Lab should validate:

- contract name/version is supported;
- data schema version is supported;
- artifact checksum matches;
- model checksum matches;
- feature schema checksum matches;
- runtime session/level/touch/bar settings match the artifact;
- class mapping and thresholds are present;
- model was trained on compatible instrument and tick definition.

Invalid artifacts must fail closed and produce a visible data/model-quality warning.

## Architecture Boundary

Future runtime integration should use ports such as:

- `ModelRegistry`: discovers and validates artifacts;
- `FeatureBuilder`: builds contract-versioned features from runtime observations;
- `InferenceEngine`: scores validated observations;
- `PredictionPublisher`: emits predictions and diagnostics.

These ports are placeholders for future phases. Phase 1-4 should not load models, run inference, upload artifacts, or execute trades.

Research alignment depends on the same engine boundaries as runtime:

- Candles and candle-derived features used by research must come from the Trade-Lab `CandleEngine` contract or a bit-for-bit equivalent implementation.
- Session, level, touch, and observation features must match the runtime contracts used by `SessionLevelEngine` and `ObservationEngine`.
- Risk decisions, broker execution behavior, fills, rejects, slippage, and account outcomes are not part of model-training labels unless a future contract explicitly models those outcomes.
- Signal/inference artifacts may produce probabilities or signal intent only; execution and risk outcomes remain separate runtime concerns.

## Open Questions

- Final feature schema for exact-price touch observations.
- Label definitions and prediction horizon.
- Model threshold ownership: research artifact only, runtime override, or both with audit trail.
- Required evaluation report format for model promotion.
