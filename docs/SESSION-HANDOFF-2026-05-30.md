# Trade-Lab Session Hand-Off — 2026-05-30

Audience: historical hand-off. **Superseded 2026-06-08:** Strategy-Core v3 now owns
Trade-Lab runtime bars, ET sessions, levels/zones, and bar-range zone touches; the
old Stage 2b deferred / Chicago exact-touch warning below has been resolved by the
Strategy-Core runtime migration.

Branch: `trade-lab-v2`. Project root: `C:\Users\gonza\Documents\Trade-Lab`.

---

## 1. TL;DR

- **Shipped:** A contract-driven CatBoost ML-inference subsystem layered onto the existing market-data runtime in Trade-Lab — strategy-contract trust boundary, fail-closed model registry, L1/L0 market-context buffer, six contract feature functions, an inference engine, an MAE-first outcome tracker, live warm-up seeding, plus REST/WebSocket hot-swap wiring and a full frontend (model picker + predictions panel + on-chart markers). The plan is **IMPLEMENTED through Stage 6**.
- **Tests green:** Backend `415 passed, 1 skipped` (5.19s); benchmark `2 passed` (~137k events/sec median). Frontend `137 tests passed` (16 files), production build clean (`tsc --noEmit` + `vite build`).
- **Inference is inert by default:** no model is active until an operator activates one via the API/UI — the runtime serves market data unchanged until then.
- **Strategy seam status:** The prior Stage 2b gap is now resolved outside this
  historical session: Trade-Lab delegates bars, ET sessions, levels/zones, and
  bar-range zone touches to Strategy-Core v3 and maps that output to DTOs.
- **Next:** Retrain/verify model bundles against the Strategy-Core v3 contract,
  then validate live precision against a quote-bearing source.

---

## 2. Objective & Scope of the Session

Reconstruct cross-project context (Trade-Lab vs Trade-Dashboard v1 vs Claude-Quant-Lab), then implement **live + replay CatBoost inference + outcome tracking** in Trade-Lab — contract-driven, with runtime model hot-swap.

Scope was deliberately bounded to the **Trade-Lab runtime only**. Claude-Quant-Lab is the model/contract emission side (read-only this session). Trade-Dashboard v1 is reference context only (its MFE-first labeling was explicitly *not* copied).

---

## 3. Key Decisions (and Why)

| Decision | What it means | Why |
|---|---|---|
| **B3 — contract-driven** | Strategy semantics ship as a versioned `strategy.json` contract bundled with the model. Claude-Quant-Lab emits it; Trade-Lab parses it with a strict Pydantic loader (`extra='forbid'`, frozen) that rejects drift loudly. | The runtime must not hardcode strategy semantics. The contract is the single source of truth; a mismatched/contract-drifted model fails closed instead of silently mispredicting. Contract version: `trade_lab_contract_v1`. |
| **L1/L0 data floor** | Live consumes trades + `mbp-1` (best bid/ask). Replay projects `mbp10` → top-of-book + trades and **drops depth**. The buffer and every feature fn are *typed* so they structurally cannot hold L2/L3 depth. | Keeps live and replay on one feature path and one realistic data floor. The no-depth invariant is enforced by types and tested. |
| **MAE-first outcome tracking** | Forward outcomes resolve **MAE-first** on 147t bars, matching the training labeler (`dashboard_utility_labeling` ladder). | Must match how the model was *trained* (NOT v1's MFE-first). Outcome labels have to be apples-to-apples with the offline ground truth or the precision numbers are meaningless. |
| **Hot-swap** | Pick/activate a model via API + UI, then run a replay. No model active by default. Hot-swap clears prediction/outcome state and stamps the active `contract_id`. | A prediction is only meaningful under its own contract; never mix bundles in one session. Operator-gated, atomic under a lock. |
| **Production model target** | `NQ_20260405_147t_5m_30m_multiclass-250602-260220-iterations800_depth4` | The real dashboard-utility model: 6 features, 3-class MultiClass, bar_type 147t, tp/sl/trap = 15/30/5 pts, confidence gate 0.70. |

---

## 4. What Was Implemented — Component Map

### 4a. Backend (`backend/src/trade_lab`, branch `trade-lab-v2`)

**New files:**

| File | Purpose |
|---|---|
| `domain/contracts/strategy_contract.py` | Pydantic strategy.json models + strict validating loader (rejects drift); `CONTRACT_VERSION = trade_lab_contract_v1`. |
| `domain/contracts/__init__.py` | Exports `ContractError`, `StrategyContract`, `load_strategy_contract`, `CONTRACT_VERSION`. |
| `domain/market_context.py` | Bounded L1/L0 ring buffer (`MarketContextBuffer` + `BufferedTrade`/`BufferedQuote`); cannot hold depth. Default retention 45 min, max 200k elements. Runtime-owned so live + replay share context. |
| `domain/outcomes.py` | Frozen `Outcome` + `ResolutionType` enum (tp_hit/sl_hit/session_end/no_resolution) for MAE-first forward labeling. |
| `services/model_registry.py` | Fail-closed discovery of local model bundles (`model.cbm` + `metadata.json` + `strategy.json`). Opaque allowlisted model ids, never leaks fs paths, never opens `.cbm` as bytes. Defines `ModelBundle`/`ModelRegistry`/`ActiveModel`, `ModelNotFoundError`/`ModelValidationError`, `is_safe_model_id`. |
| `services/inference/inference_engine.py` | `InferenceEngine`: on observation completion builds level context + contract-ordered feature vector, queries the active CatBoost model, returns a frozen path-free `Prediction` stamped with `contract_id`/`model_id`. Returns `None` when no model active. |
| `services/inference/outcome_tracker.py` | `OutcomeTracker`: consumes closed forward-bar bars (147t) after a touch, tracks running MFE/MAE, resolves **MAE-first**, caps scan at RTH close (16:15 ET) → session_end. |
| `services/inference/features/feature_functions.py` | The six contract features as a name→FeatureFn registry (`DEFAULT_FEATURE_REGISTRY`, `build_feature_vector`); reads only L1/L0; per-feature empty/edge rules; emits in contract feature order. |
| `services/inference/features/__init__.py` | Feature package exports. |
| `services/inference/__init__.py` | Inference package init. |
| `adapters/databento_historical.py` | Databento Historical API source for live warm-up seeding (L0/L1 front-month trades → pandas DataFrame); import-safe (client built lazily, `frame_fetcher` injectable for tests). |
| `services/seed.py` | `HistoricalSeedService`: legacy vectorized warm-up display-bar builder for last N sessions; current authoritative live/replay bars, sessions, levels, and touches come from Strategy-Core. |

**Edited files (inference-relevant):**

| File | Change |
|---|---|
| `pyproject.toml` | Adds `catboost>=1.2`. |
| `config.py` | Adds `models_path` (`TRADE_LAB_MODELS_PATH`, request paths never accepted), `market_context_retention_minutes` (45–240), `seed_enabled`/`seed_lookback_days`/`seed_max_bars_per_timeframe`. |
| `services/runtime.py` (+286) | Wires `MarketContextBuffer` ownership, optional `InferenceEngine` seam (completed observations → Predictions on RuntimeUpdate), `OutcomeTracker`, `ModelStatus` snapshot, prediction ring; predictions/outcomes attached to RuntimeUpdate/RuntimeSnapshot. |
| `api/dto.py` (+153) | `PredictionDTO`/`OutcomeDTO`/model-status DTOs + ws event names `prediction.created`, `prediction.resolved`, `model.status`; mappers. |
| `api/app.py` (+105) | Constructs `ModelRegistry(settings.models_path)` + `InferenceEngine`; `ActivateModelRequest` + model list/activate REST endpoints (no model active by default). |
| `services/broadcaster.py` (+44) | Broadcasts prediction/model-status envelopes. |
| `services/replay.py` (+186) | Feeds replay events into market context + inference path. |
| `services/live.py` (+54) | Live warm-up seeding integration. |

**New tests:** `tests/fixtures/strategy.json`, `tests/test_strategy_contract.py`, `tests/test_market_context.py`, `tests/test_outcome_tracker.py`, `tests/test_feature_functions.py`, `tests/test_inference_engine.py` (trains a tiny in-fixture CatBoost model), `tests/test_inference_api.py` (asserts no fs-path/secret leaks), `tests/test_seed.py`.

### 4b. Frontend (`frontend/src`)

**New files:**

| File | Purpose |
|---|---|
| `components/ModelPanel.tsx` | Model hot-swap UI: lists discovered bundles, shows active-model metadata, activate/deactivate (operator-gated server-side; no secrets entered client-side). |
| `components/ModelPanel.test.tsx` | 6 tests (bundle listing, activate/deactivate, offline-disabled). |
| `components/IntelligencePanel.test.tsx` | 5 tests for predictions/outcomes rendering. |
| `components/TopStatusBar.test.tsx` | 2 tests for the session pill. |

**Edited files:**

| File | Change |
|---|---|
| `components/IntelligencePanel.tsx` | Added a Predictions section (`PredictionRow`: predicted class, eligibility/direction badges, probability bars, level/session meta, resolved outcome MFE/MAE) + live session row. |
| `components/ChartWorkspace.tsx` | Subscribes to `usePredictions()`, feeds predictions + bars into `combineMarkers` for on-chart prediction/outcome markers. |
| `components/TopStatusBar.tsx` | Session pill reads `runtime.session` (was hardcoded `"unavailable"`). |
| `chart/viewModels.ts` | `normalizePredictionMarkers`/`normalizeOutcomeMarkers` + chart-time resolver + class-color map; `combineMarkers()` folds prediction/outcome markers onto the synthetic chart-time axis. |
| `state/stores.ts` | New `predictionStore` (predictions/outcomes/modelStatus/bundles) with bounded newest-first add helpers (`addPrediction`/`addOutcome` auto-annotate by `prediction_id`), `setModelStatus`/`setModelBundles`/`clearPredictions`, hooks `usePredictions`/`useOutcomes`/`useModelStatus`/`useBundles`; added `runtime.session`. |
| `realtime/types.ts` | New WS message types (prediction.created/resolved/model.status) + PredictionDTO/OutcomeDTO/ModelStatusDTO + snapshot fields (predictions, outcomes, model_status, session, trading_day). |
| `realtime/client.ts` | Handles the three new WS message types, seeds inference state from snapshot, clears predictions on runtime reset. |
| `api/client.ts` | REST methods `listModels`/`activeModel`/`activateModel`/`deactivateModel` against `/api/v1/models*`. |
| `api/types.ts` | `ModelBundleDTO`/`ModelsResponseDTO` + session/trading_day on `RuntimeStatusDTO`. |
| `domain/models.ts` | `Prediction`/`Outcome`/`ModelStatus`/`ModelBundle` domain types + session on `RuntimeSummary`. |
| `domain/normalize.ts` | `normalizePrediction/Outcome/ModelStatus/ModelBundle` — **drops raw feature vectors** (`feature_values` never enters the domain model), sanitizes probabilities/class maps; maps session/trading_day. |
| `App.tsx` | Mounts `ModelPanel` in a new control-row. |
| `styles.css` | `.model-*`, `.intel-prediction`/`.intel-badge`/`.intel-probs`/`.intel-outcome` styles + responsive rules. |

> Note: `frontend/src/chart/overlayManager.ts` appears in some task file lists but is **UNMODIFIED** and contains no inference code (generic level/marker renderer). It is *not* part of this work. Several pre-existing test files were also extended with inference coverage (`chart/viewModels.test.ts`, `state/stores.test.ts`, `api/client.test.ts`, `realtime/client.test.ts`, `domain/normalize.test.ts`, `components/ChartWorkspace.test.tsx`, `App.test.tsx`).

### 4c. Claude-Quant-Lab (emission side — read-only this session)

| File | Purpose |
|---|---|
| `src/alpha_lab/agents/data_infra/ml/strategy_contract.py` | Emits the versioned `strategy.json` (`trade_lab_contract_v1`) via `build_strategy_contract()`. Only `training_mode="dashboard_utility"` emits a full contract; other modes emit a minimal record. |
| `scripts/ml_training_tab.py` | `save_trained_model` (lines ~602–625) calls `build_strategy_contract` and writes `strategy.json` **best-effort** after `evaluation.json` (try/except + `logger.warning`, so emission never breaks a model save). |

**Generated contract** (the real one): `models/NQ_20260405_147t_5m_30m_multiclass-250602-260220-iterations800_depth4/strategy.json`. 18 top-level keys; 6 contractual features (`order_is_contractual=true`); 3-class `class_map` (0=tradeable_reversal, 1=trap_reversal, 2=aggressive_blowthrough); `nan_policy=model_native`.

Key thresholds from the contract:
- **inference:** eligible_class=tradeable_reversal, eligible_session=ny_rth, confidence_gate=0.7
- **feature_windows:** interaction 5m, approach 30m, within_band 2.0pts, level_proximity 0.5pts, large_trade_threshold 10, mid_price_source=top_of_book
- **touch_rule:** type=bar_intersect, bar_type=147t, zone_proximity 3.0pts, scope=first_touch_per_zone_per_day, direction {low→long, high→short}
- **label_policy:** resolution=mae_first, tp=15.0, sl=30.0, trap_mfe_min=5.0, forward_bar_type=147t, forward_cutoff=16:15 US/Eastern, no_resolution_dropped=true
- **session_scheme:** US/Eastern, trading_day_boundary 18:00 (asia 18:00–01:00, london 01:00–08:00, ny_rth 09:30–16:15)
- **data_requirements:** min_book_level L1; live_schemas [trades, mbp-1]; replay_schemas [trades, mbp-1, mbp-10]; depth_usage top_of_book_only

The six features (interaction = post-touch 5m, approach = pre-touch 30m):
- Interaction: `int_time_beyond_level`, `int_time_within_2pts`, `int_absorption_ratio`
- Approach: `app_large_trade_vol_pct`, `app_avg_trade_size`, `app_max_spread`

---

## 5. Current Verification State

### Backend

```
$ python -m pytest -q
415 passed, 1 skipped in 5.19s

$ python -m pytest --run-benchmark tests/test_benchmark_smoke.py -q
2 passed in 3.97s
# with -s (throughput is captured under plain -q):
synthetic candle+level throughput: best 138,734 events/sec, median 136,598 events/sec
over 100,000 events x 5 runs (138,734 / 138,267 / 136,598 / 131,393 / 133,821)
```

The 1 skip is pre-existing and unrelated to inference. Throughput varies run-to-run (~125k–139k observed across two runs). `catboost>=1.2` must be installed — the suite trains a tiny synthetic CatBoost model in-fixture for `test_inference_engine`/`test_inference_api`.

### Frontend

```
$ npm test -- --run
Test Files  16 passed (16)
     Tests  137 passed (137)
  Duration  2.33s

$ npm run build      # tsc --noEmit && vite build
✓ 53 modules transformed.
dist/assets/index-*.css   10.11 kB │ gzip:  2.59 kB
dist/assets/index-*.js   351.81 kB │ gzip: 111.18 kB
✓ built in 868ms
```

No TypeScript errors (`tsc --noEmit` passed before vite build). Single bundle ~352 kB (gzip ~111 kB) — sizeable but no warning emitted; consider code-splitting later if it grows.

---

## 6. How to Run It End-to-End

### Environment variables

Config uses `env_prefix=TRADE_LAB_`, loaded from `backend/.env` (do not commit secrets). Relevant vars:

| Env var | Maps to | Meaning |
|---|---|---|
| `TRADE_LAB_MODELS_PATH` | `Settings.models_path` (`config.py:35`) | Root dir holding model bundles — one subdir per bundle with `model.cbm` + `metadata.json` + `strategy.json` (+ checksum). Discovered by `ModelRegistry`. Request-provided paths are never accepted. |
| `TRADE_LAB_DATA_PATH` | `Settings.data_path` (`config.py:30`) | Replay data root. |

Point `TRADE_LAB_MODELS_PATH` at a directory containing the production bundle `NQ_20260405_147t_5m_30m_multiclass-250602-260220-iterations800_depth4` (must include `model.cbm`, `metadata.json`, `strategy.json`). Point `TRADE_LAB_DATA_PATH` at `C:\Users\gonza\Documents\Claude-Quant-Lab\data\databento\NQ` (one subdir per trading day; each holds `mbp10.parquet` — trades are projected from it).

Related defaults: `market_context_retention_minutes=45` (covers approach 30 + interaction 5 + 10 margin), `tick_timeframes=(147, 987, 2000)`, `observation_duration_seconds=300`.

### Steps

1. **Backend:** ensure `catboost>=1.2` installed (`pip install -e .` in `backend/`), set the two env vars in `backend/.env`, start the API app (`api/app.py`).
2. **Frontend:** `npm install` then `npm run dev` in `frontend/`.
3. **Activate a model:** in the UI, use the **ModelPanel** (control-row) to list discovered bundles and activate the NQ production bundle. (Equivalent REST: `POST /api/v1/models/activate`.) **No model is active by default** — until you activate, the runtime serves market data unchanged and emits no predictions.
4. **Start a replay** against the NQ data. Expected behavior:
   - On each completed observation the engine builds a contract-ordered feature vector from the L1/L0 buffer and emits a `prediction.created` envelope (predicted class + probabilities, eligibility/direction badges) — rendered in the IntelligencePanel and as on-chart markers.
   - As forward 147t bars close, the OutcomeTracker resolves each prediction MAE-first → `prediction.resolved` (tp_hit/sl_hit/session_end/no_resolution) with MFE/MAE.
   - The session pill reflects `runtime.session`.

> Live warm-up: with `seed_enabled` on, `HistoricalSeedService` seeds warm-up tick bars from the Databento Historical API for the last N sessions so live starts with populated context.

---

## 7. Known Gaps / Deferred / Gotchas

**Most important first.**

1. **Historical Stage 2b gap resolved after this handoff.** Trade-Lab now delegates
   runtime bars, ET sessions, levels/zones, and bar-range first touches to
   Strategy-Core v3. The remaining risk is model-bundle compatibility: any bundle
   trained/evaluated under the old exact-touch/Chicago semantics must be retrained
   or quarantined before live precision is trusted.

2. **Approach features need quotes.** 3 of 6 features (`app_large_trade_vol_pct`, `app_avg_trade_size`, `app_max_spread`) are order-flow over the 30-min pre-touch window and require clean top-of-book BBO. A trades-only replay source makes `app_max_spread` (and approach features) **NaN on every prediction**. The catalog should prefer quote-bearing sources and warn otherwise.

3. **Empty-case fidelity.** The six features have *different* missing-value rules (`int_absorption_ratio`→0.0 when total band vol is 0; approach features→NaN on insufficient data; interaction times→0.0). Golden-vector fixtures in `tests/test_feature_functions.py` are the only guardrail (`nan_policy=model_native`).

4. **Checksum / fail-closed validation.** Each bundle should carry a `model.cbm.sha256` checksum file. `ModelRegistry` must load + fail-closed validate (model `feature_names_` == contract feature order, class count vs class_map, checksum, tick_size) and reject binary/wrong-feature models. Hot-swap is atomic under a lock and stamps the active `contract_id`. Verify the production bundle actually ships a checksum.

5. **Stage Q emission is best-effort.** `save_trained_model` wraps `build_strategy_contract` in try/except + `logger.warning`, so a contract-emission bug silently produces **no** `strategy.json` rather than failing the model save. Watch for silently-missing contracts.

6. **Live Databento validation NOT done end-to-end.** live==replay buffer/feature equivalence is asserted by construction (single shared `process_market_event` hot path) and by tests, but live precision against the research touch rule is explicitly **not** yet validated end-to-end.

7. **Strategy-version skew.** A prediction is meaningful only under its own contract. Every Prediction/Outcome is stamped with `contract_id`; hot-swap clears prediction/outcome state; never mix bundles in one session.

8. **Replay data reality.** `C:\Users\gonza\Documents\Claude-Quant-Lab\data\databento\NQ` — each day physically holds `mbp10.parquet` + research caches (`ml_features*.parquet`, `ohlcv_1m*.parquet`); there is **no** standalone `trades.parquet` — trades are projected from `mbp10` by `historical_parquet`. **Never consume research caches** — Trade-Lab computes its own features from mbp10-projected TOB+trades.

9. **`mbp-3` naming.** Current `mbp10` data is handled, but a literal `mbp-3` source is rejected by `replay_catalog.py:26` until the schema list is extended.

10. **L1/L0 floor invariant.** `MarketContextBuffer` and every FeatureFn are typed to admit only trades + best bid/ask (no field for depth). mbp10 replay must surface no depth — tested invariant.

11. **Benchmark output.** events/sec is **not** printed under plain `-q` (pytest captures stdout); pass `-s` to see it.

---

## 8. Recommended Next Steps (Prioritized)

1. **Retrain or verify model bundles against Strategy-Core v3.** The runtime touch
   population now matches Strategy-Core ET/bar-range zone semantics; bundles trained
   under old exact-touch/Chicago behavior are not directly comparable.
2. **Add golden-vector / bundle-parity fixtures** exported from Quant-Lab for the
   active model bundle.
3. **Prefer quote-bearing replay sources + warn.** Make the replay catalog prefer sources that carry top-of-book BBO; warn (or refuse) when a trades-only source would NaN the approach features.
4. **Verify bundle checksum + fail-closed path.** Confirm the production bundle ships `model.cbm.sha256` and that `ModelRegistry` rejects a tampered/wrong-feature/wrong-tick model. Add a guard/alert for silently-missing `strategy.json` (Stage Q best-effort emission).
5. **Run a manual live Databento session** to validate live==replay feature equivalence and measure live precision under the Strategy-Core v3 contract.
6. **(Optional) Frontend code-splitting** if the ~352 kB single bundle becomes a concern.

---

## 9. References

| Item | Path |
|---|---|
| Staged plan + status (Strategy-Core runtime seam implemented) | `C:\Users\gonza\Documents\Trade-Lab\docs\inference-integration-plan.md` |
| Memory: inference integration notes | `trade-lab-inference-integration.md` (auto-memory) |
| Memory: Databento NQ store layout + multi-instrument contamination gotcha | `databento-nq-store.md` (auto-memory) |
| Memory: tick-bar behavior + live seeding gotchas | `trade-lab-candle-fix.md` (auto-memory) |
| Production model bundle | `C:\Users\gonza\Documents\Claude-Quant-Lab\models\NQ_20260405_147t_5m_30m_multiclass-250602-260220-iterations800_depth4` |
| Generated contract (`strategy.json`) | `...\NQ_20260405_147t_5m_30m_multiclass-250602-260220-iterations800_depth4\strategy.json` |
| Contract emitter | `C:\Users\gonza\Documents\Claude-Quant-Lab\src\alpha_lab\agents\data_infra\ml\strategy_contract.py` |
| Replay data root | `C:\Users\gonza\Documents\Claude-Quant-Lab\data\databento\NQ` |
| Backend config (env vars) | `C:\Users\gonza\Documents\Trade-Lab\backend\src\trade_lab\config.py` |
