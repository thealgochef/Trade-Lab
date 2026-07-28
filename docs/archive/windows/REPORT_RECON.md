# REPORT window — P1 recon (read-only, pre-build)

Date: 2026-07-10. Branch: `platform-refactor` (post WARM-FIX, TL local head 5a661ea).
Scope: the four P1 questions, answered from code + real artifacts. Build targets these facts.

## (a) Journal row schemas — verbatim

Writer: `backend/src/trade_lab/services/journal.py` (`PredictionJournal`). One JSONL file per
trading day (18:00 ET roll, `trading_day_for`) under `settings.journal_path`; rows with a `None`
timestamp land in `undated.jsonl`. Line-buffered append; malformed/partial lines are possible in
principle (no fsync by design). Verified against real rows in `backend/data/journal/2026-06-18.jsonl`.

### `type:"prediction"` (journal.py:30-52)

| field | type | notes |
|---|---|---|
| `type` | `"prediction"` | |
| `mode` | str | `"replay"` \| `"live"` (feed mode at write; warm-replay rows are `"live"`) |
| `bundle_id` | str | active model id, e.g. `NQ_W3_20260613T055600Z` |
| `ts_utc` | ISO datetime | decision instant (`event_ts_utc`) |
| `prediction_id` | str (uuid) | |
| `touch_id` | str (uuid) | |
| `observation_id` | str (uuid) | |
| `predicted_class` | str | one of the contract `class_map` values |
| `probabilities` | {class: float} | all classes |
| `feature_values` | {name: float} | contract feature set |
| `is_eligible` | bool | the serving gate verdict (class+session+confidence) |
| `direction` | str | `"long"` \| `"short"` |
| `session` | str | `"asia"` \| `"london"` \| `"ny"` (plugin scheme) |
| `level_kind` | str | e.g. `"asia_high"`, `"pdl"` |
| `level_price_ticks` | int | |
| `contract_id` | str | same as bundle id today |
| `nan_count` | int | |

### `type:"outcome"` (journal.py:54-74)

| field | type | notes |
|---|---|---|
| `type` | `"outcome"` | |
| `mode` | str | as above |
| `bundle_id` | str \| null | |
| `ts_utc` | ISO datetime | resolved instant |
| `outcome_id` | str (uuid) | |
| `prediction_id` | str | join key to the prediction row |
| `touch_id` | str | |
| `resolution_type` | str | **`"tp_hit"` \| `"sl_hit"` ONLY** (ResolutionType, outcomes.py:28-37) |
| `actual_class` | str | ground-truth label |
| `predicted_class` | str | copied from the prediction |
| `correct` | bool | predicted == actual |
| `max_mfe_pts` | float | |
| `max_mae_pts` | float | |
| `bars_to_resolution` | int | ZERO-based index of the resolving bar |
| `entry_price` | float | honest fill at decision (NOT the level price) |

**Outcome rows carry no session / level_kind / direction / is_eligible — those require the
prediction join via `prediction_id`.** They also carry no tp/sl point values.

### `type:"drop"` (journal.py:76-91)

| field | type | notes |
|---|---|---|
| `type` | `"drop"` | |
| `mode`, `bundle_id`, `ts_utc` | | decision ts |
| `prediction_id`, `touch_id` | str | |
| `reason` | str | `flatten` \| `cutoff` \| `no_fill` (registration-time, `entry_price` null) · `no_forward` \| `no_resolution` (terminal, `entry_price` = honest fill) |
| `entry_price` | float \| null | see above |

## (b) Do timeout/flatten outcomes carry a price at resolution?

**There are no timeout/flatten outcomes.** D1b retired `SESSION_END`/`NO_RESOLUTION` from
`ResolutionType` (outcomes.py:31-34); the honest resolver never force-labels. Flatten/cutoff/
no-resolution setups surface as **drop rows**, which carry **no exit price and no final
excursion** — at most the honest `entry_price` (terminal drops only). MFE/MAE exist only on
resolved (`tp_hit`/`sl_hit`) outcomes.

→ Proxy-rule consequence (per the window spec): only `tp_hit`/`sl_hit` are priceable
(`+tp_points` / `−sl_points`); drops are **excluded from net and bucketed by reason**, never
silently dropped.

## (c) Active bundle's OOS artifacts

Bundle `NQ_W3_20260613T055600Z` at `C:\Users\gonza\Documents\Claude-Quant-Lab\models\`:
`model.cbm`, `model.cbm.sha256`, `metadata.json`, `strategy.json`, `evaluation.json`,
`oos_predictions.parquet`.

### `oos_predictions.parquet` (41 rows)

Columns: `fold` (int64), `timestamp` (ns, tz=US/Eastern), `session` (str),
`binary_true_tradeable` (int64), `label_encoded` (int64), `label` (str),
`pred_label_encoded` (int64), `pred_label` (str), `prob_tradeable_reversal` (double),
`gate_0_70_runtime_sessions` (bool), `gate_0_70_ny` (bool), `prob_trap_reversal` (double),
`prob_aggressive_blowthrough` (double).

**No MFE/MAE columns exist in the OOS artifacts** (neither parquet nor evaluation.json).
→ The OOS-vs-journal comparison shows MFE/MAE stats journal-side only, OOS side explicitly
`null` with a note — not fabricated.

### `evaluation.json` gate/expectation fields (used by the comparison)

- `quality_gates.gates{name → {passed, value, threshold, operator}}`, `all_passed` (false for
  this bundle), `allow_failed_gates`.
- `gated_oos`: `trade_count` 3, `precision` 0.333, `expectancy_15_30_pts` −5.0,
  `profit_factor_15_30` 0.5, `coverage`, `confidence_gate` 0.7, `eligible_sessions` ["ny"],
  `n_samples` 41.
- `trade_utility`: `expectancy_15_15_pts` −5.53, `expectancy_15_30_pts` −15.79,
  `profit_factor_15_30` 0.231, `n_simulated_trades` 19.
- `oos_three_class_balance`: {tradeable_reversal 17, trap_reversal 10, aggressive_blowthrough 14}.
- `session_metrics.{all,asia,london,ny,non_ny}`.

**Honesty caveat carried into the UI:** OOS `expectancy_15_30_pts` is a 15tp/30sl simulation;
the journal proxy prices the contract's actual `label_policy` (15tp/15sl). The panel labels each
side's pricing rule; they are not the same instrument.

### `strategy.json` fields the pricing proxy uses

`label_policy.tp_points` 15.0, `label_policy.sl_points` 15.0, `point_value` 20.0 (matches the
$20/pt UI toggle), `class_map`, `inference.{eligible_class, eligible_session, confidence_gate}`.

## (d) Model store path the backend already knows

`settings.models_path` ← `TRADE_LAB_MODELS_PATH` (config.py:41-42), currently
`C:\Users\gonza\Documents\Claude-Quant-Lab\models` (backend/.env). The journal dir is
`settings.journal_path` ← `TRADE_LAB_JOURNAL_PATH`, default `backend/data/journal`
(config.py:82-84). Both flow through `create_app` (app.py:236, 248, 254) — the endpoint needs no
new configuration.

## Build-shaping facts found along the way

1. **Row-id duplicates are a real class**: pre-WARM-FIX journals (2026-06-15..18) contain
   warm-replay duplicate predictions (WARM_PERF_RECON.md §2, proven ×10) tagged `mode:"live"` and
   byte-indistinguishable from real-time rows apart from ids. The P3 warm-gate (e69937f) stops new
   ones. The aggregator therefore (i) dedupes EXACT id repeats (same `prediction_id` seen twice as
   prediction; >1 outcome per `prediction_id`) into a counted bucket, and (ii) cannot and does not
   heuristically dedupe distinct-id warm dupes — they are a data artifact of the old code and are
   reported as-is.
2. **Join across day files is required**: a prediction at 17:59 ET and its outcome at 18:01 ET land
   in different files; the aggregator joins over the whole directory before filtering. A trade is
   dated by its OUTCOME's trading day (P&L realization day); predictions/drops by their own ts.
3. Orphan outcomes (prediction row missing) cannot be classified by session/level/eligibility —
   bucketed, shown, never silently dropped.
4. `pyarrow>=18.0` is already a hard backend dependency — parquet reading needs no new dep.
5. `model_registry.py` imports `strategy_core` at module import; the pure aggregator re-implements
   the tiny safe-model-id check instead of importing it.
6. Frontend has no view switcher yet (single workstation layout, App.tsx); the Performance page adds
   a top-level tab. React 18 + vitest 2 + lightweight-charts 5 (charts here use plain SVG viewmodels
   instead — testable, no chart-lib coupling).
