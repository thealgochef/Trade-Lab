# BASELINE REPORT — Strategy-Core / Quant-Lab / Trade-Lab

READ-ONLY factual baseline survey. No source files were modified to produce this report.

Surveyed commits (HEAD at time of survey):
- Strategy-Core: `fd53e06` (`C:/Users/gonza/Documents/Strategy-Core`)
- Quant-Lab: `5a096a1` (`C:/Users/gonza/Documents/Claude-Quant-Lab`)
- Trade-Lab: `a191202` (`C:/Users/gonza/Documents/Trade-Lab`)

---

---

## 0. Executive summary

- **Three projects, one hub-and-spoke architecture.** *Strategy-Core* (`C:/Users/gonza/Documents/Strategy-Core`) is the shared, versioned decision engine — a library of pure functions (`build_zones` → `detect_touches` → `resolve_honest_outcome`) plus an event-at-a-time `StrategyRuntime`, stamped `ENGINE_VERSION = "strategy_core_engine_v3"`; it implements exactly one strategy (a PDH/PDL + Asia/London key-level touch / zone-reversal MAE-first classifier) and depends on neither sibling. *Quant-Lab* (`C:/Users/gonza/Documents/Claude-Quant-Lab`, ~16k LOC of agents + a Streamlit/CatBoost dashboard-utility pipeline) is the research/ML-training repo that builds 3-class datasets and emits `strategy.json` model bundles. *Trade-Lab* (`C:/Users/gonza/Documents/Trade-Lab`, FastAPI backend + React/Vite frontend) is the operator-facing live/replay workstation that drives the engine and streams derived deltas to the browser over a `ws.v1` WebSocket envelope.

- **The single seam is the `strategy_core` package plus the serialized `strategy.json` contract** — the dependency graph is `QL → SC ← TL`, with no Quant-Lab ↔ Trade-Lab edges. QL consumes the batch decision surface and TL consumes the streaming runtime surface; both bind to the same engine via the `engine_version` stamp carried inside `strategy.json`, which fail-closes on a version mismatch.

- **Maturity is asymmetric and the seam is only half-wired.** Strategy-Core and the QL→emitter→canonical-schema path are well-guarded (no-drift test, decision-layer parity harness with "0 label flips", shared loader, `extra="forbid"` frozen contract sections), but the contract *format* is physically duplicated in three places (SC `schema.py`, TL's divergent local `domain/contracts/strategy_contract.py`, and QL's hand-built dict emitter) — TL's runtime loads its own copy where `engine_version` is optional and two fields are missing, so the stated "format lives exactly once" goal is achieved for QL but not yet for TL.

- **The dominant open gap is that no v3 model can be served live.** Quant-Lab can train and emit `strategy_core_engine_v3` contracts, but Trade-Lab's model-serving layer is not yet v3-repointed (five enumerated items: fail-close activation, retire stale local candles/sessions/levels, route features through engine trade-print formulas, replace outcome tracking, prove parity), and no canonical v3 bundle has been verified — both blocked per `MIGRATION.md` and `DECISIONS.md` (D-033). Live Databento validation has never been run end-to-end.

- **The on-disk artifact reflects this in-flight state and a thin edge.** The production bundle `models/NQ_20260603_233847/strategy.json` declares 5 features, tp=15/sl=15/trap=5, NY-session/0.70-gate inference, and `supported_by_runtime: true` (diverging from the emitter's hardcoded `False`), while its `evaluation.json` shows OOS precision ≈0.487, permutation p ≈0.637, and `expectancy_15_15_pts = -0.39`.

- **There is no IFVG trading strategy and no plugin/strategy registry anywhere.** IFVG exists only as a research *signal detector* (`alpha_lab/.../signal_eng/detectors/tier2/ifvg.py`) and a Trade-Lab design doc (`docs/ifvg-strat.md`, the one modified working-tree file, self-described as "not yet proven profitable"); strategy selection is config-driven (`training_mode`, `ENGINE_VERSION`), and Trade-Lab's only discovery mechanism is *model-bundle* discovery, not strategy registration.

---

## 1. Repo map

Three projects, all present in the workspace. Excluded from all trees/counts: `.venv`, `node_modules`, `__pycache__`, `dist`, `build`, `.git`, caches, `.egg-info`. LOC counts are physical line counts of source files (Bash `wc -l`).

---

### 1.1 Strategy-Core — `C:/Users/gonza/Documents/Strategy-Core`

**Purpose (from `pyproject.toml` line 8):** "Shared, versioned strategy engine for zero-drift parity between Claude-Quant-Lab (research) and Trade-Lab (runtime)."

**Language(s):** Python only (`requires-python = ">=3.13"`, `.python-version` present). Packaged with Hatchling.

**Top-level tree (2-3 deep):**
```
Strategy-Core/
├── pyproject.toml
├── uv.lock
├── README.md  MIGRATION.md  V3_COMPATIBILITY_MATRIX.md  algo-dev.md
├── src/strategy_core/
│   ├── __init__.py  constants.py  types.py
│   ├── candles/      → batch.py  streaming.py  _ids.py  __init__.py
│   ├── contract/     → loader.py  schema.py  __init__.py
│   ├── data/         → databento_live.py  databento_parquet.py  events.py  ordering.py  __init__.py
│   ├── decisions/    → features.py  honest_entry.py  outcomes.py  sessions.py  touch.py  zones.py  __init__.py
│   └── runtime/      → levels.py  live.py  replay.py  state.py  __init__.py
├── tests/
└── validation/       → parity_harness.py  parity_harness_v2.py  decision_diff_harness.py
                         phase4b_validate.py  phase4d_liveorder.py  test_*.py  + _out/ reports
```

**Key dependencies (`pyproject.toml` lines 13-27):**
- Runtime: `numpy>=1.26`, `pydantic>=2.5`, `pyarrow>=18.0` (Databento parquet source), `tzdata>=2025.2`
- Optional `databento`: `databento>=0.79`
- Optional `dev`: `pytest>=8`, `pandas>=2.0` (only candle BATCH builder + tests touch pandas), `ruff>=0.15`

**LOC per major module (src/strategy_core, .py only):**
| Module | LOC |
|---|---|
| decisions/ | 986 |
| runtime/ | 885 |
| data/ | 710 |
| candles/ | 443 |
| contract/ | 360 |
| constants.py | 353 |
| types.py | 227 |
| __init__.py | 157 |
| tests/ (suite) | 2260 |

---

### 1.2 Quant-Lab (Claude-Quant-Lab) — `C:/Users/gonza/Documents/Claude-Quant-Lab`

**Purpose (from `pyproject.toml` line 8):** "Streamlit extrema-training workbench with retained dashboard compatibility/export tooling for NQ/ES futures". Package name `alpha-signal-lab`.

**Language(s):** Primarily Python (`requires-python = ">=3.14"`, setuptools). Also contains TypeScript front-end assets under `dashboard-ui/` (Vite + vitest config present) and `test-chart/`.

**Top-level tree (2-3 deep):**
```
Claude-Quant-Lab/
├── pyproject.toml  CLAUDE.md  AGENTS.md  ARCHITECTURE.md
├── inspect_data.py  test_chart.py
├── config/  data/  models/  docs/  catboost_info/
├── dashboard-ui/      → index.html  package.json  src/  tsconfig.json  vite.config.ts  vitest.*  __tests__/
├── test-chart/
├── scripts/           → run_pipeline.py  run_backtest.py  run_replay.py  dashboard.py
│   │                    honest_edge_audit.py  audit_lookahead.py  decision_repoint_proof.py
│   │                    phase8_1_golden.py  train_dashboard_model.py  ml_training_tab.py  … (~20 .py)
│   ├── audit_NQ_20260602/  (+ enriched/)
│   ├── levels_probe/  (+ out/)
│   └── v3_verify/  (+ ny_enriched/)
├── src/alpha_lab/
│   ├── core/          → agent_base.py  config.py  contracts.py  enums.py  exceptions.py  logging.py  message.py
│   ├── agents/        → data_infra/  execution/  monitoring/  orchestrator/  signal_eng/  validation/
│   ├── experiment/    → diagnostics.py  event_detection.py  features.py  key_levels.py  labeling.py  training.py
│   └── dashboard/     → api/  config/  db/  engine/  model/  pipeline/  trading/
└── tests/
```

**Key dependencies (`pyproject.toml`: runtime lines 13-35, dev lines 40-47):**
- Runtime: `pandas>=2.1`, `numpy>=1.26`, `scipy>=1.12`, `pydantic>=2.5`, `pydantic-settings>=2.0`, `PyYAML>=6.0`, `tzdata>=2025.2`, `polygon-api-client>=1.14.0`, `databento>=0.40`, `duckdb>=1.0`, `python-dotenv>=1.0`, `rich>=13.7`, `streamlit>=1.38`, `plotly>=5.18`, `catboost>=1.2`, `scikit-learn>=1.4`, `protobuf>=6.31,<7`, `sqlalchemy[asyncio]>=2.0`, `asyncpg`, `alembic`, `fastapi>=0.100`, `uvicorn>=0.20`, `python-multipart>=0.0.5`
- `dev`: `pytest>=7.4`, `pytest-asyncio>=0.23`, `pytest-cov>=4.1`, `ruff>=0.3`, `mypy>=1.8`, `pandas-stubs>=2.1`, `aiosqlite>=0.20`, `httpx>=0.24`

**LOC per major module (src/alpha_lab, .py only):**
| Module | LOC |
|---|---|
| agents/ (total) | 16,375 |
| └ agents/data_infra/ | 7,520 |
| └ agents/signal_eng/ | 4,997 |
| └ agents/validation/ | 1,152 |
| └ agents/monitoring/ | 1,094 |
| └ agents/execution/ | 976 |
| └ agents/orchestrator/ | 635 |
| dashboard/ (total) | 6,615 |
| └ dashboard/api/ | 2,086 |
| └ dashboard/pipeline/ | 1,694 |
| └ dashboard/trading/ | 940 |
| └ dashboard/engine/ | 871 |
| └ dashboard/db/ | 510 |
| └ dashboard/model/ | 461 |
| └ dashboard/config/ | 53 |
| experiment/ | 4,053 |
| core/ | 944 |
| scripts/ (top-level .py) | 13,281 |

Note: `src/alpha_lab/dashboard/` contains only `__init__.py` at its top level; all code is in the seven subpackages above.

---

### 1.3 Trade-Lab — `C:/Users/gonza/Documents/Trade-Lab`

**Purpose:** UI dashboard / live + replay workstation. Backend `pyproject.toml` line 8: "Deterministic backend foundation for the Trade-Lab NQ workstation." Split into a Python `backend/` and a TypeScript/React `frontend/`.

**Language(s):** Python (backend, `requires-python = ">=3.13"`, Hatchling) + TypeScript/React (frontend, Vite + Vitest). Repo root also holds `docs/`, `plans/`, `scripts/`, `.github/`.

**Top-level tree (2-3 deep):**
```
Trade-Lab/
├── README.md  AGENTS.md  package-lock.json
├── docs/  plans/  scripts/  .github/
├── backend/
│   ├── pyproject.toml
│   ├── src/trade_lab/
│   │   ├── adapters/   → databento.py  databento_historical.py  historical_parquet.py
│   │   │                  replay_catalog.py  synthetic_replay.py  __init__.py
│   │   ├── api/        → app.py  __main__.py  dto.py  serialization.py  __init__.py
│   │   ├── domain/     → candles.py  data_quality.py  events.py  feed.py  levels.py
│   │   │                  market_context.py  observations.py  outcomes.py  prices.py  sessions.py  __init__.py
│   │   ├── ports/      → market_data.py  __init__.py
│   │   └── services/   → bounded_queue.py  broadcaster.py  live.py  model_registry.py
│   │                      replay.py  runtime.py  seed.py  strategy_core_service.py  __init__.py
│   └── tests/  (+ fixtures/)
└── frontend/
    └── src/
        ├── main.tsx  App.tsx  config.ts  vite-env.d.ts  (+ *.test.*)
        ├── api/        → client.ts  types.ts  client.test.ts
        ├── chart/      → overlayManager.ts  viewModels.ts  (+ *.test.ts)
        ├── components/ → ChartWorkspace  EventBlotter  IntelligencePanel  LiveDataPanel
        │                  ModelPanel  ReplayControls  TopStatusBar  TradingChart (.tsx + .test.tsx each)
        ├── domain/     → models.ts  normalize.ts  normalize.test.ts
        ├── realtime/   → client.ts  types.ts  client.test.ts
        └── state/      → createStore.ts  stores.ts  stores.test.ts
```

**Backend key dependencies (`backend/pyproject.toml` lines 11-28):**
- Runtime: `catboost>=1.2`, `fastapi>=0.115`, `httpx2>=2.3`, `orjson>=3.10`, `pyarrow>=18.0`, `pydantic>=2.10`, `pydantic-settings>=2.6`, `uvicorn[standard]>=0.32`, and `strategy-core @ git+https://github.com/thealgochef/Strategy-Core.git@fd53e06989084368aa3b89d33eb83bee081b695f` (pinned git dependency on Strategy-Core)
- `dev`: `pytest>=8.3`, `pytest-asyncio>=0.24`, `ruff>=0.8`

**Frontend key dependencies (`frontend/package.json` lines 13-34):**
- Runtime: `react 18.3.1`, `react-dom 18.3.1`, `lightweight-charts 5.0.9`
- Dev: `@vitejs/plugin-react 4.3.4`, `@eslint/js 9.18.0`, `@testing-library/react 16.1.0`, `@testing-library/jest-dom 6.6.3`, `@types/react 18.3.18`, `@types/react-dom 18.3.5`, `eslint 9.18.0`, `eslint-plugin-react-hooks 5.1.0`, `eslint-plugin-react-refresh 0.4.16`, `globals 15.14.0`, `jsdom 25.0.1`, `typescript 5.7.3`, `typescript-eslint 8.20.0`, `vite 5.4.21`, `vitest 2.1.8`

**LOC per major module:**
Backend (`src/trade_lab`, .py only):
| Module | LOC |
|---|---|
| services/ | 3,580 |
| adapters/ | 1,895 |
| domain/ | 1,454 |
| api/ | 998 |
| ports/ | 40 |
| tests/ (suite) | 8,884 |

Frontend (`frontend/src`, .ts/.tsx incl. co-located tests):
| Dir | LOC |
|---|---|
| components/ | 1,873 |
| realtime/ | 1,048 |
| chart/ | 684 |
| domain/ | 685 |
| api/ | 526 |
| state/ | 359 |
| root (App/main/config etc.) | 270 |
| test/ | 1 |

---

## 2. The seam between the projects (MOST IMPORTANT)

The three projects are stitched together by exactly one shared Python package, `strategy_core`, plus one serialized artifact, `strategy.json`. Below is what the code actually shows, traced from real import edges and the contract files.

### (a) Cross-repo dependency direction — the real import edges

`strategy_core` is the hub; both other repos depend on it, and it depends on neither of them. Verified from `C:/Users/gonza/Documents/Strategy-Core/pyproject.toml:13-18` — its only deps are `numpy`, `pydantic`, `pyarrow`, `tzdata` (no `alpha_lab`/`trade_lab`). **Label: IMPLEMENTED.**

Edge **Trade-Lab → Strategy-Core** (declared + used):
- Hard pin in `C:/Users/gonza/Documents/Trade-Lab/backend/pyproject.toml:19`:
  ```
  "strategy-core @ git+https://github.com/thealgochef/Strategy-Core.git@fd53e06989084368aa3b89d33eb83bee081b695f",
  ```
  (`allow-direct-references = true` at line 31 permits the git URL.) This is a SHA-pinned dependency — TL binds to one exact Strategy-Core commit.
- Runtime imports: `C:/Users/gonza/Documents/Trade-Lab/backend/src/trade_lab/services/strategy_core_service.py:16-27` (`StrategyRuntime`, `RuntimeSnapshot`, `RuntimeUpdate`, `FeedStatus`, and the `types` `Bar/Quote/Trade/Touch/Level/Direction/CloseReason`), `services/live.py:18-19` (`LiveRuntime`, `LiveState`), `services/replay.py:13-15` (`ReplayConfig`, `ReplayRuntime`, `ReplayState`), and `domain/contracts/strategy_contract.py:17` (`import strategy_core`, used for `strategy_core.ENGINE_VERSION`).

Edge **Claude-Quant-Lab → Strategy-Core** (used, but NOT declared):
- Direct engine imports across source, scripts, and tests, e.g. `C:/Users/gonza/Documents/Claude-Quant-Lab/src/alpha_lab/agents/data_infra/ml/engine_decision.py:45-67`, `.../ml/strategy_contract.py:38-39`, `.../ml/config.py:404-405`, `.../ml/dashboard_utility_builder.py:32-40`, plus many scripts (`scripts/phase8_1_golden.py:39-40`, `scripts/v3_verify/*.py`, `scripts/audit_NQ_20260602/*.py`) and tests (`tests/agents/test_strategy_contract_*.py`, `test_decision_repoint_parity.py:445`).
- However, `strategy-core` does **NOT** appear in `C:/Users/gonza/Documents/Claude-Quant-Lab/pyproject.toml` dependencies (lines 12-36 list pandas/numpy/scipy/catboost/etc., no strategy-core), and there is no `requirements*.txt`, `uv.lock`, `.pth`, or `conftest.py` `sys.path` insertion referencing it (grep returned no matches). So QL resolves `strategy_core` through an undeclared external/editable install (consistent with the "PYTHONPATH=src" note in memory). **Label: PARTIAL/STUB** — the QL→SC edge is real in code but unpinned/undeclared, unlike the TL→SC SHA pin.

No `Quant-Lab ↔ Trade-Lab` edges exist (no cross-imports found). The graph is `QL → SC ← TL`.

### (b) How Strategy-Core exposes a strategy to QL and TL

Two distinct mechanisms, both IMPLEMENTED:

1. **Shared installed package (live code path).** Both repos import `strategy_core` directly. Public API is re-exported from `C:/Users/gonza/Documents/Strategy-Core/src/strategy_core/__init__.py:59-157` (zones/touches/sessions/features/outcomes, the `Bar/Quote/Trade/Touch` types, candle engine, and the contract loader/schema). TL consumes the **streaming runtime** surface (`strategy_core.runtime.state.StrategyRuntime`, `LiveRuntime`, `ReplayRuntime`); QL consumes the **batch decision** surface (`build_zones`, `detect_touches`, `resolve_outcome`, the six feature functions, `classify_session`). `strategy_core_service.py:1-7` states the contract explicitly: TL "must not recompute session/level/touch strategy meaning" — it is a thin adapter mapping TL DTOs to/from neutral Strategy-Core events.

2. **Serialized `strategy.json` contract feed (model-bundle path).** QL **emits** the contract as a dict (`C:/Users/gonza/Documents/Claude-Quant-Lab/src/alpha_lab/agents/data_infra/ml/strategy_contract.py:63` `build_strategy_contract(...) -> dict | None`), shipped beside `model.cbm`. TL **loads** it at registry time (`C:/Users/gonza/Documents/Trade-Lab/backend/src/trade_lab/services/model_registry.py:135` and `:286` call `load_strategy_contract(...)`). Version stamps `ENGINE_VERSION`/`CONTRACT_VERSION` (`__init__.py:54,57`) ride inside the JSON to bind a bundle to the engine that built it.

There is no event-bus or socket protocol between repos; the seam is in-process imports plus the on-disk JSON bundle.

### (c) The canonical contract BOTH consume — verbatim

The canonical schema lives in `C:/Users/gonza/Documents/Strategy-Core/src/strategy_core/contract/schema.py`. The top-level model, lines 246-279:

```python
class StrategyContract(_ContractModel):
    """A fully parsed, validated ``strategy.json`` for one model bundle.

    Ported from ``strategy_contract.py:156-180`` with one required field added:
    ``engine_version`` (spec §6), placed right after ``contract_version``. It is the
    structural binding between a bundle and the engine version that produced its
    labels/features; Trade-Lab fail-closes on a mismatch via the loader hook.
    """

    contract_version: str = Field(min_length=1, max_length=64)
    engine_version: str = Field(min_length=1, max_length=64)
    strategy_id: str = Field(min_length=1, max_length=256)
    training_mode: str = Field(min_length=1, max_length=64)
    supported_by_runtime: bool
    instrument: str = Field(min_length=1, max_length=32)
    tick_size: float = Field(gt=0.0)
    point_value: float = Field(gt=0.0)
    model: Model
    feature_set: FeatureSet
    class_map: ClassMap
    session_scheme: SessionScheme
    level_scheme: LevelScheme
    touch_rule: TouchRule
    feature_windows: FeatureWindows
    label_policy: LabelPolicy
    inference: InferencePolicy
    data_requirements: DataRequirements
    provenance: Provenance
    research_session_experiment: ResearchSessionExperiment | None = None

    @property
    def feature_count(self) -> int:
        return len(self.feature_set.names)
```

The module docstring (`schema.py:1-22`) is explicit that this file was "Promoted verbatim from Trade-Lab's `backend/src/trade_lab/domain/contracts/strategy_contract.py`… so the contract *format* lives exactly once and cannot drift." The strict, fail-closed loader sits beside it in `C:/Users/gonza/Documents/Strategy-Core/src/strategy_core/contract/loader.py:28-77` (`load_strategy_contract(path, *, expected_engine_version=None)`), raising `ContractError` (never a bare `ValidationError`) and checking `contract_version` (`loader.py:58-62`) then the optional `expected_engine_version` (`loader.py:64-70`).

**Critical caveat — the "exactly once" claim is not yet true for the runtime path. Label: PARTIAL/STUB.** TL does **not** consume the canonical `strategy_core.contract` at runtime. `C:/Users/gonza/Documents/Trade-Lab/backend/src/trade_lab/domain/contracts/__init__.py:8-23` re-exports `StrategyContract`/`load_strategy_contract` from its **own local copy** `trade_lab/domain/contracts/strategy_contract.py`, and `model_registry.py:26` imports from `trade_lab.domain.contracts` (the local copy), not from `strategy_core`. That local copy has diverged from the canonical schema in three concrete ways:
- `engine_version` is **optional** (`C:/Users/gonza/Documents/Trade-Lab/backend/src/trade_lab/domain/contracts/strategy_contract.py:168` `engine_version: str | None = Field(default=None, …)`), whereas the canonical SC field is **required** (`schema.py:256`).
- `LabelPolicy` is **missing `decision_offset_minutes`** (TL `strategy_contract.py:95-103` vs canonical `schema.py:159-167`).
- TL's `StrategyContract` has **no `research_session_experiment` field** (TL `strategy_contract.py:163-185` vs canonical `schema.py:274`).

The one thing that keeps them parseable across the boundary is that both declare the same format string `CONTRACT_VERSION = "trade_lab_contract_v1"` (TL local `strategy_contract.py:20`; SC `__init__.py:57`). Meanwhile QL's emitter is a **third** representation — a hand-built dict (`C:/Users/gonza/Documents/Claude-Quant-Lab/src/alpha_lab/agents/data_infra/ml/strategy_contract.py:118-220`), not either pydantic model.

### (d) How "no drift" between research and live is enforced today

A layered set of mechanisms, of mixed completeness:

- **engine_version binding (fail-closed).** IMPLEMENTED on both sides, but via two different loaders. SC canonical loader rejects mismatches via the `expected_engine_version` hook (`loader.py:64-70`). TL's local loader does its own inline binding against the running engine (`C:/Users/gonza/Documents/Trade-Lab/backend/src/trade_lab/domain/contracts/strategy_contract.py:230-237` raises `ContractError` when `contract.engine_version != strategy_core.ENGINE_VERSION`; `:238-242` loads a legacy/unbound bundle with only a warning). Current stamp is `ENGINE_VERSION = "strategy_core_engine_v3"` (`__init__.py:54`); a v2 bundle "correctly fails the v3 loader" per the docstring (`__init__.py:53`).
- **TL regression test for the binding.** IMPLEMENTED. `C:/Users/gonza/Documents/Trade-Lab/backend/tests/test_engine_version_binding.py:32-62` proves matching loads, mismatched fails closed, legacy loads unbound.
- **QL no-drift / coverage-guard test.** IMPLEMENTED. `C:/Users/gonza/Documents/Claude-Quant-Lab/tests/agents/test_strategy_contract_nodrift.py:1-31` asserts every structural emitted field equals its `strategy_core.constants` value, enumerates every leaf of `StrategyContract` to forbid a new uncovered field, and re-loads the emitted contract through the **shared SC loader** (`from strategy_core.contract.loader import load_strategy_contract`, line 27) — so QL's emitter IS validated against canonical SC.
- **`extra="forbid", frozen=True` on every contract section** (`schema.py:66`) so unknown keys/drift are never silent.
- **Decision-layer parity harness (research == legacy engine).** IMPLEMENTED. `C:/Users/gonza/Documents/Claude-Quant-Lab/tests/agents/test_decision_repoint_parity.py:1-34` pins the engine's book-mid mode byte-identical to the legacy CQL decision code (zones, touches, labels, the six features with "max abs diff must be 0"). Companion proof scripts exist: `C:/Users/gonza/Documents/Claude-Quant-Lab/scripts/decision_repoint_proof.py`, `scripts/run_databento_acceptance.py` (decision-diff/parity grep hits).
- **Strategy-Core golden tests.** IMPLEMENTED. `C:/Users/gonza/Documents/Strategy-Core/tests/test_candle_parity.py`, `test_contract.py`, `test_features.py`, `test_outcomes.py`, `test_touch.py`, `test_zones.py`, `test_sessions.py`, plus runtime parity tests.
- **SHA-pinned dependency.** IMPLEMENTED for TL (`backend/pyproject.toml:19` pins commit `fd53e06`); **NOT enforced** for QL (no SC pin in `Claude-Quant-Lab/pyproject.toml`).

**Net assessment of drift enforcement: PARTIAL.** Research→emitter→canonical-schema is well guarded (no-drift test + parity harness + shared loader). The live side is guarded only by the engine_version string check, and it runs through TL's **divergent local copy** of the schema/loader rather than `strategy_core.contract`, so the contract *format* is still physically duplicated in three places (SC `schema.py`, TL `domain/contracts/strategy_contract.py`, QL dict emitter). The single-source-of-truth goal stated in `schema.py:1-22` is achieved for QL but not yet wired through TL's runtime.

---

## 3. Strategy-Core engine

Survey root: `C:/Users/gonza/Documents/Strategy-Core/src/strategy_core/`. Package layout (tracked `.py`, excluding tests/validation/`__pycache__`): `candles/` (`batch.py`, `streaming.py`, `_ids.py`), `contract/` (`loader.py`, `schema.py`), `data/` (`databento_live.py`, `databento_parquet.py`, `events.py`, `ordering.py`), `decisions/` (`features.py`, `honest_entry.py`, `outcomes.py`, `sessions.py`, `touch.py`, `zones.py`), `runtime/` (`levels.py`, `live.py`, `replay.py`, `state.py`), plus top-level `constants.py`, `types.py`, `__init__.py`.

The package self-describes as the single shared engine imported by both Claude-Quant-Lab (research/training, batch) and Trade-Lab (live/replay inference), versioned by `ENGINE_VERSION = "strategy_core_engine_v3"` (`src/strategy_core/__init__.py:54`) and `CONTRACT_VERSION = "trade_lab_contract_v1"` (`__init__.py:57`). Status: **IMPLEMENTED** as a library of pure decision functions + a streaming runtime; there is no user-facing "Strategy" plugin abstraction.

### 3.1 Strategy base class / protocol — NOT FOUND

There is **NO** `Strategy` base class, ABC, or `Protocol` that a strategy subclasses/implements anywhere in `strategy_core`. A scan for `class …`, `Protocol`, `abstractmethod`, `ABC` across `src/` returns only dataclasses, `StrEnum`s, pydantic contract models, the candle/level state machines, the runtime controllers, and `OutcomeResult`/`HonestEntryDrop` result records — no strategy interface. (`abstractmethod`/`Protocol`/`ABC`: zero hits in `src/`.)

The engine's unit of strategy logic is therefore **not** a pluggable class but a fixed pipeline of pure functions plus one event-at-a-time runtime object:

- Zone construction: `build_zones` (`decisions/zones.py:23`).
- First-touch detection: `detect_touches` / `is_touch` (`decisions/touch.py:47,34`).
- Outcome labeling: `resolve_outcome` / `classify_mae_first` (`decisions/outcomes.py:127,63`).
- Honest decision-time orchestration: `resolve_honest_outcome` (`decisions/honest_entry.py:76`).
- Streaming driver: `StrategyRuntime` (`runtime/state.py:170`).

The single hard-coded "strategy" these encode is a **key-level touch / zone-reversal classifier** (PDH/PDL + Asia/London session high-low sweeps, classified MAE-first into reversal vs trap vs blowthrough). The closest thing to a runtime "strategy object" is `StrategyRuntime`, whose contract is its docstring + `process_event`:

`src/strategy_core/runtime/state.py:170-236`
```python
class StrategyRuntime:
    """Event-at-a-time Strategy-Core runtime for replay/live market data."""

    def __init__(
        self,
        *,
        timeframes: tuple[int, ...] = (147, 987, 2000),
        decision_timeframe: int | None = None,
        requested_symbol: str | None = None,
        scheme: SessionScheme = RESEARCH_SESSION_SCHEME,
        tick_size: float = DEFAULT_TICK_SIZE,
        recent_closed_bar_limit: int = 500,
        warning_limit: int = 100,
    ) -> None:
        ...

    def process_event(self, event: Trade | Quote | DataQualityWarning) -> RuntimeUpdate:
        if isinstance(event, DataQualityWarning):
            return self.record_warning(event)
        if isinstance(event, Quote):
            return self._process_quote(event)
        if isinstance(event, Trade):
            return self._process_trade(event)
        raise TypeError(f"unsupported runtime event type: {type(event).__name__}")
```

### 3.2 Execution model — event-sourced, event-at-a-time (IMPLEMENTED)

Live and replay both fold **one market event at a time**; there is no vectorized backtest sweep in the runtime. `StrategyRuntime._process_trade` is the main step loop, folding each trade into every timeframe's candle, updating level state, and running first-touch detection on each newly-closed decision-timeframe bar:

`src/strategy_core/runtime/state.py:262-290`
```python
    def _process_trade(self, trade: Trade) -> RuntimeUpdate:
        self._last_event_ts_utc = trade.event_ts_utc
        candle_update = self.candles.process_trade(trade)
        if candle_update.completed:
            self._recent_closed_bars.extend(candle_update.completed)
            if len(self._recent_closed_bars) > self._recent_closed_bar_limit:
                del self._recent_closed_bars[: len(self._recent_closed_bars) - self._recent_closed_bar_limit]
        levels = self.level_state.process_trade(trade)
        touches: list[Touch] = []
        for bar in candle_update.completed:
            if bar.timeframe_ticks != self.decision_timeframe:
                continue
            zones = self._zones_for_detection(bar.trading_day)
            detected = detect_touches((bar,), zones, tick_size=self.tick_size, trading_day=bar.trading_day)
            for touch in detected:
                self._touched_zone_keys.add(self._touch_zone_key_from_touch(touch, zones))
            touches.extend(detected)
        if touches:
            self._touches.extend(touches)
        session, trading_day = self._session_state()
        self._feed_status = FeedStatus(state="replaying", mode="runtime", requested_symbol=self.requested_symbol, last_event_ts_utc=trade.event_ts_utc, last_message="trade processed")
        return RuntimeUpdate(...)
```

The candle engine itself is the per-event aggregator, `CandleEngine.process_trade` (`candles/streaming.py:115`), which "Fold[s] one trade into every timeframe's current bar" and freezes a bar `COMPLETE` once `trade_count == timeframe` (tick bars; defaults `(147, 987, 2000)`).

The replay/live drivers wrap this same `process_event` step:
- `ReplayRuntime` (`runtime/replay.py:59`) iterates `self.source.events()` in a single `while True` loop (`replay.py:129-170`), calling `_process_item` → `runtime.process_event(item)` per item (`replay.py:203-206`), with pause/resume/stop, optional speed-based `asyncio.sleep`, and a timestamp-regression callback hook. It is async but strictly sequential per event.
- `LiveRuntime` (`runtime/live.py:46`) `async for item in self.source.events()` → `process_item(item)` per item (`live.py:139-154`).

There IS a separate **batch** candle builder, `build_tick_bars_from_frame` (`candles/batch.py`, re-exported at `__init__.py:60`), used by the research path to build bars over a pandas frame; but bar→touch→outcome decisioning is the event/forward-scan path above, not a vectorized signal generator.

### 3.3 How a strategy receives data and emits signals — IMPLEMENTED (signals = touches; NO order emission in-engine)

Input: `process_event(event: Trade | Quote | DataQualityWarning)` (`state.py:229`). Market types are the dependency-free dataclasses `Trade`, `Quote`, `Bar` in `types.py` (prices carried as integer **ticks**; `*_points` helpers convert via `tick_size`).

Output: a `RuntimeUpdate` (`state.py:113-137`) carrying deltas — `current_bars`, `closed_bars`, `levels`, `zones`, `touches`, `last_quote`, `feed_status`, `warnings`. The emitted "signal" is a `Touch` (`types.py:164-172`): a detected first-touch with `direction` (LONG/SHORT), `representative_price`, `level_type`, `trading_day`. The runtime does **NOT** emit orders, fills, or positions — there is no order/fill object in `strategy_core`. Inference/acting is left to adapters: the engine only ships descriptors `INFERENCE_ELIGIBLE_SESSION = "ny"` and `DEFAULT_CONFIDENCE_GATE = 0.70` as constants (`constants.py:288-289`), explicitly "a runtime convention the runtime may override." Quotes are passed through (`_process_quote`, `state.py:256-260`) and only update `last_quote`/feed status; they are not aggregated into bars.

### 3.4 Stop-loss / take-profit / barrier exits — IMPLEMENTED (label policy, not live order brackets)

Exits are expressed as **MAE-first outcome labeling thresholds**, not as live bracket orders. The kernel is `classify_mae_first` (`decisions/outcomes.py:63`): adverse excursion is checked FIRST so a bar breaching both stop and target resolves to the LOSS.

`src/strategy_core/decisions/outcomes.py:63-106`
```python
def classify_mae_first(
    max_mfe: float,
    max_mae: float,
    *,
    tp_points: float,
    sl_points: float,
    trap_mfe_min: float,
    forced: bool = False,
) -> str | None:
    """Apply the MAE-first ladder to running MFE/MAE extremes; the shared kernel.
    ...
    """
    # Adverse FIRST (conservative): both-breach -> loss, never the win.
    if max_mae >= sl_points:
        return TRAP_REVERSAL if max_mfe >= trap_mfe_min else AGGRESSIVE_BLOWTHROUGH

    # Then favorable.
    if max_mfe >= tp_points:
        return TRADEABLE_REVERSAL

    # RTH cutoff: force a loss-side resolution with the same trap/blowthrough split.
    if forced:
        return TRAP_REVERSAL if max_mfe >= trap_mfe_min else AGGRESSIVE_BLOWTHROUGH

    return None
```

The 3-class scheme (`outcomes.py:8-13`): `tradeable_reversal` (MFE ≥ tp before MAE ≥ sl), `trap_reversal` (MAE ≥ sl and MFE ≥ trap_mfe_min), `aggressive_blowthrough` (MAE ≥ sl and MFE < trap_mfe_min), `no_resolution` (neither hit). The canonical policy referenced throughout is tp=15 / sl=30 / trap_mfe_min=5 points (e.g. `outcomes.py:26`).

The **forward scan** that accumulates MFE/MAE per bar is `resolve_outcome` (`decisions/outcomes.py:127`), a PURE function over `(entry_points, direction, forward_bars, tick_size, tp/sl/trap)` — it does not know about decision time, sessions, the flatten rule, or market data (`outcomes.py:29-43`). Per bar it computes `bar_mfe = high - entry` / `bar_mae = entry - low` for LONG (mirrored for SHORT) and breaks on first resolution (`outcomes.py:180-205`).

**Time/barrier exits** (flatten + RTH cutoff + decision offset) live in `resolve_honest_outcome` (`decisions/honest_entry.py:76`), which wraps `resolve_outcome`:

`src/strategy_core/decisions/honest_entry.py:122-161`
```python
    tz = ZoneInfo(timezone)

    # (a) decision instant = touch close + decision_offset (== interaction window).
    decision_ts_utc = touch.bar_ts_utc + timedelta(minutes=decision_offset_minutes)
    decision_ts_et = decision_ts_utc.astimezone(tz)

    rth_cutoff_et = datetime.combine(touch.trading_day, rth_end, tzinfo=tz)

    # (b) executor no-entry rule: drop a decision at/after the flatten ... or RTH cutoff.
    if decision_ts_et.time() >= flatten_time:
        return HonestEntryDrop(reason="flatten", decision_ts_utc=decision_ts_utc)
    if decision_ts_utc >= rth_cutoff_et:
        return HonestEntryDrop(reason="cutoff", decision_ts_utc=decision_ts_utc)

    # (c) entry = realistic front-month TRADE price at the decision instant ...
    entry_price = trade_price_at(decision_ts_utc)
    if entry_price is None:
        return HonestEntryDrop(reason="no_fill", decision_ts_utc=decision_ts_utc)

    # (d) forward window: bars whose close is strictly AFTER the decision AND strictly BEFORE the RTH cutoff.
    forward = [
        bar
        for bar in day_bars
        if bar.close_ts_utc > decision_ts_utc and bar.close_ts_utc < rth_cutoff_et
    ]
    if not forward:
        return HonestEntryDrop(reason="no_forward", decision_ts_utc=decision_ts_utc, entry_price=float(entry_price))
    ...
```

Defaults are engine constants (`honest_entry.py:85-88`): `decision_offset_minutes = DECISION_OFFSET_MINUTES` (5 min), `flatten_time = FLATTEN_TIME` (16:40 ET, `constants.py:275`), `rth_end = RTH_END` (17:00 ET). The streaming-side forced/RTH-cutoff branch is the `forced=True` arm of `classify_mae_first` (`outcomes.py:102-104`).

### 3.5 Position / fill model — IMPLEMENTED (single-entry label model; no order types)

There is no order-type system (no market/limit/stop order objects), no position sizing, and no portfolio. The fill model is implicit in `resolve_honest_outcome`:

- **Entry timing**: `decision_ts = touch bar close + decision_offset_minutes` (`honest_entry.py:127`). The entry is realized only at/after the touch is knowable at bar close — never intrabar at the touch instant.
- **Fill price**: an injected callable `trade_price_at: Callable[[datetime], float | None]` (`honest_entry.py:79`) — "the realistic front-month TRADE price at (or just before) a UTC decision instant" with 30-min bounded lookback (`honest_entry.py:106-109`). A `None` return is a `no_fill` drop. The engine itself does no I/O; the adapter supplies the print query.
- **Intrabar assumption**: outcome excursions use each forward bar's `high_ticks`/`low_ticks` (`outcomes.py:181-189`) — i.e. full-bar range is assumed reachable. The conservative both-breach→loss rule (`outcomes.py:95-96`) is the explicit guard against optimistic intrabar fills.
- **Touch timestamp** is `bar.close_ts_utc` (`touch.py:101`), documented (`touch.py:13-20`) as the bar's close instant so a streaming consumer reproduces it with zero look-ahead.

`MID_PRICE_SOURCE == "trade_price"` (`constants.py`, referenced `features.py:5`) — interaction features and the fill use trade prints, not top-of-book mid (a deliberate v2 standardization, `features.py:6-23`).

### 3.6 Lookahead / future-bar usage — NOT FOUND as a defect; explicit anti-lookahead guards present

No future-bar peeking was found. A scan for `shift(-`, forward `iloc`/`tail`, "peek" returns only comment occurrences of "look-ahead" describing the guards. The engine carries three explicit, documented anti-lookahead mechanisms:

1. **Level availability gate** (`available_from`). A zone is skipped on any bar that closes before its level is "real" — `decisions/touch.py:93`:
   ```python
   if zone.available_from is not None and bar.close_ts_utc < zone.available_from:
       continue
   ```
   Critically, the skip does NOT consume the zone's first-touch flag (`touch.py:70-78,90-92`), so the recorded touch is the first qualifying *return* after the defining session closes, never the forming bar. `available_from` is the level's defining-session close (`types.py:124-139`): PDH/PDL from the trading-day start (prior 18:00 ET), Asia from 02:45 ET, London from 08:00 ET. Zone availability is the MAX of constituents (`zones.py:87-94`). These instants are computed in `runtime/levels.py:102-114` (`_trading_day_start_available`, `_session_close_available`).
2. **Feature/label window non-overlap.** The decision fires at `touch + decision_offset`, so the feature window `[touch, touch+offset]` and the label window `(decision, RTH_END]` never overlap (`honest_entry.py:13-16`, `outcomes.py:29-43`, `constants.py:262-267`). Forward bars are strictly `close > decision_ts` (`honest_entry.py:151-154`).
3. **Touch knowable only at bar close** (`touch.py:13-20`) — uses `close_ts_utc`, the last tick of the bucket.

Note (`state.py:292-306`): `_zones_for_detection` builds zones from ALL current levels and does NOT pre-filter by availability before `build_zones`; the look-ahead protection is delegated entirely to the per-bar gate in `detect_touches` (`touch.py:93`). `available_from is None` (the legacy/book-mid path) means ungated, reproducing pre-v3 no-guard behavior (`types.py:131-134`, `touch.py:76-78`).

### 3.7 Strategies implemented + IFVG search across all three repos

**Implemented in Strategy-Core (1, implicit):** a key-level touch / zone-reversal classifier over PDH/PDL and Asia/London session highs-lows, labeled MAE-first into `tradeable_reversal` / `trap_reversal` / `aggressive_blowthrough` / `no_resolution`. It is not packaged as a named "Strategy" object — it is the fixed `build_zones → detect_touches → resolve_(honest_)outcome` pipeline driven by `StrategyRuntime`. No other strategy variants are implemented in `strategy_core`.

**IFVG / inverse-FVG / fair-value-gap — EXPLICIT cross-repo result** (grep of `ifvg|inverse[ _-]?fvg|fair[ _-]?value[ _-]?gap` over `*.py/*.md/*.json/*.ts/*.tsx`, vendored dirs excluded):

- **Strategy-Core: NOT FOUND.** Zero matches anywhere under `C:/Users/gonza/Documents/Strategy-Core`. There is no IFVG (or any FVG) logic in the shared engine.
- **Trade-Lab: NOT FOUND as code — DOC ONLY.** The single match is `C:/Users/gonza/Documents/Trade-Lab/docs/ifvg-strat.md` (a design doc; note it appears modified in the working tree per git status). No IFVG strategy is implemented in Trade-Lab code.
- **Claude-Quant-Lab: IMPLEMENTED, but as a research SIGNAL DETECTOR, not a tradeable strategy.** `src/alpha_lab/agents/signal_eng/detectors/tier2/ifvg.py` defines `class IFVGDetector(SignalDetector)` (`ifvg.py:29`, `detector_id = "ifvg"`) that "Detects inverse FVG continuation patterns" and emits a `SignalVector` (direction/strength) over multi-timeframe bars (`ifvg.py:1-10,29-34`). Related FVG code lives alongside it: `tier2/fair_value_gaps.py`, `tier2/_fvg_helpers.py` (`detect_fvgs`, `track_fvg_fills`), `tier3/displacement.py`, `tier3/sweep_fvg_combo.py`, with coverage in `tests/agents/test_signal_eng.py`. This is a feature/signal generator in the research repo and is NOT wired into the Strategy-Core engine, the contract, the runtime, or any executor.

**Conclusion (as expected):** there is **no implemented IFVG trading strategy** in any of the three repos. What exists is (a) an IFVG *signal detector* in Claude-Quant-Lab's research signal-engineering layer, and (b) a design doc (`Trade-Lab/docs/ifvg-strat.md`). The production decision engine (Strategy-Core) implements only the level-touch / zone-reversal MAE-first classifier described above.

---

## 4. Data & bar model

### 4.1 Core neutral value types (Strategy-Core) — IMPLEMENTED

Strategy-Core defines dependency-free (stdlib-only) value types. Prices are carried as integer **ticks** throughout; the `*_points(tick_size)` helpers convert to points. Timestamps are tz-aware `datetime` named `event_ts_utc` / `open_ts_utc` / `close_ts_utc` (UTC by convention).

`Trade` and `Quote` (the inputs the engine consumes), verbatim:

`C:/Users/gonza/Documents/Strategy-Core/src/strategy_core/types.py` lines 58–88:
```python
@dataclass(frozen=True, slots=True)
class Trade:
    """A single print. ``price_ticks`` is the trade price in integer tick units."""

    event_ts_utc: datetime
    price_ticks: int
    size: int
    #: Aggressor side if known ('A' = buy/ask-lift, 'B' = sell/bid-hit). Unused by
    #: the three interaction features; carried for approach order-flow features.
    side: str | None = None

    def price_points(self, tick_size: float) -> float:
        return self.price_ticks * tick_size


@dataclass(frozen=True, slots=True)
class Quote:
    """Top-of-book (L0/L1) quote. Deeper book levels are intentionally absent."""

    event_ts_utc: datetime
    bid_price_ticks: int
    ask_price_ticks: int
    bid_size: int = 0
    ask_size: int = 0

    def mid_points(self, tick_size: float) -> float:
        return (self.bid_price_ticks + self.ask_price_ticks) / 2 * tick_size

    def spread_points(self, tick_size: float) -> float:
        return (self.ask_price_ticks - self.bid_price_ticks) * tick_size
```

The aggregated **bar** structure (the canonical bar/candle), verbatim:

`C:/Users/gonza/Documents/Strategy-Core/src/strategy_core/types.py` lines 90–122:
```python
@dataclass(frozen=True, slots=True)
class Bar:
    """An aggregated tick/time/volume bar. Prices in integer ticks.

    Field layout mirrors Trade-Lab's ``Candle`` so the promoted candle builders
    can emit this type unchanged.
    """

    timeframe_ticks: int
    trading_day: date
    bar_index: int
    bar_id: str
    open_ts_utc: datetime
    close_ts_utc: datetime
    open_ticks: int
    high_ticks: int
    low_ticks: int
    close_ticks: int
    volume: int
    trade_count: int
    is_complete: bool
    is_partial: bool
    close_reason: CloseReason | None = None

    def high_points(self, tick_size: float) -> float:
        return self.high_ticks * tick_size

    def low_points(self, tick_size: float) -> float:
        return self.low_ticks * tick_size

    def close_points(self, tick_size: float) -> float:
        return self.close_ticks * tick_size
```

Field dtypes: `timeframe_ticks`/`bar_index`/`open_ticks`/`high_ticks`/`low_ticks`/`close_ticks`/`volume`/`trade_count` are `int`; `trading_day` is `datetime.date`; `open_ts_utc`/`close_ts_utc` are tz-aware `datetime`; `bar_id` is `str` formatted `f"{tf}t:{trading_day.isoformat()}:{bar_index}"` (`candles/_ids.py:19-27`); `is_complete`/`is_partial` are `bool`; `close_reason` is the `CloseReason` StrEnum (`"complete"` / `"end_of_day"`, `types.py:51-55`). `bar_id` is the join key reconciling the streaming and batch builders.

The tick unit is exact-integer arithmetic. `DEFAULT_TICK_SIZE = 0.25` and `BAR_PRICE_SOURCE = "trade_price"` (OHLC built from the trade print, action='T', not a book mid) — `C:/Users/gonza/Documents/Strategy-Core/src/strategy_core/constants.py:28,36`. Default tick count `DEFAULT_TICK_COUNT = 147` (constants.py:41).

Timezone handling is centralized in the `SessionScheme` type (`types.py:193-212`): `timezone` (IANA name), `trading_day_boundary` (local wall-clock rollover, CME 18:00 ET), `sessions` map, optional `closed_window`. The production scheme is `RESEARCH_SESSION_SCHEME` (`US/Eastern`, 18:00 boundary, asia/london/ny windows, `closed_window=None`) at `constants.py:170-179`. A non-canonical Chicago scheme `TRADE_LAB_CT_SESSION_SCHEME` is retained for documented divergence (`constants.py:185-194`).

### 4.2 Trade-Lab and Quant-Lab equivalents of the bar/candle

- **Trade-Lab domain `Candle`** — IMPLEMENTED. `C:/Users/gonza/Documents/Trade-Lab/backend/src/trade_lab/domain/candles.py:16-32` defines `Candle` with the identical field layout to `Bar` (the Strategy-Core docstring states `Bar` "mirrors Trade-Lab's `Candle`"); `CandleCloseReason` (`candles.py:11-13`) and `make_bar_id` (`candles.py:195-196`) match the Strategy-Core ports verbatim. Trade-Lab's input event is `TradeEvent` (`domain/events.py:54-80`): `event_ts_utc`, `receive_ts_utc`, `instrument_id`, `requested_symbol`, `raw_symbol`, `price_ticks:int`, `size:int`, `side:TradeSide`, with `__post_init__` enforcing tz-aware UTC via `ensure_utc` and integer ticks. NQ tick math lives in `domain/prices.py` (`NQ_TICK_SIZE = Decimal("0.25")`, `NQ_POINT_VALUE = Decimal("20")`; floats rejected on the data-quality boundary).
- **Trade-Lab API candle DTO (`BarDTO`)** — IMPLEMENTED. `C:/Users/gonza/Documents/Trade-Lab/backend/src/trade_lab/api/dto.py:45-60`:
```python
class BarDTO(ApiModel):
    timeframe_ticks: int
    trading_day: date
    bar_index: int
    bar_id: str
    open_ts_utc: datetime
    close_ts_utc: datetime
    open_ticks: int
    high_ticks: int
    low_ticks: int
    close_ticks: int
    volume: int
    trade_count: int
    is_complete: bool
    is_partial: bool
    close_reason: str | None
```
`bar_to_dto` (dto.py:222-239) maps `Candle` → `BarDTO` (close_reason serialized to its `.value` string); `bars_payload` (dto.py:385) wraps tuples for the wire.
- **Quant-Lab `OHLCVBar`** (research/dashboard pipeline) — IMPLEMENTED, but a DIFFERENT, points-based shape. `C:/Users/gonza/Documents/Claude-Quant-Lab/src/alpha_lab/dashboard/pipeline/price_buffer.py:39-48`: `timestamp:datetime`, `open/high/low/close:Decimal`, `volume:int` — prices are `Decimal` points (no integer ticks, no `bar_id`/`trading_day`/`bar_index`/`is_complete`), so it is NOT byte-aligned with the Strategy-Core `Bar`/Trade-Lab `Candle` line.

### 4.3 Multi-timeframe representation and how it is fed to a strategy

**Strategy-Core: multiple TICK timeframes concurrently — IMPLEMENTED; minute/hour bars — NOT FOUND.** The engine's timeframes are **tick counts**, not minute/hour intervals. The streaming `CandleEngine` builds all configured tick bars concurrently from one trade stream:

`C:/Users/gonza/Documents/Strategy-Core/src/strategy_core/candles/streaming.py:100-105` — constructor default `timeframes: tuple[int, ...] = (147, 987, 2000)`; it keeps one `_MutableCandle` per timeframe in `self._current: dict[int, _MutableCandle]` and folds each trade into every timeframe (streaming.py:139-183). A bar closes COMPLETE when `trade_count == timeframe`; the `timeframe == 1` special case emits immediately; a trading-day rollover freezes the open bar END_OF_DAY (incomplete). The vectorized `build_tick_bars_from_frame` (`candles/batch.py:38-193`) produces a byte-identical `Bar` sequence (`bar_index = cumcount() // timeframe` per trading day), parity-locked by `tests/test_candle_parity.py`.

So the engine can serve e.g. 147t + 987t + 2000t **simultaneously**, but there is **no native 1-minute / 1H / 4H bar in Strategy-Core** — all bars are N-trade (tick-count) bars. The constants explicitly describe "(147, 987, 2000) tick bars concurrently; 147 is the one the decision layer touches" (`constants.py:38-41`).

How they feed the strategy: `StrategyRuntime` (`C:/Users/gonza/Documents/Strategy-Core/src/strategy_core/runtime/state.py:170-289`) wraps the `CandleEngine` with `timeframes=(147,987,2000)` and a `decision_timeframe` (defaults to `min(timeframes)`, i.e. 147 — state.py:176-188). On each trade it calls `candles.process_trade`, retains all completed bars across all timeframes in `_recent_closed_bars`, but runs the decision layer (`detect_touches`) **only on bars whose `bar.timeframe_ticks == self.decision_timeframe`** (state.py:271-275). So multiple tick timeframes are built and emitted for display, but the decision/touch logic consumes a **single** timeframe at a time, not a 1m+1H+4H multi-timeframe confluence.

**Trade-Lab** mirrors this: configured tick timeframes `tick_timeframes: tuple[int, ...] = (147, 987, 2000)` (`backend/src/trade_lab/config.py:58`); the Trade-Lab `CandleEngine` (`domain/candles.py:103-192`) builds them concurrently and `services/seed.py:42-131` warms up the same tuple vectorized for display.

**Quant-Lab dashboard pipeline (separate research path): time-based multi-timeframe IS supported via resampling — IMPLEMENTED (but distinct from the Strategy-Core engine).** `price_buffer.PriceBuffer` supports both tick-count timeframes and true time bars. `C:/Users/gonza/Documents/Claude-Quant-Lab/src/alpha_lab/dashboard/pipeline/price_buffer.py:20-36`:
```python
_TICK_COUNTS: dict[str, int] = {
    "987t": 987,
    "2000t": 2000,
}

_TIMEFRAME_MAP: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "3m": timedelta(minutes=3),
    "5m": timedelta(minutes=5),
    "10m": timedelta(minutes=10),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1H": timedelta(hours=1),
    "4H": timedelta(hours=4),
    "1D": timedelta(days=1),
}
```
`get_ohlcv(timeframe, since)` (price_buffer.py:140-180) routes tick-count strings to `_build_tick_bars`, and time strings to: build live 1m bars from trades, merge with historical 1m bars, then `_aggregate_bars(merged_1m, td)` rolls 1m up to the requested timeframe (price_buffer.py:283+). So **1m + 1H + 4H simultaneously is possible in the Quant-Lab dashboard buffer by resampling 1m bars** — this is a research/UI buffer, NOT the Strategy-Core production engine. Quant-Lab also has the streaming `TickBarBuilder` (`dashboard/pipeline/tick_bar_builder.py`) keyed by `f"{tc}t"` accumulators (default `[987, 2000]`), an OHLCVBar-on-Decimal path independent of the integer-tick engine.

### 4.4 Data loaders / sources and resampling logic

**Strategy-Core (the production engine boundary):**
- **Parquet replay loader — IMPLEMENTED.** `C:/Users/gonza/Documents/Strategy-Core/src/strategy_core/data/databento_parquet.py` — `DatabentoParquetSource` (dataclass, parquet paths + `requested_symbol` + `schema`). `.events()` k-way merges multiple parquet files into one chronological `Iterator[Trade | Quote | DataQualityWarning]` (heap-merge, databento_parquet.py:74-128). `_scan_file` (130-189) validates schema (`trades`/`mbp-1`/`mbp-10` and aliases), reads via `pyarrow.parquet.iter_batches`. `_normalize_trade` (199-213) converts `price → integer ticks` strictly via `Decimal` (rejecting off-grid prices, `_price_ticks` 305-315), parses `ts_event` to UTC (`_timestamp` 281-303, ns-int / iso-str / datetime), and builds the canonical sort key `(ts, sequence, side_signed_price_ticks, size)`. Front-month filtering and spread-symbol exclusion at 236-256.
- **Live loader — IMPLEMENTED.** `C:/Users/gonza/Documents/Strategy-Core/src/strategy_core/data/databento_live.py` — `DatabentoLiveSource` (class, line 61) with `normalize_provider_message(...)` (line 22) converting provider messages into neutral `Trade`/`Quote` by schema; unsupported schemas raise.
- **Canonical event ordering — IMPLEMENTED.** `C:/Users/gonza/Documents/Strategy-Core/src/strategy_core/data/ordering.py` — `canonical_event_sort_key` / `side_signed_price_ticks` / `sort_events` implement the v3 order `(ts_event, sequence, side_signed_price, size)` (buy sweeps ascending, sell sweeps descending). Provider feeds deliver events in this order so the streaming engine never re-sorts.
- **No resampler in Strategy-Core.** There is no minute/hour resampling code in the engine; bars are tick-count aggregations only (batch.py / streaming.py).

**Quant-Lab (research + DuckDB tick store):**
- **DuckDB tick store — IMPLEMENTED.** `C:/Users/gonza/Documents/Claude-Quant-Lab/src/alpha_lab/agents/data_infra/tick_store.py` — `TickStore` over date/symbol-partitioned parquet (`{data_dir}/{symbol}/{date}/{mbp10|mbp1|trades}.parquet`, tick_store.py:33-38). Look-ahead-safe queries with hard `end` wall and front-month filter (`symbol NOT LIKE '%-%'`). Trading-day SQL is DST-aware ET: `_SQL_TRADING_DAY = "CAST((ts_event AT TIME ZONE 'America/New_York') + INTERVAL 6 HOUR AS DATE)"` (tick_store.py:48), matching `strategy_core...trading_day_for`.
  - **Time-bar resampler — IMPLEMENTED.** `build_bars_from_ticks(symbol, start, end, bar_size="5 minutes")` uses DuckDB `time_bucket(INTERVAL '{bar_size}', ts_event)` over top-of-book mid (or raw price) → OHLCV with a `DatetimeIndex` (tick_store.py:416-508). `query_ohlcv` reads pre-computed `ohlcv_{tf}.parquet` (tick_store.py:382-414).
  - **Tick-bar builder — IMPLEMENTED, parity-locked to Strategy-Core.** `build_tick_bars(symbol, start, end, tick_count=987, price_source="trade")` buckets via `ROW_NUMBER() ... PARTITION BY trading_day ORDER BY {order_clause}`, `bar_index = rn // tick_count`, OHLC+volume+trade_count, `is_complete = trade_count == tick_count` (tick_store.py:510-595). `_tick_event_selection` (597-736) routes `price_source` (`"trade"` filters `action='T'`, side-signed price order with the phase-4f ns ordering key; `"book_mid"` legacy `(bid+ask)/2`). `query_tick_events` (738-766) returns the exact ordered print stream that, fed to the streaming `CandleEngine`, reproduces these bars byte-for-byte. Feature-row loader `query_tick_feature_rows` (273-380) routes `price_source` and projects MBP-depth columns.
  - **Replay iterator — IMPLEMENTED.** `replay(symbol, start, end, step)` yields chronological tick batches with no future leakage (tick_store.py:770-791).
- **Live/research streaming bar buffers — IMPLEMENTED** (`price_buffer.py`, `tick_bar_builder.py` as in 4.3).

**Trade-Lab loaders/adapters — IMPLEMENTED.** `C:/Users/gonza/Documents/Trade-Lab/backend/src/trade_lab/adapters/` has `databento.py`, `databento_historical.py`, `historical_parquet.py`, `replay_catalog.py`, `synthetic_replay.py`. The warm-up loader `HistoricalSeedService` + `build_tick_bars_from_frame` (`services/seed.py`) pulls front-month trades via `DatabentoHistoricalSource.trades_frame(start, end)` and builds display tick bars vectorized; its docstring marks it a "legacy warm-up helper for display context only; authoritative live/replay runtime bars, sessions, levels, and touches are produced by Strategy-Core" (seed.py:10-13). It uses the Chicago session boundaries (`CT`, `_SESSION_OPEN_SOD = 18*3600`, `_SESSION_CLOSE_SOD = 16*3600`) — i.e. the seed path still localizes to America/Chicago, distinct from the engine's `RESEARCH_SESSION_SCHEME` ET clock.

---

## 5. Quant-Lab

Survey of `C:/Users/gonza/Documents/Claude-Quant-Lab` (package `alpha_lab`, layout `src/alpha_lab`, scripts under `scripts/`). There are two distinct ML pipelines in the code: a legacy **extrema rebound/crossing** pipeline (tick-level peak finding) and the production **dashboard_utility** pipeline (level-touch 3-class classification). The latter is what feeds Strategy-Core / Trade-Lab.

### 5.1 End-to-end pipeline structure — IMPLEMENTED

Two coexisting modes, selected by `MLPipelineConfig.training_mode` (`config.py:353-356`): `"extrema_rebound_crossing"` and `"dashboard_utility"`.

**Entry points (scripts/):**
- `scripts/ml_training_tab.py` — the PRIMARY training workflow (a Streamlit-style tab; ~2500 lines). Orchestrates build → purged walk-forward train → evaluate → emit artifacts. Calls `build_utility_dataset` (`ml_training_tab.py:2037-2041`), `ExtremaModelTrainer` (`:178, :287, :354, :563, :1424`), `build_strategy_contract` (`:1583-1592`), and writes `evaluation.json` / `strategy.json` (`:1552, :1594`).
- `scripts/train_dashboard_model.py` — labelled in its own docstring as the "retained secondary export path for ML-Trading-Dashboard" (`train_dashboard_model.py:11-12`); trains the canonical 3-feature `.cbm` from `data/experiment/feature_matrix.parquet` with its own purged walk-forward (`:57-113`).
- `scripts/run_pipeline.py` — a separate multi-agent demo runner (`DATA -> SIG -> VAL -> EXEC -> MON`, `run_pipeline.py:5`), not the ML training path.
- Other scripts: `dashboard.py`, `experiment_tab.py`, `run_backtest.py`, `run_replay.py`, `honest_edge_audit.py`, `decision_repoint_proof.py`, plus audit dirs `audit_NQ_20260602/`, `v3_verify/`, `levels_probe/`.

**`src` ML packages** (`src/alpha_lab/agents/data_infra/ml/`):
- `config.py` — Pydantic config for every stage.
- **dashboard_utility path:** `dashboard_utility_builder.py` (bars → levels → touches → label → features orchestrator, `build_utility_dataset`), `dashboard_utility_labeling.py` (legacy 3-class labeler), `engine_decision.py` (the Strategy-Core adapter; see 5.2).
- **extrema path:** `dataset_builder.py` (`ExtremaDatasetBuilder`), `extrema_detection.py` (`detect_extrema` via `scipy.signal.find_peaks`, `extrema_detection.py:37-42`), `labeling.py` (rebound/crossing), `features_microstructure.py`, `features_momentum.py`, `features_signals.py`.
- **shared train/eval:** `walk_forward.py`, `model_trainer.py`, `model_evaluator.py`, `strategy_contract.py`.
- `ml_export.py` (actually at `data_infra/ml_export.py`, one directory ABOVE this `ml/` package) — a separate `MLDatasetBuilder` producing bar+orderbook features with forward-return labels (`fwd_ret_*`); point-in-time bar feature matrix, not used by the dashboard_utility contract path.

Per-date stages of the production builder are enumerated in its docstring (`dashboard_utility_builder.py:7-14`): build bars → compute key levels from prior-date session highs/lows → detect touch events → label TP/SL → compute 3 interaction features → optional 27 approach features. Per-date results are cached to `{symbol}/{date}/ml_utility_{cache_tag}.parquet` (`:111-163`), where `cache_tag` is `MLPipelineConfig.dataset_config_hash()`.

### 5.2 Consumption of Strategy-Core — IMPLEMENTED

The dashboard_utility decision layer is single-sourced onto `strategy_core` via the adapter `engine_decision.py`. Toggled by the `use_engine: bool = True` flag threaded through `build_utility_dataset` → `_process_single_date` (`dashboard_utility_builder.py:70, 244, 281-293`). When True (default), `_process_single_date` delegates to `process_single_date_engine`; when False the legacy CQL decision code runs (kept for the parity diff).

Engine symbols imported and called (`engine_decision.py:45-71`): `build_zones`, `detect_touches`, `resolve_outcome`, `resolve_honest_outcome` (+ `HonestEntryDrop`), `classify_session`, `make_bar_id`, the 3 interaction-feature functions `int_time_beyond_level` / `int_time_within_2pts` / `int_absorption_ratio`, the 3 approach-feature functions `app_avg_trade_size` / `app_large_trade_vol_pct` / `app_max_spread`, neutral types `Bar`/`Level`/`Quote`/`Trade`/`Side`/`CloseReason`, and constants `DEFAULT_TICK_SIZE` / `RTH_END` / `DECISION_OFFSET_MINUTES`. The production decision call is `resolve_honest_outcome(...)` (`engine_decision.py:255-264`); the book-mid regression path calls `resolve_outcome(...)` (`:275-283`).

Other consumers: `config.py:404-405` imports `ENGINE_VERSION`, `BAR_PRICE_SOURCE`, `LABEL_ENTRY_REFERENCE` into the cache hash; `dashboard_utility_builder.py:32-40` single-sources `RESEARCH_SESSION_SCHEME`, `TRADING_DAY_BOUNDARY`, `ZONE_PROXIMITY_PTS`; `strategy_contract.py:38-39` imports `CONTRACT_VERSION`, `ENGINE_VERSION`, and `constants as k`. Production defaults are stated at `engine_decision.py:179-181` (`price_source="trade"`, `tick_size=TRADE_TICK=0.25`, `honest_entry=True`); `BOOK_MID_TICK=0.125` is kept reachable for the phase-5 parity regression (`:82-87`).

### 5.3 Dataset / label format — IMPLEMENTED

**Production (dashboard_utility) row schema** — emitted per touch in `process_single_date_engine`. Verbatim:

`C:/Users/gonza/Documents/Claude-Quant-Lab/src/alpha_lab/agents/data_infra/ml/engine_decision.py` lines 309-324:
```python
            row = {
                "event_ts": bar_ts_et,
                "date": date_str,
                "timestamp": bar_ts_et,
                "session": session_info.session,
                "decision_time": decision_time_et,
                "label_window_end": label_window_end,
                "direction": touch.direction.value,
                "representative_price": touch.representative_price,
                "level_type": touch.level_type,
                "label": outcome.label,
                "label_encoded": outcome.label_encoded,
                "max_mfe": outcome.max_mfe,
                "max_mae": outcome.max_mae,
            }
            row.update(features)
```
`features` adds `int_time_beyond_level`, `int_time_within_2pts`, `int_absorption_ratio` (`:456-460`); optional approach features add `app_avg_trade_size`, `app_large_trade_vol_pct`, `app_max_spread` (`:518-524`). The legacy CQL path emits the same core columns (`dashboard_utility_builder.py:328-340`).

**3-class label definitions** — verbatim docstring + encoding, `C:/Users/gonza/Documents/Claude-Quant-Lab/src/alpha_lab/agents/data_infra/ml/dashboard_utility_labeling.py` lines 1-13 and 26-40:
```python
"""
Dashboard-utility labeling for level-touch events.

Assigns 3-class labels aligned to the dashboard execution semantics:
- tradeable_reversal (0): MFE >= tp_points before MAE >= sl_points
- trap_reversal (1):      MAE >= sl_points and MFE >= trap_mfe_min
- aggressive_blowthrough (2): MAE >= sl_points and MFE < trap_mfe_min

Resolution order: MAE checked first (conservative), matching both
Quant-Lab experiment/labeling.py and Trading-Dashboard outcome_tracker.py.

Entry reference: representative_price (level price), matching training labels.
"""
```
```python
TRADEABLE_REVERSAL = "tradeable_reversal"
TRAP_REVERSAL = "trap_reversal"
AGGRESSIVE_BLOWTHROUGH = "aggressive_blowthrough"
NO_RESOLUTION = "no_resolution"

LABEL_ENCODING = {
    TRADEABLE_REVERSAL: 0,
    TRAP_REVERSAL: 1,
    AGGRESSIVE_BLOWTHROUGH: 2,
}

CLASS_NAMES = {v: k for k, v in LABEL_ENCODING.items()}

RTH_END = time(16, 15)
_ET = "US/Eastern"
```
NOTE on entry reference: this legacy labeler doc says entry = level price, but the engine production path re-anchors entry to the realistic trade price at the decision instant (`engine_decision.py:255-267`, `resolve_honest_outcome`), and the contract records `entry_reference = "realistic_at_decision"` (strategy.json:92). There is no single `is_long` column — direction is a string `"LONG"/"SHORT"` (`dashboard_utility_builder.py:610`, `engine_decision.py:316`).

**Legacy extrema label schema** — separate format, `labeling.py:184-208`: columns `tick_index, timestamp, price, extremum_type, prominence, width, reversal_ticks, continuation_ticks, label_Nt` (rebound=1/crossing=0/None per threshold).

**Storage:** datasets are Parquet. Per-date utility caches `ml_utility_{hash}.parquet`; experiment matrix at `data/experiment/feature_matrix.parquet`; tick source via DuckDB through `TickStore` (`engine_decision.py:341-378` shows raw DuckDB `SELECT ... action='T'` front-month trade queries). No single declared columnar DDL/Arrow schema file exists — the schema is the row dict above.

### 5.4 Feature-extraction layer — IMPLEMENTED

Two layers exist.

**Production interaction/approach features** (live-computable, MBP-1 + trades). The canonical feature sets are declared in `config.py:17-35` (`LIVE_APPROACH_FEATURES` 8 names, `LIVE_INTERACTION_FEATURES` 3 names, `LIVE_ALL_FEATURES`). The 3 interaction features are computed over the post-touch window in `compute_interaction_features_engine` (`engine_decision.py:381-460`) by feeding engine `Trade` objects into `int_time_beyond_level` / `int_time_within_2pts` / `int_absorption_ratio`. Approach features via `compute_approach_features_engine` (`engine_decision.py:463-524`) — only the 3 engine-implemented scalars (`app_avg_trade_size`, `app_large_trade_vol_pct`, `app_max_spread`); the docstring notes acceleration/imbalance/volatility "have no engine formula yet" (`:474-476`). A legacy non-engine twin `_compute_interaction_features` (manual numpy loop) exists at `dashboard_utility_builder.py:627-715`, and `_compute_approach_features` (`:718-789`) routes 27 raw features through `alpha_lab.experiment.features._query_approach_features` then filters to `LIVE_APPROACH_FEATURES`.

**Legacy extrema feature extractors** (3 families, combined in `dataset_builder.py:170-222`): `extract_pl_features` (PL/MBP-10 order-book microstructure, `features_microstructure.py:22-59`, cites Sokolovsky & Arnaboldi 2020), `extract_ms_features` (momentum/RSI, `features_momentum.py`), and `extract_signal_features_batch` (detector outputs, `features_signals.py:60`, consuming `SignalBundle`/`SignalVector` from `alpha_lab.core.contracts`).

Interaction features (the at-level vs through-level absorption ratio) are the closest thing to an explicit "interaction feature" layer; there is no generic cross-product feature-interaction module.

### 5.5 Walk-forward / CV scaffolding (purging/embargo) — IMPLEMENTED (purge: yes; embargo: gap-day only)

Two implementations.

**`WalkForwardSplitter`** (`walk_forward.py:37-166`) — rolling or expanding (`WalkForwardConfig.expanding`), config `train_days=60, test_days=20, gap_days=1` (`config.py:142-163`). The `gap_days` field is documented as "Gap between train and test to prevent leakage" (`config.py:155-159`); this is the embargo mechanism. Exposes `as_sklearn_cv()` for RFECV (`walk_forward.py:146-161`).

**Explicit label-leakage purging** in the training tab — `purge_training_indices_for_label_leakage` (`ml_training_tab.py:1216+`). It prefers the exact `label_window_end` column when present (purge rule `label_window_end < test_start`, `:1234-1252`), falls back to a derived dashboard-utility horizon (`_derive_dashboard_utility_label_window_end`, `:1154`), and finally to a `forward_window` tick heuristic (`fw_minutes = max(5, forward_window // 500)`, `:1294-1306`). Applied per fold at `:327-337`; the parquet emit records `n_purged_total`, `label_purge`, and `used_label_window_end` (`evaluation.json:272` shows `n_purged_total: 0`; the v3 run used `label_window_end` exclusively).

`train_dashboard_model.py` has its own simpler purged splitter `create_purged_walk_forward_folds` (`:57-113`) with `PURGE_DAYS=2` and `has_time=True` CatBoost.

No separate combinatorial-purged-CV or distinct forward-embargo-after-test module was found — embargo is realized as the train/test `gap_days` plus the label-horizon purge.

### 5.6 Model training & artifact format — IMPLEMENTED (framework: CatBoost)

**Framework:** CatBoost only. `ModelConfig` (`config.py:166-210`): `model_type="catboost"`, `iterations=1000`, `depth=6`, `learning_rate=0.03`, `loss_function="Logloss"` (default; `"MultiClass"` for the 3-class dashboard model), `auto_class_weights="Balanced"`, `rfecv_enabled=True`. `ExtremaModelTrainer._build_classifier` constructs `CatBoostClassifier(... random_seed=42, allow_writing_files=False)` (`model_trainer.py:139-161`). Optional RFECV feature selection over walk-forward CV splits (`_run_rfecv`, `:101-137`, scoring `precision_weighted` for MultiClass). Final model fit once on full selected data (`:77-99`). Evaluation via `ModelEvaluator` (`model_evaluator.py`): precision/F1/ROC-AUC, block-bootstrap CIs, permutation test, Cohen's d.

**Saved bundle format** — `ExtremaModelTrainer.save_model` (`model_trainer.py:163-187`): writes `model.cbm` plus `metadata.json` (`selected_features`, `feature_importances`, `train_metrics`, `config`). The training tab additionally writes `evaluation.json` (`ml_training_tab.py:1442-1552`) and `strategy.json` (`:1583-1597`). Confirmed on disk for `models/NQ_20260603_233847/`: `model.cbm` (302 KB), `metadata.json`, `evaluation.json`, `strategy.json`.

**`strategy.json` bundle contract** — emitted by `build_strategy_contract` (`strategy_contract.py:63-220`); every structural field single-sourced from `strategy_core.constants` (no restated literals, per `:22-31`). The actual production artifact `C:/Users/gonza/Documents/Claude-Quant-Lab/models/NQ_20260603_233847/strategy.json` (abbreviated to the contractual blocks):
```json
{
  "contract_version": "trade_lab_contract_v1",
  "engine_version": "strategy_core_engine_v3",
  "strategy_id": "NQ_20260603_233847",
  "training_mode": "dashboard_utility",
  "supported_by_runtime": true,
  "instrument": "NQ",
  "tick_size": 0.25,
  "point_value": 20.0,
  "model": { "type": "catboost", "loss_function": "MultiClass", "file": "model.cbm" },
  "feature_set": {
    "names": [ "int_time_within_2pts", "int_absorption_ratio",
               "app_avg_trade_size", "app_large_trade_vol_pct", "app_max_spread" ],
    "order_is_contractual": true,
    "interaction_features": [ "int_time_within_2pts", "int_absorption_ratio" ],
    "approach_features": [ "app_avg_trade_size", "app_large_trade_vol_pct", "app_max_spread" ],
    "nan_policy": "model_native"
  },
  "class_map": { "0": "tradeable_reversal", "1": "trap_reversal", "2": "aggressive_blowthrough" },
  "label_policy": {
    "resolution": "mae_first",
    "entry_reference": "realistic_at_decision",
    "decision_offset_minutes": 5,
    "tp_points": 15.0, "sl_points": 15.0, "trap_mfe_min": 5.0,
    "forward_bar_type": "147t",
    "forward_cutoff": "17:00_US/Eastern_ny_close",
    "no_resolution_dropped": true
  },
  "inference": { "eligible_class": "tradeable_reversal", "eligible_session": "ny", "confidence_gate": 0.7 },
  "provenance": { "dataset_config_hash": "d8e239c7",
    "catboost": { "iterations": 500, "depth": 4, "learning_rate": 0.03, "auto_class_weights": "Balanced" } }
}
```
NOTE the emitter source hardcodes `"supported_by_runtime": False` (`strategy_contract.py:125`), but the on-disk artifact shows `true` — indicating the saved bundle was produced/patched after activation was unblocked, diverging from the current emitter default.

**`metadata.json`** (`models/NQ_20260603_233847/metadata.json`): `selected_features` (5), `feature_importances`, `train_metrics` (`train_accuracy ≈ 0.690`, `n_train_samples = 652`, `n_selected_features = 5`), and the CatBoost `config` block (`iterations=500, depth=4, loss_function="MultiClass", auto_class_weights="Balanced", rfecv_enabled=false`).

**`evaluation.json`** (same dir): OOS aggregate (`precision ≈ 0.487`, `accuracy ≈ 0.492`, `roc_auc ≈ 0.483`, `permutation_p_value ≈ 0.637`, `cohens_d ≈ -0.05`, `n_samples=565`), 33 fold metrics, threshold/calibration tables, `trade_utility` (`expectancy_15_15_pts = -0.39`), `full_dataset_class_balance` (`tradeable_reversal=322, trap_reversal=187, aggressive_blowthrough=143`), the full `full_config`, and 225 `dates_used` (2025-06-02 → 2026-02-22). The legacy export `train_dashboard_model.py` instead saves a bare `data/models/dashboard_3feature_v1.cbm` (`:51`) with no JSON sidecars.

---

## 6. Trade-Lab

Trade-Lab is the operator-facing dashboard (FastAPI backend + React/Vite frontend) that drives Strategy-Core for both live and historical replay over a single shared runtime, and streams the engine's derived domain deltas to the browser over a versioned WebSocket envelope. Status: **IMPLEMENTED** end-to-end; live Databento streaming is gated behind opt-in config and the optional SDK.

### 6.1 How it drives Strategy-Core (live vs replay wiring)

**Single shared runtime; adapters only know byte provenance.** `ApplicationRuntime` (`C:/Users/gonza/Documents/Trade-Lab/backend/src/trade_lab/services/runtime.py`) composes a `StrategyCoreService` plus Trade-Lab-only state (observations, inference, market-context buffer). All canonical events enter through one method, and only trades advance bars/touches — IMPLEMENTED:

`backend/src/trade_lab/services/runtime.py` lines 390-441:
```python
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
```

`_process_trade` (lines 528-551) appends the trade to the `MarketContextBuffer`, delegates to `self.strategy_core_service.process_market_event(trade)`, refreshes observations, runs inference on completed observations, resolves outcomes on closed bars, and starts new observations from any returned touches — bundling everything into one `RuntimeUpdate`.

**The Strategy-Core seam.** `StrategyCoreService` (`backend/src/trade_lab/services/strategy_core_service.py`) is a thin compatibility adapter that converts Trade-Lab canonical events into `strategy_core` neutral events, feeds `StrategyRuntime`, and maps neutral updates back to Trade-Lab DTO-compat types. It explicitly must not recompute strategy meaning — IMPLEMENTED:

`backend/src/trade_lab/services/strategy_core_service.py` lines 86-148:
```python
class StrategyCoreService:
    """Compatibility adapter around :class:`strategy_core.runtime.StrategyRuntime`."""

    def __init__(
        self,
        *,
        requested_symbol: str | None,
        tick_timeframes: tuple[int, ...],
        recent_closed_bar_limit: int = 500,
        warning_limit: int = 100,
    ) -> None:
        self.requested_symbol = requested_symbol
        self._display_timeframes = tuple(sorted(set(tick_timeframes)))
        self._runtime = StrategyRuntime(
            requested_symbol=requested_symbol,
            timeframes=self._display_timeframes,
            # audit #5: PIN the decision bar to the smallest configured timeframe
            # explicitly instead of relying on StrategyRuntime's implicit min() fallback.
            # ...
            decision_timeframe=min(self._display_timeframes),
            recent_closed_bar_limit=recent_closed_bar_limit,
            warning_limit=warning_limit,
        )
        self._last_trade: TradeEvent | None = None
        self._last_schema: str | None = None
        self._touch_sequence: dict[tuple[date, str], int] = {}
    ...
    def process_market_event(self, event: MarketEvent) -> StrategyCoreUpdate:
        if isinstance(event, TradeEvent):
            self._last_trade = event
            self._last_schema = event.source_schema
            return self._map_update(self._runtime.process_event(_trade_to_core(event)))
        if isinstance(event, TopOfBookEvent):
            self._last_schema = event.source_schema
            quote = _quote_to_core(event)
            if quote is None:
                return StrategyCoreUpdate()
            return self._map_update(self._runtime.process_event(quote))
        return StrategyCoreUpdate()
```

Conversion to Strategy-Core types is done by module-level helpers `_trade_to_core` (maps `TradeSide` to canonical databento codes `B`/`A`/`N`, lines 251-260) and `_quote_to_core` (returns `None` for one-sided quotes, lines 263-272). `_map_update`/`_map_snapshot` (lines 153-217) filter Core bars to the configured display timeframes, map Core `Level`/`Touch`/`FeedStatus` back to Trade-Lab DTO-compat dataclasses, and stamp `strategy_core.ENGINE_VERSION` into metadata. The Core `Touch.direction` is carried straight through (audit #NN-1, lines 43-51, 219-248) rather than re-derived.

**Live controller.** `LiveMarketDataService` (`backend/src/trade_lab/services/live.py`) is an operator-controlled state machine that never auto-starts. On `start()` it resets the runtime, builds the Databento feed via an injected `feed_factory`, and wraps it in a Strategy-Core `LiveRuntime` whose callbacks route every item back through the shared runtime — IMPLEMENTED:

`backend/src/trade_lab/services/live.py` lines 196-205:
```python
                core_live = CoreLiveRuntime(
                    feed,
                    process_item=self._process_live_item,
                    on_update=self._emit,
                    is_warning=lambda item: isinstance(item, DataQualityWarning),
                    is_event=lambda item: not isinstance(item, (DataQualityWarning, FeedStatus)),
                    event_timestamp=lambda item: getattr(item, "event_ts_utc", None),
                )
                self.strategy_core_live = core_live
                await core_live.start()
```

`_process_live_item` (lines 262-267) dispatches `FeedStatus` to `runtime.set_feed_status`, `DataQualityWarning` to `runtime.record_warning`, and everything else to `runtime.process_market_event`. A warm-up seed task runs off-loop via `_seed_and_broadcast` with a `_generation` token guarding against a stale fetch seeding a freshly reset runtime (audit #NN-5, lines 302-341).

**Replay controller.** `HistoricalReplayService` (`backend/src/trade_lab/services/replay.py`) uses the same runtime path. It wraps a Trade-Lab `HistoricalMarketDataSource` in `_StrategyCoreHistoricalSourceAdapter` (lines 36-50) and drives a Strategy-Core `ReplayRuntime` — IMPLEMENTED:

`backend/src/trade_lab/services/replay.py` lines 207-218:
```python
        adapter = _StrategyCoreHistoricalSourceAdapter(source, config)
        self.strategy_core_replay = CoreReplayRuntime(
            None,
            adapter,
            process_item=self._process_replay_item,
            on_update=self._on_strategy_core_replay_update,
            is_warning=lambda item: isinstance(item, DataQualityWarning),
            event_timestamp=lambda item: getattr(item, "event_ts_utc", None),
            on_timestamp_regression=self._timestamp_regression_update,
        )
        self._state = ReplayState.READY
        self._task = asyncio.create_task(self._run_strategy_core_replay(config))
```

`_process_replay_item` (lines 252-255) routes warnings to `runtime.record_warning` and events to `runtime.process_market_event` — the identical sink the live path uses. Replay additionally coalesces per-trade deltas into at most one broadcast per `_REPLAY_FLUSH_INTERVAL_SECONDS = 0.05` (`_accumulate`/`_maybe_flush`/`_flush_pending`, lines 363-392) via `_coalesce_replay_updates` (lines 406-458), which keeps snapshot-style fields latest-wins and concatenates event-style fields (closed_bars, touches, observations, predictions, outcomes) so no closed bar or resolved outcome is dropped.

**Mutual exclusion.** Both services share one `ApplicationRuntime`; `create_app` (`backend/src/trade_lab/api/app.py`) wires both update callbacks to the same broadcaster (lines 239-290) and the start endpoints refuse to start one while the other is active (audit #NN-2, lines 178-189, 333-337, 434-437) because either `start()` resets the engine.

### 6.2 Data feed/source — live (Databento SDK) vs replay (parquet / synthetic)

**Live: Databento SDK adapter.** `DatabentoMarketDataFeed` (`backend/src/trade_lab/adapters/databento.py`) implements the `MarketDataFeed` port. Importing the module never connects; the SDK is imported lazily and isolated behind `_DatabentoSdkFacade` so tests use fakes (lines 73-130). Subscribed schemas are trades, MBP-1/CMBP-1 top-of-book, definition, status, statistics; the SDK callback (`_provider_callback`, lines 275-289) runs on SDK threads and only enqueues onto a bounded `asyncio.Queue` (overflow drops newest and raises a `BACKPRESSURE_DROP` warning). `events()` (lines 220-269) normalizes queued records via `normalize_provider_message` (lines 322-385), which is the single point where only trades become `TradeEvent` and quotes become `TopOfBookEvent`. SDK availability is detected by import metadata only — IMPLEMENTED:

`backend/src/trade_lab/adapters/databento.py` lines 54-60:
```python
def is_databento_sdk_available() -> bool:
    """Return whether the optional Databento SDK appears importable.

    This uses import metadata only; it never creates a client or connects.
    """

    return importlib.util.find_spec("databento") is not None
```

The feed factory is built in `create_app` (`backend/src/trade_lab/api/app.py` lines 256-267) only when no `live` is injected, pulling `api_key`, `dataset`, `stype_in`, and schemas from `Settings`.

**Live warm-up seeding: Databento Historical API.** `DatabentoHistoricalSource` (`backend/src/trade_lab/adapters/databento_historical.py`) fetches front-month L0 trade prints as a pandas DataFrame (`DBNStore.to_df`) for chart warm-up, clamping `end` to the dataset's available range to avoid 422 errors (lines 88-108). `HistoricalSeedService` (`backend/src/trade_lab/services/seed.py`) builds tick bars vectorized off the event loop; these are display-only context (the docstring states authoritative bars/levels/touches come from Strategy-Core, lines 10-12) and are stored apart from the live buffer via `runtime.seed_closed_bars` (`runtime.py` lines 443-469).

**Replay: historical parquet.** `HistoricalParquetAdapter` (aliased `HistoricalParquetSource`, `backend/src/trade_lab/adapters/historical_parquet.py`) implements `HistoricalMarketDataSource.scan`. It projects only live-contract columns and ignores historical-only depth fields (`HISTORICAL_ONLY_PREFIXES`, lines 71, 522-526), supports `trades`/`mbp-1`/`mbp-10` schemas, k-way merges multiple files by `event_ts_utc` (lines 128-170), and has an opt-in `front_month_only` filter that drops spread/back-month symbols by picking the dominant outright via vectorized PyArrow value counts (lines 394-452) — the multi-instrument contamination guard. The replay catalog instantiates this source with `front_month_only=True` (`adapters/replay_catalog.py` lines 389-391).

**Replay: synthetic.** `SyntheticNqDemoSource` (`backend/src/trade_lab/adapters/synthetic_replay.py`) is a deterministic in-memory NQ stream (180 Asia trades creating a stable high, then 2,120 London trades revisiting it) emitting canonical `TradeEvent`s through the same runtime, registered under the opaque id `synthetic:nq-demo` (lines 95-104). Source ids are opaque allowlist names, never caller paths (module docstring lines 1-7).

**The port both honor** — IMPLEMENTED, `backend/src/trade_lab/ports/market_data.py` lines 18-39:
```python
class MarketDataFeed(Protocol):
    """Async live/replay feed emitting canonical events and status updates."""

    async def events(self) -> AsyncIterator[MarketEvent | DataQualityWarning | FeedStatus]: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class HistoricalMarketDataSource(Protocol):
    """Read-only source for backfill/replay scans over canonical events."""

    def scan(
        self,
        paths: Iterable[Path],
        *,
        requested_symbol: str,
        schema: str,
        start_ts_utc: datetime | None = None,
        end_ts_utc: datetime | None = None,
    ) -> Iterator[MarketEvent | DataQualityWarning]: ...
```

### 6.3 What it renders, and how engine state/events reach the UI

**WebSocket broadcaster + DTO envelopes.** `WebSocketBroadcaster` (`backend/src/trade_lab/services/broadcaster.py`) is the fan-out. Raw ticks are never broadcast — clients get domain deltas only (docstring lines 1-6). On connect it sends a `system.snapshot` then a `system.heartbeat` (lines 55-63). `messages_for_update` (lines 77-112) maps each populated field of a `RuntimeUpdate` to a typed envelope:

`backend/src/trade_lab/services/broadcaster.py` lines 77-112 (excerpt):
```python
    def messages_for_update(self, update: RuntimeUpdate) -> tuple[bytes, ...]:
        messages: list[bytes] = []
        if update.feed_status is not None:
            messages.append(
                self.envelope_bytes("feed.status", feed_status_to_dto(update.feed_status))
            )
        for warning in update.warnings:
            messages.append(self.envelope_bytes("data_quality.warning", warning_to_dto(warning)))
        if update.current_bars:
            messages.append(
                self.envelope_bytes("market.bar.updated", bars_payload(update.current_bars))
            )
        if update.closed_bars:
            messages.append(
                self.envelope_bytes("market.bar.closed", bars_payload(update.closed_bars))
            )
        ...
        for prediction in update.predictions:
            messages.append(
                self.envelope_bytes("prediction.created", prediction_payload(prediction))
            )
        for outcome in update.outcomes:
            messages.append(self.envelope_bytes("prediction.resolved", outcome_payload(outcome)))
        model_status_message = self._model_status_message_if_changed()
```

Per-client queues are bounded (`queue_depth=100`); on overflow the oldest is dropped and a `BACKPRESSURE_DROP` warning is queued (`_put_domain_message`/`_put_backpressure_warning_if_room`, lines 150-182). The WebSocket route `/ws/v1` (`api/app.py` lines 477-501) checks browser Origin allowlist, then runs a `send_loop`. `model.status` is also pushed immediately on REST activate/deactivate (`broadcast_model_status`, lines 138-139).

**Envelope + message contract** — IMPLEMENTED, `backend/src/trade_lab/api/dto.py` lines 24-38, 214-220:
```python
MESSAGE_VERSION = "ws.v1"
MessageType = Literal[
    "system.heartbeat",
    "system.snapshot",
    "market.bar.updated",
    "market.bar.closed",
    "levels.updated",
    "touch.detected",
    "observation.updated",
    "data_quality.warning",
    "feed.status",
    "prediction.created",
    "prediction.resolved",
    "model.status",
]
...
class Envelope(ApiModel):
    version: str = MESSAGE_VERSION
    type: MessageType
    sequence: int
    server_time_utc: datetime
    payload: dict[str, Any]
```

All payloads are Pydantic DTOs (`BarDTO`, `DisplayLevelDTO`, `TouchDTO`, `ObservationDTO`, `PredictionDTO`, `OutcomeDTO`, `ModelStatusDTO`, `FeedStatusDTO`, `DataQualityWarningDTO`, `SnapshotPayload`) with `extra="forbid"`; converters live in the same file (lines 222-445). DTOs exist only at the API boundary so domain dataclasses stay out of the hot path (module docstring lines 1-5).

**Realtime client.** The frontend `RealtimeClient` (`frontend/src/realtime/client.ts`) owns reconnect with bounded exponential backoff (lines 65-71), decodes string/ArrayBuffer/Blob frames (lines 227-254), validates the envelope shape and `ws.v1` version (lines 78-82, 221-225), then `route()` (lines 91-149) switches on `envelope.type` to write each delta into the appropriate store and append a blotter event. `applySnapshot` (lines 151-176) seeds market/intelligence/prediction/runtime stores and annotates predictions with already-resolved outcomes. `applyFeedStatus` (lines 178-204) clears stores on a "runtime reset" message and derives replay/live UI sub-states from the feed message text. The TypeScript message-type/DTO mirror lives in `frontend/src/realtime/types.ts` (lines 1-155).

**Panels/components** (`frontend/src/components/*`, composed by `App.tsx` lines 40-56). All IMPLEMENTED:

- `TopStatusBar.tsx` — header pills for Mode, Session, Trading Day, Feed, API, WS, Heartbeat, Timeframe (lines 12-27).
- `ChartWorkspace.tsx` + `TradingChart.tsx` — the main chart. `ChartWorkspace` selects narrow store slices, builds bars/level-overlays/markers via `normalizeBarsForTimeframe`/`normalizeLevels`/`combineMarkers`, and offers a `[147, 987, 2000]`-tick timeframe selector (lines 7, 23-49). `TradingChart` wraps `lightweight-charts` imperatively, diffing bar data for incremental `series.update` vs full `setData` (`applyBarData`/`shouldReplaceBarData`, lines 88-127) and syncing level/marker overlays through `ChartOverlayManager`.
- `IntelligencePanel.tsx` — Runtime (session/origin/trading day/eligibility), Levels (ordered, eligible vs display), Predictions (predicted class + probabilities + outcome MFE/MAE), Touches, Observations, and Data Quality sections (lines 8-73).
- `EventBlotter.tsx` — collapsible runtime-event log (last 80) from the shared blotter store (lines 10-39).
- `ReplayControls.tsx` — allowlisted source picker + Start/Pause/Resume/Stop, replay metrics, and safe historical diagnostics; enforces safe source ids client-side (`isSafeSourceId`, line 179) and calls the REST replay endpoints (lines 99-124).
- `LiveDataPanel.tsx` — Databento status (dataset, symbol, schemas, API-key/SDK/subscription readiness, events processed) and Start/Stop Live; surfaces the "market-data only, no trading" note and config-disabled reasons (lines 19-58).
- `ModelPanel.tsx` — discovered-bundle dropdown + Activate/Deactivate hot-swap, active-model summary (strategy, instrument, class map, validation, feature names) (lines 63-127).

**State path.** REST status polling (`App.tsx` lines 17-31, every 15 s via `apiClient`) seeds runtime/replay/live stores; the WebSocket then feeds live deltas into the same stores in `frontend/src/state/stores.ts`, which the components subscribe to via the `useMarket`/`useIntelligence`/`usePredictions`/`useRuntime`/`useConnection`/`useReplay`/`useLive`/`useBundles`/`useModelStatus`/`useBlotter` selectors. DTO-to-view normalization is centralized in `frontend/src/domain/normalize.ts`.

---

## 7. Cross-cutting

### Testing

**Suites / counts / directories (per project)**

| Project | Test dirs | Files | Approx. test functions | Runner config |
|---|---|---|---|---|
| Strategy-Core | `tests/`, `validation/` | 18 in `tests/` + 3 in `validation/` | ~135 in `tests/`, 3 in `validation/` | `pyproject.toml` `[tool.pytest.ini_options] testpaths=["tests"]` (`C:/Users/gonza/Documents/Strategy-Core/pyproject.toml:32-34`) |
| Quant-Lab | `tests/agents/`, `tests/core/`, `tests/integration/`, plus `src/alpha_lab/agents/validation/tests/` | 24 under `tests/` | ~727 | `pyproject.toml` `testpaths=["tests"]`, `pythonpath=["src"]`, `addopts="-v --tb=short"` (`C:/Users/gonza/Documents/Claude-Quant-Lab/pyproject.toml:53-56`) |
| Trade-Lab (backend) | `backend/tests/` | 28 | ~348 | `pyproject.toml` `testpaths=["tests"]`, `pythonpath=["src"]`, custom `benchmark` marker (`C:/Users/gonza/Documents/Trade-Lab/backend/pyproject.toml:36-41`); `conftest.py` adds `--run-benchmark` opt-in gate (`C:/Users/gonza/Documents/Trade-Lab/backend/tests/conftest.py:4-19`) |
| Trade-Lab (frontend) | `frontend/src/**` | 16 `*.test.ts(x)` | n/a | Vitest — `"test": "vitest run"` (`C:/Users/gonza/Documents/Trade-Lab/frontend/package.json:11`) |

IMPLEMENTED. (Counts are `def test_`/`async def test_` line counts, so parametrized cases expand at runtime; `catboost_info/test` under Quant-Lab is a CatBoost artifact dir, not a test suite.)

**Parity / golden tests across the three projects** — IMPLEMENTED. These are the explicit zero-drift harnesses:

- **Strategy-Core `validation/test_production_pair_parity.py`** — GATE: research DuckDB side-signed trade bars (`TickStore.build_tick_bars`, imported from Quant-Lab via `sys.path.insert` of `C:/Users/gonza/Documents/Claude-Quant-Lab/src`, lines 34-36) must equal Trade-Lab wire-order `strategy_core.candles.streaming.CandleEngine` bars on the same front-month trade set; asserts OHLC ticks locked, residual is volume-only, ts agree to µs (`:189-224`).
- **Strategy-Core `validation/test_decision_diff.py`** — STANDING decision-layer diff: runs the full `strategy_core` pipeline (levels→zones→touches→honest labels→6 features) on both bar sets via sibling `decision_diff_harness`, asserts identical levels/zones, ≤1 touch diff, identical survive/drop partition, **0 label flips**, feature diffs <1e-6 (`:61-129`).
- **Strategy-Core `validation/test_duckdb_streaming_parity.py`** — DuckDB-batch == streaming under the same deterministic order (the necessary-but-not-production predecessor, cited in `test_production_pair_parity.py:3`).
- **Trade-Lab `backend/tests/test_strategy_core_acceptance.py`** — asserts `trade_lab.services.runtime.ApplicationRuntime` produces the same single touch as a direct `strategy_core.runtime.state.StrategyRuntime` on identical events (`:30-65`), and statically asserts the runtime path uses `StrategyCoreService` and contains **no** legacy `CandleEngine`/`SessionLevelEngine`/`SessionClassifier` (`:68-78`).
- **Trade-Lab `backend/tests/test_engine_version_binding.py`** — fail-closed `engine_version` binding regression: a `strategy.json` declaring an unmatched engine_version is rejected, a matching one loads, legacy (no field) loads unbound (`:1-8`).
- **Quant-Lab `tests/agents/test_decision_repoint_parity.py`** — standing BOOK-MID parity: `engine_decision` (the `strategy_core` repoint) must reproduce the legacy duplicate CQL decision code EXACTLY (zones, touches, labels, 6 features, integrated rows, max abs diff `<1e-9`) when pinned to book-mid mode; a second test pins the NEW production trade-path cutover behavior (`:236-455`). Data-gated via `QUANT_LAB_DATABENTO_DIR`.
- **Quant-Lab `tests/agents/test_strategy_contract_nodrift.py` / `test_strategy_contract_repoint.py`** — prove the contract emitter single-sources every literal from `strategy_core.constants`, carries `engine_version`, round-trips through the shared `strategy_core` loader, and includes a coverage guard that fails if any new `StrategyContract` leaf field has no constant/allow-listed source (`test_strategy_contract_nodrift.py:1-16`).
- **Quant-Lab `scripts/phase8_1_golden.py`** — golden capture/compare: `--mode golden` runs the inline pre-refactor honest-entry orchestration, `--mode engine` routes through `strategy_core.resolve_honest_outcome`, `--mode compare` byte-compares the two JSON captures field-by-field (`:1-18`).
- Strategy-Core also ships `tests/test_candle_parity.py` and `tests/test_contract.py` (the fail-closed schema + `engine_version` binding tests, `test_contract.py:1-9`).

### Strategy registration / discovery

- **A plugin/entry-point strategy registry is NOT FOUND.** No `[project.entry-points]` exists in any of the three `pyproject.toml` files, and no `select:registry`/`@register` strategy registry exists in `strategy_core`. There is exactly **one** strategy — the shared `strategy_core` engine — versioned by `ENGINE_VERSION` (`C:/Users/gonza/Documents/Strategy-Core/src/strategy_core/__init__.py:54`) and selected by config, not registration.
- **Model-bundle discovery (Trade-Lab) — IMPLEMENTED.** `trade_lab.services.model_registry` scans `TRADE_LAB_MODELS_PATH` for subdirectories containing `model.cbm` + `metadata.json` + `strategy.json` and builds opaque `ModelBundle` descriptors; `discover_model_bundles` (`model_registry.py:71-117`) sorts by directory name and skips unsafe ids via `is_safe_model_id` (`:58-68`). `ModelRegistry.activate` (`:238-255`) loads + fail-closed-validates one bundle and atomically hot-swaps it. This is *model* discovery, not strategy registration.
- **Binding mechanism.** The `strategy.json` contract carries `engine_version`; the loader fail-closes when it differs from `strategy_core.ENGINE_VERSION` (`C:/Users/gonza/Documents/Trade-Lab/backend/src/trade_lab/domain/contracts/strategy_contract.py:167-241`).
- **Quant-Lab strategy selection** is via `MLPipelineConfig(training_mode="dashboard_utility", ...)` (`C:/Users/gonza/Documents/Claude-Quant-Lab/tests/agents/test_decision_repoint_parity.py:113-127`; module `alpha_lab/agents/data_infra/ml/config.py`) — config-driven, not a registry.

### Config / settings approach (per project)

- **Trade-Lab (backend) — pydantic-settings.** `trade_lab.config.Settings(BaseSettings)` with `SettingsConfigDict(env_prefix="TRADE_LAB_", env_file=<backend>/.env, extra="ignore")`, `SecretStr` for keys, field validators and allow-lists (`C:/Users/gonza/Documents/Trade-Lab/backend/src/trade_lab/config.py:17-160`). `.env` + `.env.example` present at `backend/`. Frontend has `frontend/.env.example`. IMPLEMENTED.
- **Quant-Lab — three coexisting approaches.** (a) YAML→Pydantic `BaseModel`: `alpha_lab.core.config.load_settings()` merges `config/{settings,instruments,prop_firms,validation_thresholds}.yaml` into a `Settings` model (`C:/Users/gonza/Documents/Claude-Quant-Lab/src/alpha_lab/core/config.py:95-164`); the YAML files exist under `config/`. (b) pydantic-settings `DashboardSettings(BaseSettings)` with `DASHBOARD_` prefix + `.env` (`src/alpha_lab/dashboard/config/settings.py:1-40`). (c) ML pipeline config `alpha_lab/agents/data_infra/ml/config.py` (`MLPipelineConfig`). Root `.env` + `.env.example` present. IMPLEMENTED.
- **Strategy-Core — no settings layer / no `.env`.** All "magic values" are centralized in `strategy_core.constants` as the single source of truth (`C:/Users/gonza/Documents/Strategy-Core/src/strategy_core/constants.py:1-13` docstring); only `pyproject.toml` `[tool.pytest]`/`[tool.ruff]` config exists. NOT FOUND (by design): no pydantic-settings, no YAML, no `.env`.

### Shared library / package

The single shared package is **`strategy-core`** (`name = "strategy-core"`, `C:/Users/gonza/Documents/Strategy-Core/pyproject.toml:6-7`).

- **Trade-Lab depends on it explicitly** via a pinned git URL: `"strategy-core @ git+https://github.com/thealgochef/Strategy-Core.git@fd53e06989084368aa3b89d33eb83bee081b695f"` (`C:/Users/gonza/Documents/Trade-Lab/backend/pyproject.toml:19`, with `allow-direct-references = true`). It imports it directly (e.g. `import strategy_core` for the `ENGINE_VERSION` binding in `strategy_contract.py:17`).
- **Quant-Lab depends on it implicitly — PARTIAL.** `strategy-core` is **not** declared in `C:/Users/gonza/Documents/Claude-Quant-Lab/pyproject.toml` (grep for `strategy_core`/`strategy-core` there = no matches), yet `src/alpha_lab/agents/data_infra/ml/engine_decision.py:45` does `import strategy_core as sc`. No `.venv`, `.pth`, or `sys.path` insertion for it was found in the repo, so resolution relies on `strategy_core` being importable on `PYTHONPATH`/the active environment (the undeclared-dependency gap; the reverse direction is wired in Strategy-Core's `validation/*` via `sys.path.insert(0, "C:/Users/gonza/Documents/Claude-Quant-Lab/src")`).
- Note: Trade-Lab also keeps a **parallel** contract implementation (`trade_lab.domain.contracts`) distinct from `strategy_core.contract`; both expose `StrategyContract`/`load_strategy_contract`/`ContractError`.

Public surface of the shared package, quoted verbatim:

`C:/Users/gonza/Documents/Strategy-Core/src/strategy_core/__init__.py:108-157`
```python
__all__ = [
    "ENGINE_VERSION",
    "CONTRACT_VERSION",
    # types
    "Trade",
    "Quote",
    "Bar",
    "Level",
    "Zone",
    "Touch",
    "Side",
    "Direction",
    "CloseReason",
    "SessionScheme",
    "SessionWindow",
    "SessionInfo",
    # schemes
    "RESEARCH_SESSION_SCHEME",
    "TRADE_LAB_CT_SESSION_SCHEME",
    # candles
    "CandleEngine",
    "CandleUpdate",
    "build_tick_bars_from_frame",
    "make_bar_id",
    # decisions: zones / touch / sessions
    "build_zones",
    "is_touch",
    "detect_touches",
    "classify_session",
    "trading_day_for",
    "is_in_closed_window",
    # decisions: features
    "int_time_beyond_level",
    "int_time_within_2pts",
    "int_absorption_ratio",
    "app_large_trade_vol_pct",
    "app_avg_trade_size",
    "app_max_spread",
    # decisions: outcomes
    "classify_mae_first",
    "resolve_outcome",
    "OutcomeResult",
    # decisions: honest-entry orchestration
    "resolve_honest_outcome",
    "HonestEntryDrop",
    # contract
    "StrategyContract",
    "load_strategy_contract",
    "ContractError",
]
```

The two version stamps are also part of the public surface (`__init__.py:54,57`):
```python
ENGINE_VERSION = "strategy_core_engine_v3"
CONTRACT_VERSION = "trade_lab_contract_v1"
```

---

## 8. Gaps, TODOs, open questions

Scope note: every claim below cites a file path with line numbers from a read-only inspection. Labels: IMPLEMENTED | PARTIAL/STUB | PLANNED/TODO | NOT FOUND.

### 8.1 Explicit TODO / FIXME / XXX / HACK markers

A grep for `TODO|FIXME|XXX|HACK` across all three trees returns essentially **one** real source-code marker. The only other hits are false positives: `XXX` inside npm lockfile integrity hashes (`Claude-Quant-Lab/dashboard-ui/package-lock.json:3097`, `Trade-Lab/frontend/package-lock.json:4106`).

- **PLANNED/TODO — Quant-Lab signal-engineering refinement is a hard stub.** `Claude-Quant-Lab/src/alpha_lab/agents/signal_eng/agent.py:145-148`:
```python
        # TODO: Implement parameter adjustment based on failed_metric
        # For now, raise NotImplementedError for metrics we don't handle yet
        msg = f"Refinement logic for metric '{failed_metric}' not yet implemented"
        raise NotImplementedError(msg)
```
- **NOT FOUND — no `TODO/FIXME/XXX/HACK` markers in Strategy-Core source** (only narrative "not yet"/"deferred" wording in docs and `.wf/` build scripts, e.g. `Strategy-Core/.wf/build_strategy_core.js:114,323`).
- **NOT FOUND — no `TODO/FIXME/XXX/HACK` markers in Trade-Lab `backend/src`** (hits there are the identifier `pending`, e.g. `services/replay.py`, `services/broadcaster.py`, not gap markers).

### 8.2 Stubs / NotImplementedError / placeholders (code-level)

- **PARTIAL/STUB — Quant-Lab tick-bar aggregation unimplemented (by decision).** `Claude-Quant-Lab/src/alpha_lab/agents/data_infra/aggregation.py:115-116` raises `NotImplementedError("Tick bar aggregation not implemented")` for `TICK_987`/`TICK_2000`. Decision recorded in `Claude-Quant-Lab/docs/DECISIONS.md:82-86` (D-010): "`aggregate_tick_bars()` remains `NotImplementedError`. Only time-based bars (1m through 1D) are implemented. `build_data_bundle()` silently skips tick timeframes."
- **PARTIAL/STUB — Quant-Lab `StubDataProvider` is a non-functional stub.** `Claude-Quant-Lab/src/alpha_lab/agents/data_infra/providers/stub.py:34,44,48` — `get_ticks`, `get_ohlcv`, and `get_daily_settlement` each `raise NotImplementedError("... not yet implemented")` despite the module docstring claiming it "generates deterministic synthetic OHLCV bars."
- **PARTIAL/STUB — Quant-Lab provider tick/ohlcv paths.** `data_infra/providers/databento.py:356-364` raises `NotImplementedError` for tick-based timeframes (directs callers to `get_ticks`); `data_infra/providers/polygon.py:150,218` also raise `NotImplementedError`.
- **PARTIAL/STUB — Quant-Lab `ml_extrema_classifier` flagged experimental, uses placeholder features.** `Claude-Quant-Lab/src/alpha_lab/agents/signal_eng/detectors/tier3/ml_extrema_classifier.py:36` ("filled with 0.0 and some values (e.g. `pl_width`) are placeholders") and `:239` (`features["pl_width"] = 50.0  # Placeholder`). Decision `DECISIONS.md:233-237` (D-028) marks it `_EXPERIMENTAL = True` with a runtime DeprecationWarning due to a "severe domain mismatch" (bar-level OHLCV approximating tick-level training features).
- **PARTIAL/STUB — Quant-Lab dead helper.** `Claude-Quant-Lab/src/alpha_lab/agents/data_infra/ml/dashboard_utility_builder.py:230-232`: `_update_session_levels_from_cache(...)` is a `"No-op placeholder — session levels must be computed from bars."` (body is `pass`).
- **PARTIAL/STUB — Strategy-Core live Databento source is intentionally inert.** `Strategy-Core/src/strategy_core/data/databento_live.py:90`: after checking the SDK import, `start()` ends with `raise RuntimeError("real Databento live startup is not implemented in Strategy-Core tests")`. A `start_fake()` (`:95`) is the only working start path.
- **STALE PLACEHOLDER DOCSTRINGS — Trade-Lab.** `Trade-Lab/backend/src/trade_lab/api/__init__.py:1` reads `"API boundary placeholder; no frontend streaming is implemented in Phase 2A."` and `services/__init__.py:1` reads `"Service orchestration layer placeholder for future pipeline wiring."` — both are out of date: the packages now contain a full FastAPI app, broadcaster, replay/live/runtime services (`services/replay.py`, `services/live.py`, `services/runtime.py`, `services/broadcaster.py`). The placeholder docstrings were not updated.

### 8.3 Deferred / blocked / "not yet" items (doc + contract level)

The dominant cross-project gap is a single coherent theme: **Quant-Lab can train and emit `strategy_core_engine_v3` contracts, but no v3 model can be served live/paper because (a) Trade-Lab's model-serving layer is not yet v3-repointed, and (b) no canonical v3 bundle has been verified.**

- **BLOCKED — Trade-Lab is not v3-compatible for model serving.** `Claude-Quant-Lab/docs/DECISIONS.md:277-282` (D-033): "Do not describe a v3 dashboard-utility bundle as Trade-Lab-ready until Trade-Lab is repointed... Quant-Lab can still train and emit v3 contracts before Trade-Lab is ready; runtime use remains blocked." Echoed in `Claude-Quant-Lab/ARCHITECTURE.md:7` ("Trade-Lab is **not yet v3-compatible**"), `Claude-Quant-Lab/CLAUDE.md:12`, and `Claude-Quant-Lab/docs/pipeline_state.yaml:139` (`trade_lab: "INCOMPATIBLE pending repoint"`).
- **BLOCKED — model-serving gate, market-data runtime aligned.** `Strategy-Core/V3_COMPATIBILITY_MATRIX.md:7,17`: "**Market-data runtime aligned; model serving still blocked.**" / "**Runtime session aligned; model-serving gate still blocked.**" Strategy-Core's `MIGRATION.md:20` marks "Trade-Lab model-contract / feature / outcome compatibility" as ⚠️ blocked: "Model activation, contract fail-close, feature-vector parity, and decision-time outcome tracking still need a verified v3 bundle path. Do not serve v3 bundles there yet."
- **PLANNED — remaining Trade-Lab repoint engineering enumerated but not built.** `Strategy-Core/MIGRATION.md:39-62` lists five required items: (1) fail-close bundle activation via `strategy_core.load_strategy_contract`, (2) retire/quarantine stale local `domain/candles.py`, `domain/sessions.py`, `domain/levels.py`, (3) replace feature computation (route the two time features + absorption through Strategy-Core trade-print formulas instead of quote-mid dwell), (4) replace outcome tracking with the v3 decision-time entry convention, (5) prove end-to-end parity before activation. The contract source confirms this is by-design: `Claude-Quant-Lab/src/alpha_lab/agents/data_infra/ml/strategy_contract.py:12,123` — "Full v3 contract is emitted, but current Trade-Lab activation is blocked."
- **DEFERRED — canonical v3 model/data bundle verification.** Blocked on an external artifact (the "local data/model zip"). `Strategy-Core/MIGRATION.md:21,66-77`; `Claude-Quant-Lab/docs/pipeline_state.yaml:123` (`canonical_bundle_verification: "DEFERRED until local data/model zip is available"`); `Claude-Quant-Lab/AGENTS.md:100`; `Claude-Quant-Lab/ARCHITECTURE.md:155`; `Claude-Quant-Lab/docs/ML_TRAINING_WORKBENCH.md:225`. Note the retained `data/models/dashboard_3feature_v1.cbm` exporter is explicitly "not automatically a v3 bundle" (`DECISIONS.md:273`, D-032 trade-off).
- **PLANNED (drafted, awaiting approval) — Stage Q `strategy.json` emitter.** `Trade-Lab/docs/inference-integration-plan.md:136-137`: "*Status: `strategy_contract.py` + the `save_trained_model` hook were drafted before plan approval; pending your go/revert.*" Related operational caveat in `Trade-Lab/docs/SESSION-HANDOFF-2026-05-30.md:216`: Stage Q emission is best-effort (wrapped in try/except + `logger.warning`), so "a contract-emission bug silently produces **no** `strategy.json` rather than failing the model save."
- **PENDING — live Databento validation never run end-to-end.** `Trade-Lab/docs/architecture.md:79-80,101` ("Manual live validation remains pending and must be run explicitly by an operator"); `Trade-Lab/docs/SESSION-HANDOFF-2026-05-30.md:218` ("Live Databento validation NOT done end-to-end... live precision against the research touch rule is explicitly **not** yet validated end-to-end").
- **PLANNED/NOT FOUND — replay seek.** `Trade-Lab/docs/architecture.md:153-154`: `ReplayController` supports start/pause/resume/stop/speed/end-of-stream; "seek is not implemented and remains a future option."
- **PLANNED — model registry / inference ports prepared but not enabled in Trade-Lab.** `Trade-Lab/docs/architecture.md:57` ("Prepare, but do not yet enable, model registry and inference ports"), `:81` ("Model inference, trading execution, risk/account management, and broker routing remain out of scope"), `:155` (`ContractRegistry` or future `ModelRegistry`).

### 8.4 Known data/feature gaps an architect should know (Trade-Lab handoff)

From `Trade-Lab/docs/SESSION-HANDOFF-2026-05-30.md:200-228` ("Known Gaps / Deferred / Gotchas"):

- **Approach features need quotes (SESSION-HANDOFF:210).** 3 of 6 features (`app_large_trade_vol_pct`, `app_avg_trade_size`, `app_max_spread`) require clean top-of-book BBO; a trades-only replay source makes `app_max_spread` and approach features "**NaN on every prediction**."
- **Checksum / fail-closed validation unverified (SESSION-HANDOFF:214).** Each bundle "should carry a `model.cbm.sha256` checksum file"; the note flags "Verify the production bundle actually ships a checksum" — i.e. not confirmed present.
- **Model-bundle compatibility risk (SESSION-HANDOFF:204-208).** Any bundle "trained/evaluated under the old exact-touch/Chicago semantics must be retrained or quarantined before live precision is trusted." Reinforced as the top "Recommended Next Step" (`:234-236`).
- **`mbp-3` source rejected (SESSION-HANDOFF:224).** A literal `mbp-3` source "is rejected by `replay_catalog.py:26` until the schema list is extended."
- **Frontend intelligence placeholders (`inference-integration-plan.md:219-220`).** Runtime `session` + `trading_day` are not yet in `/api/v1/status`, leaving frontend `"unavailable"`/null placeholders at `IntelligencePanel.tsx:14-18` and `normalize.ts:18`.

### 8.5 Branch / version / repo state (factual, from git)

- **All three on `main`.** Strategy-Core HEAD = `fd53e06` ("fix: audit follow-ups — touch-zone parity (#3)..."). Quant-Lab HEAD = `5a096a1`. Trade-Lab HEAD = `a191202`.
- **Trade-Lab pins Strategy-Core to the current HEAD.** Trade-Lab commit `aacc51b` ("build: pin strategy-core to the fixed engine commit (fd53e06)") matches Strategy-Core's HEAD `fd53e06`, so the dependency pin is up to date.
- **Uncommitted working-tree state in Trade-Lab.** `docs/ifvg-strat.md` is modified (`M`) and `backend/.claude/` is untracked (`??`). The other two repos show clean working trees in the surveyed output.
- **Docs describe an unbuilt strategy.** `Trade-Lab/docs/ifvg-strat.md` is a large SMC/HTF-FVG/IFVG strategy spec (with a Pine reference at `Trade-Lab/plans/algo_chef_smc_htf_fvg_ifvg_strategy_v2.pine`). Its own review section states the strategy is "not yet proven profitable or production-ready" (`ifvg-strat.md:1515`); no implementation of this strategy was found in the runtime code — it is design/plan documentation only.
- **Stale-doc hazard called out by the projects themselves.** `Claude-Quant-Lab/docs/README.md:5` and `DECISIONS.md:286` (D-034) warn that older phase reports are pruned/historical and must not be read as current architecture; Strategy-Core's `validation/PHASE*.md` are explicitly "audit trail, not current-state docs" (`MIGRATION.md:3`).