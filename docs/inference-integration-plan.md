# Trade-Lab — Live Inference Integration Plan

Status: **IMPLEMENTED through Stage 6** (Stages Q, 0–6) · **Strategy-Core
runtime seam implemented** · Scope: Trade-Lab runtime + `strategy.json` emission
in Claude-Quant-Lab.

Implementation note: the inference vertical now runs on Trade-Lab's shared
Strategy-Core runtime path for bars, ET sessions, levels/zones, and bar-range
first touches. Trade-Lab parses and surfaces model-contract session/touch/level
settings; Strategy-Core currently owns the active runtime implementation rather
than hot-swapping multiple strategy variants per bundle. Features, labels,
thresholds, classes, windows, and the confidence gate are contract-driven.
Therefore per-prediction feature math and the touch population are aligned to the
Strategy-Core v3 runtime contract; any model trained under old Chicago/exact-tick
semantics must be treated as incompatible until retrained or explicitly measured.

This plan implements live/replay CatBoost inference + outcome tracking in
Trade-Lab, driven by a versioned **Strategy Contract** that ships with each
model, with **runtime model hot-swap via API/UI** and an **L1/L0-only** data
floor. It is grounded in the context report and the actual model bundle
`Claude-Quant-Lab/models/NQ_20260405_147t_5m_30m_multiclass-250602-260220-iterations800_depth4`.

---

## 1. Decisions locked (from review)

| # | Decision | Choice |
|---|---|---|
| B | Strategy semantics ownership | **B3 — shared Strategy-Core contract path.** Sessions/touches/levels/windows/thresholds/features/classes are versioned contract concepts. Runtime bars/sessions/levels/zones/touches are now delegated to Strategy-Core v3; Trade-Lab wraps that output for API/WebSocket/observation DTOs and keeps future variant selection behind the contract boundary. |
| Scope | What this plan covers | **Trade-Lab runtime only**, plus `strategy.json` emission in Claude-Quant-Lab (Stage Q). |
| Features | Inference feature set | **Schema-driven**, defaulting to the active model's 6: see §3. |
| Output | What inference drives | **Intelligence + outcome tracking** (MAE-first). No execution, no risk. |
| Model UX | Selection | **Runtime hot-swap via API + UI model picker.** Pick a bundle, activate, rerun. |
| Data | Book depth | **L1/L0 floor** (already structurally true; harden into a tested invariant). |

---

## 2. The model bundle this targets (contract source of truth)

From `…iterations800_depth4/metadata.json` + `evaluation.json:454-515`:

- `training_mode`: `dashboard_utility`
- **Features (6, exact order):** `int_time_beyond_level`, `int_time_within_2pts`,
  `int_absorption_ratio`, `app_large_trade_vol_pct`, `app_avg_trade_size`,
  `app_max_spread` — `metadata.json:2-9`
- **Classes (MultiClass):** `{0:tradeable_reversal, 1:trap_reversal, 2:aggressive_blowthrough}` — `evaluation.json:365-369`
- `bar_type`: `147t` · interaction window `5m` · approach window `30m` — `evaluation.json:507-511`
- TP/SL/trap: `15.0 / 30.0 / 5.0` pts · `level_proximity_pts` `0.50` — `evaluation.json:504-508`
- within-band `2.0` pts (hardcoded) — `dashboard_utility_builder.py:502` · large-trade `size>=10` — `config.py:18`
- `tick_size` `0.25`, `NQ` — `evaluation.json:513-514`
- Confidence gate (recommended): prob ≥ `0.70` → precision ~0.94 — `evaluation.json:399-401`

**Consequence:** 3 of the 6 features are **approach (order-flow)** features over a
30-min pre-touch window. Trade-Lab now retains the required bounded L1/L0
`MarketContextBuffer` of trades and top-of-book quotes on the shared live/replay
runtime path; replay sources that lack quote-bearing context still produce NaN for
spread-dependent approach features and should be surfaced as data-quality risk.

---

## 3. The Strategy Contract (`strategy.json`)

A model bundle = `model.cbm` + `metadata.json` + `evaluation.json` +
**`strategy.json`** + `model.cbm.sha256`. `strategy.json` makes the implicit
strategy semantics explicit and versioned (`contract_version: trade_lab_contract_v1`).

**Parameterised (config values — change with zero code):** feature names+order,
class map, `bar_type`, interaction/approach windows, tp/sl/trap, proximity bands,
confidence gate, session timezone/boundaries.

**Behind a protocol seam (variants are structural):** `TouchRule`,
`SessionScheme`, `LevelScheme`, and each feature as a `FeatureFn`. Implement the
seam + the one variant needed now; a new research variant = an added
implementation, never a rewrite.

Contract sections: `instrument/tick_size/point_value`, `model`, `feature_set`
(names, interaction/approach split, `nan_policy`), `class_map`, `session_scheme`,
`level_scheme`, `touch_rule`, `feature_windows`, `label_policy` (mae_first, tp/sl/
trap, forward bar_type, RTH cutoff), `inference` (eligible class/session,
confidence gate), `data_requirements` (min L1, schemas, top-of-book-only),
`provenance` (dataset hash, catboost params).

---

## 4. Architecture additions

All additions hang off the single shared hot path
`ApplicationRuntime.process_market_event` (`runtime.py:65-105`) so **live and
replay produce identical feature vectors by construction**.

```
TradeEvent / TopOfBookEvent
      │  runtime._process_trade / NEW _process_quote
      ▼
[NEW] MarketContextBuffer ── rolling ≥35-min trades & best bid/ask (L1/L0 only, bounded)
      │
ObservationEngine.start_from_touch ─► ACTIVE obs (scheduled_end = touch + interaction_window)  [exists]
      │  on completion
      ▼
[NEW] FeatureFunctionRegistry ── builds the contract's ordered feature vector
      │
[NEW] ModelRegistry ── loads bundle, validates against strategy.json, fail-closed, HOT-SWAPPABLE
      │
[NEW] InferenceEngine ── predict_proba → class_map → Prediction(diagnostics, is_eligible, contract_id)
      │
[NEW] OutcomeTracker ── MAE-first on contract bar_type closed bars → actual_class, correct
      │
RuntimeUpdate(+predictions, +outcomes, +model_status) ─► Broadcaster ─► ws.v1 ─► Frontend
```

**Boundary invariant** (`docs/architecture.md:303-313`): domain engines never
import inference; inference lives in `services/inference/` behind ports.

**L1/L0 invariant (new, tested):** `MarketContextBuffer` and every `FeatureFn`
are typed to admit only trades + best bid/ask. There is structurally no field
for L2/L3 depth, so no feature can read it. Live = `trades`+`mbp-1`
(`databento.py:49,141`); replay projects `mbp10`→TOB+trades, depth dropped
(`historical_parquet.py:34,71,515-526`).

---

## 5. Staged plan

Each stage is independently shippable, gated on tests + the existing
268-backend / 97-frontend suites not regressing and the 100k-events/s benchmark.

### Stage Q — Claude-Quant-Lab: emit `strategy.json` (bounded; already drafted)
- New `src/alpha_lab/agents/data_infra/ml/strategy_contract.py`:
  `build_strategy_contract(config, selected_features, *, strategy_id)`.
- Wire into `scripts/ml_training_tab.py::save_trained_model` (after `evaluation.json`),
  best-effort (never breaks a save).
- Hand-author `strategy.json` into the target model folder so it can be tested
  immediately, byte-identical to what the code emits.
- **Test:** you re-run a training/export and confirm `strategy.json` appears and
  matches the hand-authored file.
- *Status: `strategy_contract.py` + the `save_trained_model` hook were drafted
  before plan approval; pending your go/revert.*

### Stage 0 — Contract layer in Trade-Lab (no inference yet)
- `domain/contracts/strategy_contract.py`: Pydantic models mirroring `strategy.json`;
  loader + validator; `contract_version` check.
- `models/` dir convention: one subdir per bundle; `ModelRegistry` discovers them;
  checksum file required. Config: `TRADE_LAB_MODELS_PATH`, optional default active id.
- **Tests:** parse the real `strategy.json`; reject bad version / missing fields /
  feature-count mismatch vs `class_map`.

### Stage 1 — `MarketContextBuffer` (L1/L0 retention)
- `domain/market_context.py`: time+count-bounded ring buffer of trades
  `(ts, price_ticks, size, side)` and BBO `(ts, bid_ticks, ask_ticks)`; retains
  ≥ `approach_window + margin` (default 35m). O(1) append, O(window) slice.
- Route `TradeEvent` + new `_process_quote(TopOfBookEvent)` in `runtime._process_trade`.
- **Tests:** time/count eviction; **live==replay buffer equivalence** for the same
  stream; mbp10 replay surfaces no depth; memory bounded at 100k events.

### Stage 2 — `FeatureFunctionRegistry` (parity crux)
- `services/inference/features/`: `name → FeatureFn(buffer, window, level_ctx) → float`.
  Implement the 6 with exact ported semantics + per-feature empty-case rules:
  - interaction (post-touch 5m, mid=(bid+ask)/2): `int_time_beyond_level`,
    `int_time_within_2pts` (|mid−lvl|≤2.0), `int_absorption_ratio` (±0.50 band,
    **0.0 if total vol 0**) — `dashboard_utility_builder.py:492-538`.
  - approach (pre-touch 30m): `app_large_trade_vol_pct` (size≥10 → large/total),
    `app_avg_trade_size`, `app_max_spread`; insufficient data → **NaN**.
- Assemble vector in the contract's order; cross-check against the model's
  `feature_names_` (fixes v1's positional-only weakness — `prediction_engine.py:50-53`).
- Every `FeatureFn` may read **only** the L1/L0 buffer (enforced by signature).
- **Tests (highest priority):** golden-vector fixtures; live==replay equivalence;
  each empty/edge case; ideally one cross-check vector exported from Claude-Quant-Lab.

### Stage 2b — Strategy-Core strategy seam (implemented default)
- Runtime bars, sessions, levels/zones, and touches are delegated to
  `StrategyCoreService` / `strategy_core.runtime.StrategyRuntime`.
- Trade-Lab maps Strategy-Core snapshots/deltas to existing API/WebSocket DTOs;
  it does not run a second authoritative Chicago/exact-touch engine.
- Future contract-selected variants remain structural additions to Strategy-Core
  or its adapter boundary, not rewrites of Trade-Lab runtime plumbing.
- **Tests:** acceptance coverage compares direct Strategy-Core touch output with
  Trade-Lab DTO/observation output and verifies old local engines are not used as
  the authoritative runtime path.

### Stage 3 — `ModelRegistry` + `InferenceEngine` (with hot-swap)
- `services/inference/model_registry.py`: discover bundles; **load + fail-closed
  validation** (`model.feature_names_ == contract.feature_set`, class count,
  checksum, tick_size); reject binary/wrong-feature models (the check v1 lacks —
  `model_manager.py:117-124`). **Atomic hot-swap** under a lock; stamp active
  `contract_id`.
- `services/inference/inference_engine.py`: on observation completion → registry →
  features → `predict_proba` → `class_map` → `Prediction` (predicted_class,
  probabilities, feature vector, level, direction, `is_eligible` = eligible_class
  AND eligible_session AND prob≥gate, model_id, contract_id, touch_id). No
  execution/risk.
- **Tests:** deterministic prediction; registry rejection matrix; graceful no-model
  (market data still served); hot-swap clears prediction state but not market data;
  eligibility gate.

### Stage 4 — `OutcomeTracker` (MAE-first, contract bar_type)
- `services/inference/outcome_tracker.py`: per open prediction, consume contract
  `bar_type` (147t) closed bars from the Strategy-Core-backed runtime; running
  max MFE/MAE vs level by direction; **MAE first** (`mae≥sl → trap if
  mfe≥trap_min else blowthrough`; else `mfe≥tp → tradeable`); RTH-close cutoff;
  session-end forced resolution;
  `actual_class`, `correct = predicted==actual`. Mirrors
  `dashboard_utility_labeling.py:44-108`; explicitly **not** v1's MFE-first tracker.
- **Tests:** MAE-first ladder incl. same-bar SL+TP→loss; trap/blowthrough split;
  forced resolution; correctness flag.

### Stage 5 — DTOs / WebSocket / REST (incl. hot-swap API)
- Extend `RuntimeUpdate`/`RuntimeSnapshot` with `predictions`, `outcomes`,
  `model_status` (`runtime.py:30-62`). New ws.v1 types `prediction.created`,
  `prediction.resolved`, `model.status` (`dto.py:21-32`, `broadcaster.py:68-141`).
  Never leak model paths/secrets.
- **Model hot-swap REST** (operator-token gated, like live control —
  `api/app.py:69-110`):
  - `GET /api/v1/models` — list bundles (id, strategy_id, training_mode, feature
    count, class map, validation status). No paths.
  - `GET /api/v1/models/active` — active bundle + status.
  - `POST /api/v1/models/activate {model_id}` — validate + atomic swap; clears
    prediction/outcome stores; emits `model.status`. 409 if invalid/locked.
  - `POST /api/v1/models/deactivate` — unload; runtime keeps serving market data.
- Add runtime `session` + `trading_day` to `/api/v1/status` (fixes the frontend
  `"unavailable"`/null placeholders — `IntelligencePanel.tsx:14-18`, `normalize.ts:18`).
- **Tests:** new envelopes + monotonic sequence; no secret/path leakage;
  activate/deactivate happy + reject paths; version-compat for old clients.

### Stage 6 — Frontend (model picker + intelligence)
- `ModelPanel`: dropdown from `GET /api/v1/models`, Activate (operator-gated),
  shows active bundle + contract summary (feature set, classes, thresholds) +
  validation status. "Rerun" = activate model → start replay.
- `predictionStore`; Predictions panel (class, 3 probabilities, eligibility, then
  resolved outcome + correctness); prediction/outcome chart markers on the touch
  bar (overlay layer already anticipates "ML annotations" — `overlayManager.ts:11-13`).
- Wire real `session`/`trading_day` into `TopStatusBar`/`IntelligencePanel`.
- **Tests:** model list/activate flow; store upsert/cap; panel render; markers; no
  API-key input regressions.

---

## 6. Validation commands
- backend/ : `pytest`, `pytest --run-benchmark` (≥100k events/s), `ruff check .`
- frontend/ : `npm test`, `npm run build`, `npm run lint`
- Gate each stage: counts ≥ 268 backend / 97 frontend + new tests green.

---

## 7. Risks
- **Strategy/runtime contract skew:** a prediction is meaningful only under the
  Strategy-Core/runtime semantics used to create its touch population. Stamp every
  `Prediction`/`Outcome` with `contract_id`; hot-swap clears prediction state;
  never mix bundles in one session. Bundles trained under old Chicago/exact-tick
  Trade-Lab semantics must be retrained or explicitly quarantined.
- **Approach features need clean BBO:** trades-only replay → `app_max_spread` NaN
  every prediction. Catalog should prefer quote-bearing sources; warn otherwise.
- **Touch-population drift:** the runtime now uses Strategy-Core ET/bar-range
  zone touches. Any saved evaluation calibrated on old Trade-Lab exact-level
  touches is not directly comparable; verify/retrain under the v3 contract before
  trusting live precision.
- **Empty-case fidelity:** the 6 features have *different* missing-value rules
  (absorption→0.0, approach→NaN). Golden vectors are the guardrail.
- **Never consume research caches:** `ml_features.parquet` / `ohlcv_1m*.parquet`
  in the data dirs are off-limits; Trade-Lab computes its own features.
- **mbp-3 naming:** current `mbp10` data is handled; a literal `mbp-3` source is
  rejected by `replay_catalog.py:26` until the schema list is extended.

---

## 8. Remaining validation tasks
1. Retrain or verify model bundles against the Strategy-Core v3 runtime contract.
2. Add golden-vector fixtures exported from Quant-Lab for the active model bundle.
3. Run manual live Databento validation during active market hours.
4. Verify quote-bearing replay sources for approach-feature coverage and surface
   warnings when only trades are available.
5. Keep execution/risk out of this vertical until explicitly approved.
