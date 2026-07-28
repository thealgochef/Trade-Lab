# VIZ_RECON — TL frontend trade-visualization inventory

Read-only recon, 2026-06-12. **Branch note:** the `worktree-w2-operate` worktree was pruned mid-recon
(directory emptied, no longer in `git worktree list`); recon completed against `platform-refactor`
@ `5a8d28a`, which is one lint-only commit (app.py import order, test_lifecycle_w2 line wraps) ahead
of the worktree state — every file cited below is byte-identical between the two. SC references were
read from the sibling checkout `C:\Users\gonza\Documents\Strategy-Core` and verified byte-identical
to the pinned commit `256020c` for the cited file (`git diff --stat 256020c -- src/strategy_core/decisions/streaming.py` is empty).

---

## 1. Chart stack

**Library:** `lightweight-charts` **5.0.9** (TradingView), declared at `frontend/package.json:14`.
No custom canvas/svg; the only chart consumer is the candle workspace.

**Owning files:**
- `frontend/src/components/TradingChart.tsx` — creates the chart + single `CandlestickSeries`
  (`TradingChart.tsx:27-45`), owns lifecycle, applies incremental bar updates
  (`applyBarData`, `TradingChart.tsx:88-101`).
- `frontend/src/chart/overlayManager.ts` — `ChartOverlayManager`, the one annotation surface:
  price lines (`syncLevels`, `overlayManager.ts:17-39`) + series markers (`syncMarkers`,
  `overlayManager.ts:41-43`, built on `createSeriesMarkers`, `overlayManager.ts:14`).
- `frontend/src/chart/viewModels.ts` — all domain→chart mapping (bars, level overlays, markers).

**Native annotation support:**
- Per-point markers: **yes** — `createSeriesMarkers` (arrowUp/arrowDown/circle/square, above/below
  bar, text label). Already in use.
- Horizontal lines: **yes, but full-chart-width only** — `series.createPriceLine`
  (`overlayManager.ts:29-36`). Already in use for levels.
- Time-bounded horizontal segments (e.g. a TP line from entry bar to resolution bar): **not
  native**. Needs either one extra `LineSeries` per segment or a v5 `ISeriesPrimitive` plugin.
- Critical nuance for any new annotation: chart time is a **synthetic axis** (trading-day midnight
  + `bar_index` seconds, `viewModels.ts:39-45`) because tick bars share wall-clock seconds. Every
  wall-clock timestamp must be mapped through `createChartTimeResolver` (`viewModels.ts:87-101`)
  or the annotation lands on the wrong candle.

## 2. Existing overlays

Everything below renders **on the chart** today, assembled in `ChartWorkspace.tsx:23-25` and pushed
through `TradingChart` props → `ChartOverlayManager`:

| Overlay | Builder | Chart form | Store |
|---|---|---|---|
| Levels (PDH/PDL/session) | `normalizeLevels` (`viewModels.ts:103-118`) | full-width price line, eligible=solid/colored, display=dashed grey | `intelligenceStore.levels` (`stores.ts:62-67`) |
| Touches | `normalizeTouchMarkers` (`viewModels.ts:120-137`) | arrowUp/circle marker on touch bar | `intelligenceStore.touches` |
| Observations | `normalizeObservationMarkers` (`viewModels.ts:139-158`) | square marker on scheduled-end bar | `intelligenceStore.observations` |
| Predictions | `normalizePredictionMarkers` (`viewModels.ts:163-181`) | arrowDown (eligible) / grey circle (ineligible) above touch bar | `predictionStore.predictions` (`stores.ts:111-117`) |
| Outcomes | `normalizeOutcomeMarkers` (`viewModels.ts:185-204`) | arrowUp green (correct) / arrowDown red (miss) below the **touch** bar | `predictionStore.predictions[].outcome` |

All five are merged by `combineMarkers` (`viewModels.ts:206-216`). Components:
`ChartWorkspace.tsx` (subscribes via `useIntelligence`/`usePredictions`/`useMarket`) →
`TradingChart.tsx` → `ChartOverlayManager`.

**Pane-only** (never on chart): warnings (`IntelligencePanel.tsx:41-43`), blotter events
(`EventBlotter.tsx`), and the textual lists of levels/touches/observations/predictions in
`IntelligencePanel.tsx:23-43`. Dropped predictions are **deliberately chart-invisible**
(`stores.ts:143` comment: drops never enter `prediction.outcome`, so no marker).

## 3. Prediction/outcome rendering trace (from `client.ts`)

**`prediction.created`** (`client.ts:133-136`) → `normalizePrediction` → `addPrediction`
(`stores.ts:122-130`) → `predictionStore.predictions`, reaching three surfaces:
1. **Chart** — marker above the touch bar (`viewModels.ts:163-181`); shows class text + color only.
2. **IntelligencePanel pane** — top 8, `IntelligencePanel.tsx:32-34`, row at `48-79`: predicted
   class, eligible/ineligible badge, direction, full probability set, `levelKind @ level price`
   (`levelPriceTicks/4`, line 62), session. **Not shown:** model_id, contract_id, nan_count.
3. **EventBlotter** — generic "Prediction created" row (`client.ts:135`), no fields.

**`prediction.resolved`** (`client.ts:137-140`) → `normalizeOutcome` → `addOutcome`
(`stores.ts:132-140`; annotates the matching `prediction.outcome`):
1. **Chart** — correct/miss marker **anchored at `prediction.timeUtc` (the touch bar), not at
   resolution time** (`viewModels.ts:185-204`, comment 183-184). Shows correct/miss + actual class.
2. **IntelligencePanel** — outcome sub-row (`IntelligencePanel.tsx:63-71`): correct/incorrect
   badge, actual class, **MFE pts, MAE pts, resolution type**. **Not shown:** `entryPrice`,
   `barsToResolution`, resolved timestamp.
3. **Blotter** — generic "Prediction resolved" (`client.ts:139`).
- The standalone `outcomes` list + `useOutcomes` hook (`stores.ts:174`) have **no component
  consumer**; outcomes surface only via the annotated prediction.

**`prediction.dropped`** (`client.ts:141-146`) → `normalizeDropped` → `addDropped`
(`stores.ts:144-152`):
1. **Chart** — nothing, by design.
2. **IntelligencePanel** — dropped sub-row (`IntelligencePanel.tsx:72-77`): badge + reason only.
3. **Blotter** — "Prediction dropped (reason)" (`client.ts:144`).

The snapshot path (`client.ts:171-203`) seeds the same stores with the same annotation rules.

**Answer to the field question: `entry_price` is displayed NOWHERE in the UI** (grep over
`frontend/src` shows it only in types, normalizers, and tests). **TP/SL are displayed nowhere and
do not exist in any DTO** (see §4). MFE/MAE display only in the IntelligencePanel outcome sub-row.

## 4. DTO pass-through

**WS type seam (backend `dto.py` ↔ `frontend/src/realtime/types.ts`): zero drift.**
- `PredictionDTO` backend `dto.py:101-116` ↔ frontend `types.ts:92-108` — field-for-field
  identical (incl. `level_price_ticks`, `feature_values`).
- `OutcomeDTO` backend `dto.py:119-131` ↔ frontend `types.ts:110-123` — identical; **all six
  asked-about fields are carried**: `entry_price`, `level_price_ticks` (on prediction),
  `resolution_type`, `max_mfe_pts`, `max_mae_pts`, `bars_to_resolution`.
- `DroppedPredictionDTO` backend `dto.py:134-139` ↔ frontend `types.ts:125-131` — identical,
  incl. `entry_price: number | null`.

**Where fields actually fall off — the domain-normalize seam (`frontend/src/domain/normalize.ts`)
and the render layer:**
- `normalizePrediction` (`normalize.ts:116-133`) drops `feature_values` deliberately
  (comment `normalize.ts:114-115`); everything else carried.
- `normalizeOutcome` (`normalize.ts:135-148`) carries everything incl. `entryPrice`
  (`models.ts:148`) — **carried into the domain model but never rendered** (render gap, not a
  type gap).
- `normalizeTouch` (`normalize.ts:87-94`) drops `level_price_ticks` (`MarketTouch.priceTicks` is
  the **trade** price, `normalize.ts:92`), `trading_day`, `requested_symbol`, `raw_symbol`,
  `instrument_id`, `sequence_in_session`.
- `normalizeObservation` (`normalize.ts:96-103`) drops `level_price_ticks`, `trading_day`,
  `originating_touch_id`.
- **TP/SL prices never leave the backend:** `tp_points`/`sl_points` live only on the contract
  policy fed to the resolver build (`backend/src/trade_lab/services/runtime.py:416-423`); no DTO
  carries them. `PredictionDTO` has no entry field at all — entry does not exist at creation time
  (see §5).

## 5. Entry-price timing (backend, read-only)

The anchored entry price is computed at registration (`entry_price = self._trade_price_at(decision_ts_utc)`,
`streaming.py:222`) but stored only on the **private** `_OpenSetup.entry_points`
(`streaming.py:226-234`) with no public accessor (the class exposes only `open_count`), so the first
event/field that surfaces it is `StreamResolution.entry_price` / `StreamDrop.entry_price` at
resolution or drop — nothing between `prediction.created` and `prediction.resolved` carries it
(the WS vocabulary `frontend/src/realtime/types.ts:1-15` has no intermediate event, and TL's
`_register_prediction` returns only the registration-time drop, `runtime.py:438-476`).

**Proving symbol:** `StreamingHonestResolver.register()` →
`Strategy-Core/src/strategy_core/decisions/streaming.py:194-235` — returns `StreamDrop | None`,
where `None` means "live setup" and the fill survives only inside `self._open`.

## 6. Size estimate — "entry marker + TP/SL lines + resolution marker per prediction"

File-touch list only (no design). Two tiers:

**Tier A — frontend-only, annotations appear at resolution time** (uses `entryPrice`,
`resolutionType`, `outcome.timeUtc` already in the domain model):
- `frontend/src/chart/viewModels.ts` — new builders: entry-price marker/line, TP/SL overlays,
  resolution marker anchored at `outcome.timeUtc` (today's outcome anchor is the touch bar,
  `viewModels.ts:185-204`).
- `frontend/src/chart/overlayManager.ts` — bounded TP/SL segments need a per-prediction
  `LineSeries` or `ISeriesPrimitive`; the manager currently holds only the candle series
  (`overlayManager.ts:11`), so it would need the chart handle from `TradingChart.tsx:45`.
- `frontend/src/components/TradingChart.tsx` — new props + sync effects (pattern at lines 64-65).
- `frontend/src/components/ChartWorkspace.tsx` — wire new overlay memos (lines 23-25).
- Tests: `frontend/src/chart/viewModels.test.ts`, `frontend/src/chart/overlayManager.test.ts`,
  `frontend/src/components/ChartWorkspace.test.tsx`.

**Tier B — live TP/SL + entry shown from prediction time** (adds everything Tier A needs plus the
backend/SC plumbing, because entry is private until resolution and TP/SL never leave the backend):
- `Strategy-Core/src/strategy_core/decisions/streaming.py` — surface the accepted entry from
  `register()` (return value or accessor).
- `backend/src/trade_lab/services/runtime.py` — capture entry in `_register_prediction`
  (`runtime.py:438-476`); TP/SL already in hand at resolver build (`runtime.py:416-423`).
- `backend/src/trade_lab/api/dto.py` — extend `PredictionDTO` (`dto.py:101-116`) or add an event.
- `backend/src/trade_lab/api/app.py` — WS emit + snapshot payload for the new fields/event.
- `frontend/src/realtime/types.ts`, `frontend/src/domain/models.ts`,
  `frontend/src/domain/normalize.ts` — carry the new fields across the seam.
- Backend tests: `backend/tests/test_lifecycle_w2.py`, `backend/tests/test_resolution_adapter.py`,
  `backend/tests/test_honest_resolver_serving.py`; frontend `realtime/client.test.ts`.

Rough scale: Tier A ≈ 4 source files + 3 test files; Tier B ≈ 10-12 files across SC + TL backend +
frontend, including one SC API change that must respect the streaming/batch parity gates.
