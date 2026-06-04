# Strategy-Core Architecture Spec
### A shared engine for zero-drift parity between Claude-Quant-Lab (research) and Trade-Lab (runtime)

**Status:** draft v1  
**Audience:** any engineer or agent implementing this with no prior context. This document is self-contained.  
**Repos:** `Claude-Quant-Lab` (research / model training, batch over parquet) · `Trade-Lab` (live + replay inference, streaming)

**new repo**: "C:\Users\gonza\Documents\Strategy-core"
---

## 1. Purpose

The research lab is a *strategy factory*: you configure a strategy there, it trains an ML model. Whatever strategy produced a given model, Trade-Lab must then execute **exactly the same** — zero drift. Today the two repos implement the same strategy logic (bar construction, levels/zones, touch detection, session classification, features, label/outcome resolution) **independently**, and they have diverged. The contract (`strategy.json`) was meant to be the bridge, but it currently only describes about half the strategy and is wrong in at least one place.

This spec defines a **shared, versioned engine** plus a **complete config contract** so that any strategy configured in research is executed identically in Trade-Lab, while research stays free to vary the strategy. The goal is flexibility **and** zero drift at the same time.

---

## 2. Decisions (ratified)

| # | Decision | Choice | Why |
|---|---|---|---|
| 1 | Source of truth | The research **engine**, read as a *parameterized engine* — not a frozen strategy. The training path (`dashboard_utility_builder` + `dashboard_utility_labeling`) is the reference behavior. | It is literally the code that produced the model's labels and features. The model learned that world; everything else must conform to it. |
| 2 | Sync mechanism | **Hybrid**: one shared engine library (a single implementation both repos import) **plus** a complete config contract bound to each model. | Contract-as-spec alone drifts (two interpreters of the same words diverge — today's bug). Shared-lib with fixed params kills flexibility. Only the hybrid is flexible *and* drift-proof. |
| 3 | Packaging | A **separate, versioned package** (`strategy-core`, name TBD), lean dependencies, editable install during dev. | A real version boundary is what makes "a model binds to an engine version, Trade-Lab refuses what it can't match" clean. Respects the two repos' different app lifecycles (service+frontend vs ML lab). |
| 4 | Performance | **Two-layer split**: vectorize the candle layer (hot path); single-source the decision layer (cold path). | Speed where the data volume is, parity where the meaning is. They barely overlap, so neither taxes the other. |
| 5 | Flexibility model | Parameter changes are **config-only**; structural changes are **one engine change + a version bump**. | Configuring known parameters should cost zero code and propagate instantly. Genuinely new mechanisms can't be zero-code, but should be one shared change, not two diverging ones. |
| 6 | Versioning | Each model binds to a **config + engine version**; Trade-Lab **fail-closes** on a version it can't match. | Builds on Trade-Lab's existing fail-closed model registry and `contract_id` stamping. |

---

## 3. Core principles

- **Engine, not strategy.** The canonical artifact is not one strategy — it is the engine that turns a config into behavior, plus the config bound to each model. Research varies the config freely; the engine and the config-binding are what never drift.
- **Vectorize where the data is; single-source where the meaning is.** Candle building collapses millions of ticks/day into thousands of bars — high volume, mechanical, so it gets a fast vectorized path (plus a streaming path), proven identical by a parity test. Strategy decisions run on bars and touches — thousands of bars and a handful of touches/day, 3–5 orders of magnitude less data — so they can be a single scalar implementation at zero meaningful perf cost.
- **Two kinds of flexibility.** *Parameter* flexibility (bar size, zone width, sessions, tp/sl, features from a known menu) is config-only and instant. *Structural* flexibility (a genuinely new touch mechanism, feature family, or label scheme) requires extending the engine and bumping the engine version — rare, and drift-proof because it's one change in one place.
- **Fail-closed binding.** A prediction is only meaningful under its own engine version + config. Trade-Lab must refuse to run a model whose engine version it does not have.
- **Preserve, don't rewrite.** This is a *consolidation*, not a redesign. The engine must reproduce the current training behavior exactly; "correct" is defined as "what the training path does today."

---

## 4. Current state (what we're fixing)

Grounded in the two audits already run against both repos.

**The contract is half-real.** Config-driven scalars (`tp/sl/trap`, `bar_type`, feature windows, `level_proximity`, the `16:15` cutoff, `class_map`) are genuinely single-sourced from the same `MLPipelineConfig` the trainer used. But session scheme, touch geometry, `zone_proximity` (3.0), `within_band` (2.0), `large_trade_threshold` (10), timezone, and the trading-day boundary are **restated literals in the emitter** — independent copies that can drift. The emitter's own docstring admits they are "restated here rather than imported."

**One active mismatch.** The contract declares `mid_price_source = top_of_book`, but the training code uses the **trade price** (`ticks["price"]`, `dashboard_utility_builder.py:488`). If Trade-Lab faithfully implements "top-of-book mid," it diverges from the model on day one. (See §7 — open.)

**"Touch" is defined four different ways across the system, and they disagree:**

| Implementation | Mechanism | Scope | Timezone |
|---|---|---|---|
| Research **training** (canonical) | bar-intersect on **zones** (bar low/high straddles zone mean) | first touch per **zone per day** | **ET** |
| Research experiment | bar-intersect + `available_from` gating | per zone | ET |
| Research live dashboard | trade-price **crossing** | RTH/non-RTH spending | ET |
| **Trade-Lab runtime** | **exact tick** on individual levels (`trade.price_ticks == level_price_ticks`) | first touch per **level per session** | **Chicago** |

Trade-Lab's touch rule is not a drifted copy of the trained one — it is a different concept on a different primitive. **Trade-Lab has no zone object at all**; zones exist only in the contract it currently ignores.

**Sessions diverge concretely.** Research is ET; Trade-Lab is `America/Chicago`, and the windows don't line up — Trade-Lab's "NY" runs roughly 09:00–17:00 ET against research's `ny_rth` of 09:30–16:15 ET. The `eligible_session = ny_rth` gate is firing on a different clock.

**Research is not internally single-sourced either.** Touch, session, level, feature, and label logic each exist in 3+ parallel implementations within `Claude-Quant-Lab` (training / experiment / live-dashboard / data-infra).

**Bright spots (already aligned — protect these):**
- **Outcome resolution is MAE-first on both** the training path and Trade-Lab, and the logic looks aligned (check `max_mae >= sl` before `max_mfe >= tp`). The MFE-first version is the research *live dashboard* only — an off-model lineage, not the serving path.
- **Tick-bar close rule agrees**: close at trade count == N on both sides.
- **Trade-Lab already has a vectorized batch tick-bar builder** (`build_tick_bars_from_frame`, ~100× faster than the streaming `CandleEngine`) that is **parity-tested** to produce identical bars to the streaming path. This is the key reusable asset.

---

## 5. Target architecture

### 5.1 Layering

```
            Claude-Quant-Lab (research)            Trade-Lab (runtime)
            ----------------------------           --------------------------
IO layer    parquet / DuckDB  (per-repo)           Databento / replay scan (per-repo)
                    |                                       |
            -----------------------  strategy-core (SHARED)  -----------------------
CANDLE      vectorized batch builder  <--- parity test --->  streaming builder
layer       (numpy)                                          (event-at-a-time)
                    |                                       |
DECISION    zones -> touch -> features -> label/outcome  (single scalar impl, config-driven)
layer
                    |                                       |
CONFIG      contract schema (Pydantic) + engine_version  (single source for format)
```

- **IO layer — stays per-repo.** Research keeps DuckDB/parquet; Trade-Lab keeps Databento/streaming + replay. Nobody changes how they *read* data.
- **Candle layer — shared, two builders, parity-tested.** A fast vectorized batch builder and a streaming builder, both in the package, locked identical by one parity test. Promote Trade-Lab's existing `build_tick_bars_from_frame` (batch) + `CandleEngine` (streaming) pair.
- **Decision layer — shared, single scalar implementation, parameterized by the contract.** Pure functions for: zone construction, touch test, first-touch scope tracking, session classification, the feature formulas, and label/outcome resolution. Operates on neutral value types and plain numbers; each repo adapts its own data into it.
- **Config — shared schema.** The contract Pydantic model lives in the package (today it is duplicated: research has the emitter, Trade-Lab has the loader). Both import it, so the config *format* can't drift either. Carries `engine_version`.

### 5.2 What the shared package owns

1. **Candle builders** — `build_bars_batch(...)` (vectorized, numpy) and a streaming `CandleEngine`, plus the parity test between them.
2. **Decision functions** (scalar, pure, config-driven): `build_zones`, `is_touch`, first-touch tracker, `classify_session`, the feature formulas, `resolve_outcome`.
3. **Neutral value types** — minimal representations of trade, quote, bar, level, zone, touch (plain dataclasses / numpy-friendly), so the engine never imports pandas or Trade-Lab domain objects.
4. **Contract schema + loader/validator** (Pydantic, strict, frozen) and `ENGINE_VERSION`.
5. **All strategy constants** — `3.0` zone proximity, `2.0` within-band, `10` large-trade, the session windows, etc. live here once. Both the engine and the contract emitter read them from here.

**Dependencies:** numpy + stdlib + pydantic only. No pandas, no duckdb, no fastapi. (This keeps it fast to import and fast on the hot path, and trivially installable in both repos.)

### 5.3 What each repo keeps

- **Research:** parquet/DuckDB IO; the training orchestration (UI, dataset assembly, walk-forward, CatBoost training, model save); adapters that turn DataFrames into engine inputs; the contract **emitter** (now emitting from engine constants).
- **Trade-Lab:** Databento live feed + replay scan; the FastAPI app, broadcaster, frontend; runtime orchestration; the model registry + fail-closed validation (now also checking `engine_version`); adapters that feed streaming events/candles into the engine.

### 5.4 Proposed package layout

```
strategy-core/
  pyproject.toml            # deps: numpy, pydantic ; engine_version exported
  src/strategy_core/
    __init__.py             # ENGINE_VERSION
    types.py                # neutral Trade/Quote/Bar/Level/Zone/Touch
    constants.py            # 3.0, 2.0, 10, session windows, etc. (single source)
    candles/
      batch.py              # vectorized builder
      streaming.py          # event-at-a-time builder
    decisions/
      zones.py              # build_zones
      touch.py              # is_touch + first-touch scope tracker
      sessions.py           # classify_session (ET)
      features.py           # the feature formulas
      outcomes.py           # resolve_outcome (MAE-first)
    contract/
      schema.py             # Pydantic model + ENGINE_VERSION binding
      loader.py             # strict fail-closed loader
  tests/
    test_candle_parity.py   # batch == streaming
    test_golden_decisions.py# captures current training behavior
```

---

## 6. The contract (complete config)

The contract must carry the **full parameter space** the engine reads, so a parameter-only change is config-only:

- `engine_version` (Trade-Lab fail-closes if it can't match)
- `bar`: type (tick/time/volume), size
- `session_scheme`: timezone, trading-day boundary, session windows
- `level_scheme`: which levels, how zones are formed, `zone_proximity`, representative price
- `touch_rule`: type, direction mapping, scope (per-zone-per-day)
- `feature_set`: which features, windows, thresholds (`within_band`, `large_trade`), price source
- `label_policy`: resolution (mae_first), tp/sl/trap, forward bar type, cutoff
- `inference`: eligible class/session, confidence gate

**Two rules for the emitter:**
1. Every field is emitted from the engine's own constants/config — **no restated literals**.
2. Anything the engine *does* must be expressible in the contract. If the engine has a behavior the contract can't describe, that's a schema gap to close (and an engine-version bump).

---

## 7. Decisions the canonical choice forces — and the one still open

Choosing "canonical = training path" closes most of the divergences automatically:

| Divergence | Resolution (forced by canonical = training path) | Work it implies |
|---|---|---|
| Sessions ET vs Chicago | **ET** | Trade-Lab drops Chicago, adopts shared `classify_session` (ET). |
| Touch mechanism | **bar-intersect on zones** | Trade-Lab builds a zone layer + bar-intersect on closing candles; drops exact-tick. |
| First-touch scope | **per zone per day** | Trade-Lab switches its `(day, session, kind)` key to per-zone-per-day. |
| Outcome resolution | **MAE-first** (already aligned) | Keep; move to shared `resolve_outcome`. |

**Still genuinely open — needs your call:**

- **`mid_price_source`.** The code uses **trade price**; the contract says `top_of_book`. Since canonical = training path, the **default is: engine uses trade price, and we correct the contract label** to match what the model actually learned. ⚠️ If TOB-mid was the *intended* design, that is a **retrain**, not a contract edit — flag before proceeding.
- **Candle builder: numpy vs DuckDB.** Research builds tick bars in DuckDB SQL today (fast). Before committing research to the shared numpy builder, **benchmark it.** If numpy can't match DuckDB at your data sizes, fallback: the engine owns the bar *spec* + parity test, and research keeps its DuckDB builder as a verified-equivalent fast path.
- **Migrating non-critical research paths.** The experiment and live-dashboard paths also duplicate this logic. Recommend migrating them onto the shared engine *eventually*, but the first pass is **training ↔ Trade-Lab only** (that's the serving path with money on it).

---

## 8. Migration plan (phased, ordered)

1. **Scaffold `strategy-core`.** Package skeleton, deps (numpy, pydantic), `ENGINE_VERSION`, neutral types, `constants.py`. Move the contract Pydantic schema in from both repos so there is one definition.
2. **Lift the candle layer.** Promote Trade-Lab's `build_tick_bars_from_frame` (batch) + `CandleEngine` (streaming) + their parity test into the package. **Benchmark batch builder vs research DuckDB** (open decision above).
3. **Extract the decision layer from the training path.** Port `build_zones`, `_detect_touches` (bar-intersect), first-touch scope, `classify_session` (ET), the three feature formulas, and the MAE-first label resolution into scalar parameterized functions. **Write golden-vector tests** that capture current training output so we can prove behavior is preserved.
4. **Repoint research training onto the engine.** Adapter from DataFrames → engine inputs. Re-run dataset construction; **assert the training set is unchanged** (golden vectors). Resolve `mid_price_source` here.
5. **Fix the emitter.** Emit the *complete* contract from engine constants; delete the restated literals; use the shared schema; stamp `engine_version`.
6. **Repoint Trade-Lab onto the engine.** Build the zone layer; swap sessions to ET; swap touch to bar-intersect-on-zones, per-zone-per-day, fed by closing candles; route features/outcomes through the shared decision layer; **fail-close on `engine_version`**. Remove the bespoke exact-tick/Chicago/per-session machinery.
7. **End-to-end parity validation.** Take one trained model + one slice of raw data. Confirm research-training and Trade-Lab produce **identical touches, identical feature vectors, and identical outcome labels**. This is the real test — same touch population, same numbers.
8. **(Later) Absorb the remaining duplicates.** Migrate the research experiment + live-dashboard paths onto the shared engine to kill internal duplication.

---

## 9. Definition of done

- One engine implementation; no duplicate touch/session/level/label logic on the critical (training↔serving) path.
- The contract is **complete** (no engine behavior absent from it) and **emitted from engine constants** (no restated literals); `mid_price_source` reconciled.
- Given the same model + same raw data, research-training and Trade-Lab produce **identical touches, feature vectors, and outcome labels** (§8.7 passes).
- Trade-Lab **fail-closes** on `engine_version` mismatch.
- Candle build benchmark shows **no regression** vs current builders.
- A new **parameter-only** strategy config flows research → model → Trade-Lab with **zero code change**.

---

## 10. Non-goals

- Not rewriting the strategy itself — preserve current training behavior exactly.
- Not changing either repo's data/IO layer — research stays DuckDB/parquet, Trade-Lab stays Databento/streaming.
- Not forcing a monorepo.
- Not migrating the non-critical research paths in the first pass.
- Data-layer hygiene (e.g. multi-instrument contamination, never consuming research feature caches) remains each repo's own responsibility, out of scope here.

---

## Appendix A — current-state evidence anchors

**Research (`Claude-Quant-Lab`)**
- Touch (canonical): `dashboard_utility_builder.py:414-440` (`_detect_touches`, `bar_low <= rep <= bar_high`, `zone["touched"]`).
- Zones: `dashboard_utility_builder.py:382-411` (`_build_zones`); proximity `:53-54` (`_ZONE_PROXIMITY = 3.0`).
- Sessions (ET): `dashboard_utility_builder.py:44-54`; conversion `:314-322`.
- Features: `dashboard_utility_builder.py:446-538`; `mid` = trade price at `:488`; `within_band` 2.0 at `:502`; `large_trade` 10 at `experiment/features.py:44`.
- Labels (MAE-first): `dashboard_utility_labeling.py:71-108`.
- Bar build (batch SQL): `tick_store.py:451-533`.
- Contract emitter (restated literals): `strategy_contract.py` (incl. `:42-51` "restated here", `:52-59`, `:170-205`).
- Training orchestration: `scripts/ml_training_tab.py:921-942`, `:1110-1114`, `save_trained_model :517-525`.
- Duplicate paths: `experiment/event_detection.py`, `experiment/labeling.py`, `dashboard/engine/*`, `agents/data_infra/sessions.py`.

**Trade-Lab**
- Touch (exact-tick): `domain/levels.py:245-274`; eligible prices `:276-292`.
- Sessions (Chicago): `domain/sessions.py:8` (`CT`), `:35-52` (`classify_session`).
- No zone object: zones present only in `domain/contracts/strategy_contract.py:73-79` + `tests/fixtures/strategy.json:72-78`.
- Candles: streaming `domain/candles.py:103-172`; vectorized seed `services/seed.py:42-128` (parity-tested).
- Features: `services/inference/features/feature_functions.py:323-343`.
- Outcomes (MAE-first): `services/inference/outcome_tracker.py:160-191`.
- Runtime orchestration: `services/runtime.py:381-432`, trade path `:492-534`.
- Fail-closed registry / `contract_id`: `services/model_registry.py`.