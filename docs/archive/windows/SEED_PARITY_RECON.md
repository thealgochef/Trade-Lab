# SEED_PARITY_RECON — does the TL dashboard replay seed prior-day PDH/PDL identically to QL training's carry?

**BACKLOG item:** verify-prior-session-levels. **Date:** 2026-07-06. **Scope:** read-only across SC (strategy-core) @ `108d1a7`, QL (Claude-Quant-Lab) @ `0b4a2e8`, TL (Trade-Lab) @ `923b29a`, all on `platform-refactor`. Citation prefixes `SC/`, `QL/`, `TL/` = repo-relative paths.

**Method.** Sections 1-4: five parallel recon agents, each followed by an independent citation-verification agent that re-opened every cited file:line and re-ran the cheap shell probes (11 substantive corrections applied). Section 5: computed evidence from `scratch_seed_parity.py` (untracked, TL root) run read-only over the local store — results in `scratch_seed_parity_results.json`, log in `scratch_seed_parity_run.log` (both untracked). Nothing in section 5 is inferred: every number below was produced by executing QL's own seed function, the canonical SC reader, and an independent pyarrow re-computation.

**Answer in one line.** No — the TL dashboard replay never seeds PDH/PDL at all (structurally UNSEEDED), while QL training's rolling carry is proven tick-exact against canonical prior-day extremes on all 7 probe days; the fix template (the w3b `_SeedingSource`) already exists and is parity-proven.

---

## 1. TL dashboard replay seed

#### (a) Call chain: replay-start endpoint → first event

1. **Endpoint** — `POST /api/v1/replay/start` → `replay_start` (TL/backend/src/trade_lab/api/app.py:500-501). Guards: `_authorize_live_control` (app.py:504), `_reject_path_like_source_id` (app.py:505), 409 if the live feed is active (app.py:508-511). Source resolved from `app.state.replay_sources` (app.py:512-515), then `await app.state.replay.start(source, ReplayConfig(..., trading_day=definition.trading_day, symbol_dir=definition.symbol_dir))` (app.py:517-530).
2. **Composition** — the shared `ApplicationRuntime` is `app.state.runtime` (TL/backend/src/trade_lab/api/app.py:277); `replay = replay or HistoricalReplayService(runtime)` (app.py:279); update callback wired to the broadcaster (app.py:280-281). The catalog stamps `trading_day` from the `YYYY-MM-DD` directory name (TL/backend/src/trade_lab/adapters/replay_catalog.py:382-397).
3. **Service start** — `HistoricalReplayService.start` (TL/backend/src/trade_lab/services/replay.py:183) emits `self.runtime.reset(requested_symbol=..., feed_message="runtime reset for historical replay", reset_reason="replay_reset")` (replay.py:199-205), sets feed status REPLAYING (replay.py:206-217), wraps the source in `_StrategyCoreHistoricalSourceAdapter` (replay.py:218), constructs `CoreReplayRuntime` (replay.py:219-227), and spawns `_run_strategy_core_replay` (replay.py:229).
4. **Runtime reset** — `ApplicationRuntime.reset` (TL/backend/src/trade_lab/services/runtime.py:228-273) constructs a **brand-new** `StrategyCoreService` (runtime.py:245-250); that constructor builds a new `StrategyRuntime` with `**touch_reversal_kwargs()` (TL/backend/src/trade_lab/services/strategy_core_service.py:100-116), and `touch_reversal_kwargs()` instantiates a **fresh plugin object** per call — `"plugin": get_strategy("touch_reversal")()` (SC/src/strategy_core/runtime/wiring.py:33) — so a fresh `StrategyLevelState` (SC/src/strategy_core/strategies/touch_reversal/plugin.py:225-228).
5. **Event pull** — `_run_strategy_core_replay` calls `await core.start(...)` (TL replay.py:291); the SC replay loop pulls `iter(self.source.events())` (SC/src/strategy_core/runtime/replay.py:109, 128) → `_StrategyCoreHistoricalSourceAdapter.events` calls `source.scan(..., trading_day=..., symbol_dir=...)` (TL replay.py:43-57) → `HistoricalParquetAdapter.scan` (TL/backend/src/trade_lab/adapters/historical_parquet.py:54-89) → `_ScParquetSource.for_trading_day` (historical_parquet.py:77-83), which builds the `[D-1 18:00 ET, D 18:00 ET)` two-file window (SC/src/strategy_core/data/databento_parquet.py:276-279; missing prior-day file degrades to single file with `MISSING_PRIOR_DAY_FILE`, databento_parquet.py:296-315).
6. **First event delivered** — each yielded item goes through `_process_replay_item` → `self.runtime.process_market_event(...)` (TL replay.py:263-266 → runtime.py:704).

Nothing between the endpoint and the first event calls any level-seeding method.

#### (b) Exhaustive seed-call classification

Grep of TL `backend/` for `load_prior_day_summary|set_static_levels|prev_full_hl|prior_day|pdh` (case-insensitive), every hit classified:

- **Dashboard replay path: ZERO hits.** `services/replay.py` contains none of these strings; the `replay_start` endpoint body (app.py:500-533) contains none. The only app.py hit is a comment about the **live** composition (TL/backend/src/trade_lab/api/app.py:297).
- **Live path**: `LiveMarketDataService.start` seeds before any event — comment + call at TL/backend/src/trade_lab/services/live.py:236-238, implementation `_load_prior_day_summary` at live.py:311-339 (`self.runtime.levels.load_prior_day_summary(day, high_ticks, low_ticks)` at live.py:330); the value source adapter TL/backend/src/trade_lab/adapters/databento_historical.py:1,11 is live-only ("W2 P1c").
- **w3b SCRIPT harness (NOT the dashboard path)**: `backend/scripts/w3b/headless_replay.py` wraps the day source in `_SeedingSource` so the seed fires inside `scan()`, after `HistoricalReplayService.start`'s `runtime.reset()` and before the first event (headless_replay.py:99-115), calling `runtime.strategy_core_service.load_prior_day_summary(prior_trading_day(date_str), high_ticks=..., low_ticks=...)` (headless_replay.py:285-293); seed value = QL `prev_full_hl` backward-walk (backend/scripts/w3b/window.py:165-193), key = calendar minus-1 (window.py:196-200). Also scripts/w3b/__init__.py:9 and scripts/w3b/REPORT.md:91-95 (docs).
- **Adapter seam (inert unless called)**: `StrategyCoreService.load_prior_day_summary` pass-through (TL/backend/src/trade_lab/services/strategy_core_service.py:144-149); `LevelKind.PDH` fallback in touch mapping (strategy_core_service.py:268).
- **Shared serving code (not seeding)**: kind→side derivation in TL/backend/src/trade_lab/services/inference/inference_engine.py:65-70; `LevelKind` enum TL/backend/src/trade_lab/domain/levels.py:17-25; `MISSING_PRIOR_DAY_FILE` code TL/backend/src/trade_lab/domain/data_quality.py:24.
- **Tests**: test_live_warm_start.py:234-283 (live seed), test_strategy_core_service.py:81-96, test_inference_engine.py:369-443, test_inference_api.py:400-401, test_journal.py:22, test_lifecycle_w2.py:87, test_w3b_falsification.py:204-295, test_historical_parquet_adapter.py:227-254 (missing-prior-day-FILE warning, not a level seed).
- **Artifacts/docs**: backend/W3B_FIX_TL_DIFF.txt, backend/tests/fixtures/strategy.json:93 (`"pdh_pdl_source": "prior_day_full"` contract field).

`set_static_levels` has **zero** callers anywhere in TL backend source (only the SC definitions exist: the plugin write-through at SC/src/strategy_core/strategies/touch_reversal/plugin.py:237-239, the runtime write-through at SC/src/strategy_core/runtime/state.py:270-272, the level-state implementation at SC/src/strategy_core/runtime/levels.py:58-59, and the protocol declaration at SC/src/strategy_core/strategies/protocols.py:265).

#### (c) Seed value source — N/A for the dashboard replay

No seed path exists in the dashboard replay (above), so there is no value source / key convention / computation to trace on that path. For contrast, the two paths that DO seed use different conventions: live uses `prior_trading_day(trading_day_for(now))` — weekend-skipping calendar walk-back, holidays deliberately unmodeled (TL/backend/src/trade_lab/domain/trading_day.py:42-48, 5-7) — with `frame["high"].max()` / `frame["low"].min()` over the prior trading day's `[prev 18:00 ET, 18:00 ET)` ohlcv-1h Historical-API bars (TL live.py:322-330); the w3b script uses calendar minus-1 as the summary KEY with QL's `prev_full_hl` most-recent-NON-EMPTY-prior-window-day VALUE (TL backend/scripts/w3b/window.py:165-200).

#### (d) Dashboard replays run UNSEEDED — what levels exist

**Plainly: a dashboard replay of day D never seeds PDH/PDL, and PDH/PDL never appear during it.**

- **Not stale**: staleness is impossible because `HistoricalReplayService.start` unconditionally calls `runtime.reset` (TL replay.py:199-205), which **rebuilds** `StrategyCoreService` from scratch (TL runtime.py:245-251) → new `StrategyRuntime` → a newly instantiated plugin (SC wiring.py:33) → empty `StrategyLevelState._summaries` (SC/src/strategy_core/runtime/levels.py:42-50). There is no reset-skipping flag on the replay path (unlike live's `reset_runtime_on_start`, TL live.py:76, 228).
- **PDH/PDL absent entirely**: `StrategyLevelState.levels()` emits `pdh`/`pdl` only when `self._summaries` holds a day `< self._trading_day` (SC levels.py:96-100). `_summaries` is populated only by (i) `load_prior_day_summary` (SC levels.py:61-64) — never called on this path — or (ii) an in-stream day roll banking the completed day's extremes (SC levels.py:70-85). The replay window `[D-1 18:00 ET, D 18:00 ET)` (SC databento_parquet.py:276-279) is exactly ONE trading day under the 18:00 boundary, so `classify_session` never changes `info.trading_day` mid-stream and no roll occurs.
- **Organic levels that DO emerge**: session extremes only — `asia_high`/`asia_low` and `london_high`/`london_low`, folded per-trade into `self._ranges` (SC levels.py:49, 88-89) and emitted with `available_from` = session close (SC levels.py:101-107, 119-125). The fold tracks NO NY range (`_ranges = {"asia": ..., "london": ...}`, SC levels.py:49), so TL's `NY_HIGH`/`NY_LOW` `LevelKind` values (TL domain/levels.py:24-25) are unreachable from the SC fold as well.
- Consequence: any touch/zone the plugin detects during a dashboard replay (SC plugin.py:265-295, `build_zones` over `self._levels.levels()` at plugin.py:281) can only involve asia/london levels — a structural divergence from QL training, where `prev_full_hl` PDH/PDL zones exist from the trading-day start.

#### (e) Warm-start interplay (fce7846)

- The 2-prior-trading-days warm-start machinery lives entirely in the **live feed adapter**: `_WARM_START_PRIOR_TRADING_DAYS = 2` (TL/backend/src/trade_lab/adapters/databento.py:59-61), walk-back loop at databento.py:239-242, historical fetch/drain at databento.py:289, 352-359, 437-470. It is inside `DatabentoMarketDataFeed`, constructed only by `live_feed_factory` with `intraday_replay=True` + the shared `DatabentoHistoricalSource` (TL app.py:299-325). `HistoricalReplayService` never imports or touches any of it — its source is the catalog's `HistoricalParquetAdapter` (TL app.py:341-345, replay_catalog.py:382-397). The only shared code is the `ApplicationRuntime` fold itself (`process_market_event`: replay at TL replay.py:263-266, live at TL live.py:341-354 — sharing is stated as deliberate at live.py:3-6).
- Live PDH/PDL therefore come from BOTH the explicit one-day seed (TL live.py:311-339) AND organic rolls banked while replaying the 2 prior full days (SC levels.py:70-85; the explicit seed stays authoritative over an organic roll for the same day, SC levels.py:71-74). The dashboard replay has neither.
- **Piggybacking is impossible**: live and replay share one runtime *instance* (TL app.py:277-282, 327-336), but `HistoricalReplayService.start`'s unconditional `runtime.reset` (TL replay.py:199-205) rebuilds the SC service (TL runtime.py:245-250), wiping any live-seeded prior-day summary before the first replay event. Mutual exclusion additionally 409-blocks starting a replay while live is active (TL app.py:506-511) and starting live while a replay is active (TL app.py:383-387).

---

## 2. TL live seed (definition map)

#### (a) Seed call chain — startup to plugin

Live never auto-starts; the chain begins at the operator endpoint `POST /api/v1/live/start` → `app.state.live.start()` (TL/backend/src/trade_lab/api/app.py:380-390; a 409 guard refuses start while a replay is active, app.py:383-388). `LiveMarketDataService` is constructed at app creation with `historical_source=historical_source, throttle_warm_start=True` (TL/backend/src/trade_lab/api/app.py:327-336).

Inside `LiveMarketDataService.start()` (TL/backend/src/trade_lab/services/live.py:211): after the runtime reset (live.py:228-235, `reset_runtime_on_start=True` default at live.py:76) the seed is awaited at live.py:236-238 — "W2 P1c: the prior trading day's PDH/PDL through the SAME seed path research and cold replay use — before any event reaches the engine" — via `await self._load_prior_day_summary()`. The plugin write-through chain from `self.runtime.levels.load_prior_day_summary(day, high_ticks, low_ticks)` (TL/backend/src/trade_lab/services/live.py:330):

1. `ApplicationRuntime.levels` is an alias for `StrategyCoreService` (TL/backend/src/trade_lab/services/runtime.py:170-172, re-bound on reset at runtime.py:251).
2. `StrategyCoreService.load_prior_day_summary(trading_day, high_ticks, low_ticks)` → `self._runtime.load_prior_day_summary(...)` (TL/backend/src/trade_lab/services/strategy_core_service.py:144-149).
3. `StrategyRuntime.load_prior_day_summary` → `self._plugin.load_prior_day_summary(...)` ("S-B3a: written through to the plugin's level state — the sole level fold", SC/src/strategy_core/runtime/state.py:274-276).
4. `TouchReversalPlugin.load_prior_day_summary` → `self._levels.load_prior_day_summary(...)` (SC/src/strategy_core/strategies/touch_reversal/plugin.py:241-243).
5. `StrategyLevelState.load_prior_day_summary` stores `self._summaries[trading_day] = _DaySummary(high_ticks, low_ticks)` (SC/src/strategy_core/runtime/levels.py:61-64).

`set_static_levels` exists on the same chain (live path does not call it; SC/src/strategy_core/runtime/state.py:270-272, plugin.py:237-239) — no TL live call site found.

#### (b) SOURCE of the seed values

Databento **Historical API**, `ohlcv-1h` schema, continuous symbol — NOT the local parquet store and NOT a cached file. `LiveMarketDataService._load_prior_day_summary` calls `frame = await asyncio.to_thread(lambda: source.ohlcv_frame(start=start, end=end))` (TL/backend/src/trade_lab/services/live.py:324). `DatabentoHistoricalSource.ohlcv_frame` issues `client.timeseries.get_range(dataset=self._dataset, schema=_OHLCV_SCHEMA, symbols=[self._requested_symbol], stype_in=self._stype_in, start=start, end=end)` with `_OHLCV_SCHEMA = "ohlcv-1h"` and returns `store.to_df()` (TL/backend/src/trade_lab/adapters/databento_historical.py:33, 98-113; the client is `databento.Historical(self._api_key)`, databento_historical.py:124-126). The source is constructed once in `create_app` with `dataset=settings.databento_dataset, requested_symbol=settings.databento_requested_symbol, stype_in=settings.databento_stype_in` (TL/backend/src/trade_lab/api/app.py:299-308); defaults are `GLBX.MDP3` / `NQ.c.0` / `continuous` (TL/backend/src/trade_lab/config.py:43-45). Unlike `dbn_record_streams`, `ohlcv_frame` does NOT clamp `end` to dataset availability (`_clamp_end_to_available` is called only at databento_historical.py:79, not in ohlcv_frame at 98-113). If the source is unavailable or the frame is empty the seed is **skipped with a warning** — "PDH/PDL emit only after the first in-stream day roll" (live.py:314-319, 325-327, 334-339); there is NO backward walk to an earlier non-empty day (contrast: the QL cache warmer's most-recent-non-empty-day walk).

#### (c) KEY convention — how "prior trading day" is chosen

`day = prior_trading_day(trading_day_for(self._now()))` (TL/backend/src/trade_lab/services/live.py:322), where `self._now` defaults to `datetime.now(UTC)` at (re)start time (live.py:139). `trading_day_for` assigns a UTC instant to the ET 18:00-roll trading day (TL/backend/src/trade_lab/domain/trading_day.py:17-24); `prior_trading_day` is a **weekday-only calendar walk**: "The previous Mon-Fri calendar date (Friday for a Monday trading day)" — `day -= 1; while day.weekday() >= 5: day -= 1` (trading_day.py:42-48). Exchange holidays are **deliberately not modeled**: "an empty fetch is surfaced by the caller instead of silently guessed around" (trading_day.py:5-7). This is neither an exchange calendar nor a most-recent-available-day rule.

The lookup side is forgiving: `StrategyLevelState.levels()` resolves PDH/PDL from `max((day for day in self._summaries if day < self._trading_day), default=None)` — the most recent banked summary strictly before the current trading day, not an exact prev-day key (SC/src/strategy_core/runtime/levels.py:96-100).

The **separate** 2-prior-trading-day window (bars, not the seed) is computed in the feed adapter: `day = trading_day_for(self._now()); for _ in range(_WARM_START_PRIOR_TRADING_DAYS): day = prior_trading_day(day); replay_start = trading_day_start_utc(day)` with `_WARM_START_PRIOR_TRADING_DAYS = 2` (TL/backend/src/trade_lab/adapters/databento.py:59-61, 238-241).

#### (d) COMPUTATION of high/low

Full 18:00→18:00 ET trading-day extremes (NOT RTH-only): `start, end = trading_day_bounds_utc(day)` = `[prev 18:00 ET, 18:00 ET)` (TL/backend/src/trade_lab/services/live.py:323; trading_day.py:51-56). Reduction, quoted exactly (live.py:328-330):

```python
high_ticks = price_to_ticks(str(float(frame["high"].max())))
low_ticks = price_to_ticks(str(float(frame["low"].min())))
self.runtime.levels.load_prior_day_summary(day, high_ticks, low_ticks)
```

i.e. max of hourly-bar `high` / min of hourly-bar `low` from the `ohlcv-1h` DataFrame as returned by `store.to_df()` (databento_historical.py:112) — no explicit fixed-point/1e-9 scaling in TL; the value round-trips float→str→Decimal in `price_to_ticks`, which divides by `NQ_TICK_SIZE = Decimal("0.25")` and **raises** `PriceError` on any non-tick-aligned price rather than rounding (TL/backend/src/trade_lab/domain/prices.py:10, 26-40). Any exception in the fetch/reduction is caught and downgraded to a warning (live.py:334-339). The symbol is continuous `NQ.c.0` (single-instrument by construction), so the local-store front-month-only concern does not apply to this path.

#### (e) LEVELS vs BARS/candles — two distinct fetches

**LEVELS** come from the small **1-prior-trading-day `ohlcv-1h`** fetch only (live.py:322-330, chain in (a)). The 2-day warm-start record fetch does not feed `load_prior_day_summary` anywhere — no other TL call site of `load_prior_day_summary` exists on the live path.

**BARS/candles** come from the **2-prior-trading-day `dbn_record_streams`** fetch (trades + mbp-1) staged in `DatabentoMarketDataFeed.start()` (TL/backend/src/trade_lab/adapters/databento.py:230-251; fetch at 289-305 with `schemas=(self.trade_schema, self.quote_schema)`), drained in `events()` BEFORE the live client is created (`self._connect(replay_start=None)` only after the drain, databento.py:352-362), each record normalized through the same `normalize_provider_message` path live records take (databento.py:437-470). They then flow: `CoreLiveRuntime(process_item=self._process_live_item, ...)` (live.py:250-257) → `runtime.process_market_event` (live.py:354) → `ApplicationRuntime.process_market_event` ("only trades advance bars/touches", TL/backend/src/trade_lab/services/runtime.py:704-713) → `StrategyCoreService.process_market_event` (strategy_core_service.py:160-171). Retention for the deeper warm start: `recent_closed_bar_limit=8_000` (TL/backend/src/trade_lab/api/app.py:241-244) threaded through `ApplicationRuntime` (runtime.py:143, 167) into `StrategyRuntime` (strategy_core_service.py:95, 110); the frontend cap matches at `MAX_BARS_PER_TIMEFRAME = 8_000` (TL/frontend/src/chart/viewModels.ts:8, commit 05ef9f6).

**Cross-feed fact:** the replayed 2-day trade stream ALSO reaches the level engine organically — `StrategyLevelState.process_trade` banks the completed day's extremes at each in-stream day roll ("An explicit load_prior_day_summary for the same day stays authoritative … `and self._trading_day not in self._summaries`", SC/src/strategy_core/runtime/levels.py:66-90, guard at 75-81). So post-fce7846 the live path has TWO PDH/PDL sources: the explicit ohlcv-1h seed (wins for its day because it is loaded before any event) and trade-derived organic banking from the replayed days (supplies D-2's summary during the replayed D-1 segment, and is the only source if the ohlcv seed was skipped). Asia/London session ranges for the replayed days come solely from the replayed trades (levels.py:88-89).

**Stale-comment fact:** the "FALLBACK when the live gateway rejects the intraday replay-start subscribe" wording survives in the module docstring (databento_historical.py:1-17) and at databento.py:221-224, but commit fce7846 deleted the gateway replay-start subscribe — `_connect` is now always called with `replay_start=None` (databento.py:251, 362) and the Historical fetch is unconditional when `intraday_replay=True` (set at app.py:321-324).

#### (f) Ordering, throttle, reconnect

**Seed-before-first-event:** in `start()` the order is runtime reset (live.py:228-235) → `await self._load_prior_day_summary()` (live.py:238) → feed construction + `CoreLiveRuntime.start()` (live.py:248-259). The seed therefore lands before any warm-start or live event reaches the engine. Within the feed, the historical drain in `events()` precedes the live subscribe (databento.py:352-362), and the seam gap between the availability-clamped historical end and the live subscribe is logged (`_log_warm_start_seam_gap`, databento.py:316-328; `last_stream_end` set at databento_historical.py:74, 80).

**Re-applied on reconnect:** a feed disconnect schedules `_reconnect_after_disconnect` → `await self.start()` after `reconnect_delay_seconds` (live.py:377-383, 398-409) — "reconnect = the same warm-start path. start() resets the runtime, reloads the prior-day seed, and replays the trading day" (live.py:380-382). Since `runtime.reset()` rebuilds `StrategyCoreService` (fresh `StrategyLevelState._summaries`, runtime.py:245-251; levels dict created at SC levels.py:48), every (re)start both wipes and re-seeds; the prior-day key is recomputed from the wall clock at that moment (live.py:322, 139).

**Throttle is broadcast-only:** `throttle_warm_start=True` (app.py:335) suppresses per-event WebSocket deltas while the processed frontier lags wall clock by > 30 s, emitting throttled snapshots instead, and resumes streaming within 5 s of real time (live.py:52-54, 437-466). It does not affect the seed, engine processing, or event ordering. Warm/live phase marking is timestamp-based (`_mark_warm_start`: event ts < the start wall clock ⇒ "warming", live.py:356-364).

---

## 3. QL training seed

#### (a) The rolling prev_full_hl carry

`build_utility_dataset` (QL/src/alpha_lab/agents/data_infra/ml/dashboard_utility_builder.py:110) initializes the carry once before the loop and threads it day-to-day:

```python
# Track the prior FULL trading day's high/low for PDH/PDL (engine v3) — the
# cold-start seed for each single-day stream drive.
prev_full_hl: tuple[float, float] | None = None

for i, date_str in enumerate(sorted(dates)):
```
(QL/src/alpha_lab/agents/data_infra/ml/dashboard_utility_builder.py:147-151)

What is carried is a plain Python `tuple[float, float] | None` of **prices in POINTS** (e.g. `(25465.25, 25058.25)` — the guard-test constant at QL/tests/agents/test_cache_seed_guard.py:20), NOT ticks and NOT a dict. Conversion to ticks happens only at seed-load time inside the engine drive: `_round_to_ticks(prev_day_hl[0], TRADE_TICK)` with `TRADE_TICK = 0.25` (QL/src/alpha_lab/agents/data_infra/ml/engine_decision.py:86, 108-109, 647-652).

Per iterated day the loop does, in order: (1) trust-check + read cache OR build fresh via `_process_single_date(date_str, ..., prev_full_hl)` — the ENTERING seed is what the engine drive is seeded with (dashboard_utility_builder.py:161-187); (2) reassign `prev_full_hl = _get_session_hl_for_date(data_dir, symbol, date_str, util_cfg, prev_full_hl)` — the carry update for the NEXT day (dashboard_utility_builder.py:189-192; cache-hit twin at 166-169); (3) write the cache (dashboard_utility_builder.py:194-196). `_process_single_date` passes the seed to `process_single_date_stream(..., prev_day_hl=prev_full_hl)` (dashboard_utility_builder.py:266-274), which calls `runtime.load_prior_day_summary(td - timedelta(days=1), high_ticks=..., low_ticks=...)` (QL/src/alpha_lab/agents/data_infra/ml/engine_decision.py:647-652) — the same SC lifecycle method TL serving uses (SC/src/strategy_core/runtime/state.py:274-276 → SC/src/strategy_core/strategies/touch_reversal/plugin.py:241-243 → SC/src/strategy_core/runtime/levels.py:61-64).

#### (b) _get_session_hl_for_date semantics

Quoted in full (QL/src/alpha_lab/agents/data_infra/ml/dashboard_utility_builder.py:230-249):

```python
def _get_session_hl_for_date(
    data_dir: Path,
    symbol: str,
    date_str: str,
    util_cfg: DashboardUtilityConfig,
    prev_full_hl: tuple[float, float] | None,
) -> tuple[float, float] | None:
    """This date's FULL trading-day H/L — the NEXT day's PDH/PDL seed.

    Engine v3: max-high / min-low over the entire [18:00, 18:00) ET window (the
    daily-candle extremes). W1 P4b: the asia/london carry is gone — session
    extremes are folded per-trade inside the engine's level state during the
    stream drive, never sliced from bar closes here.
    """
    bars = _build_bars_for_date(data_dir, symbol, date_str, util_cfg)
    if bars.empty:
        return prev_full_hl

    bars_et = _ensure_et_index(bars)
    return (float(bars_et["high"].max()), float(bars_et["low"].min()))
```

- **Window**: `_build_bars_for_date` computes `start_utc = prev_day 18:00 America/New_York`, `end_utc = date 18:00 America/New_York` where `prev_day = td - timedelta(days=1)` — the prior CALENDAR day's 18:00 ET, DST-aware, converted to UTC (dashboard_utility_builder.py:287-298). The DuckDB where-clause is half-open `[start, end)` (QL/src/alpha_lab/agents/data_infra/tick_store.py:609-610).
- **Rows**: D-036 pins `bar_type=147t` (QL/docs/DECISIONS.md:309), so the `.endswith("t")` branch runs `store.build_tick_bars(symbol, start_utc, end_utc, tick_count=147, price_source="trade")` (dashboard_utility_builder.py:321-336). That selection is **trades only** (`AND lower(CAST(action AS VARCHAR)) = 't'` plus `price IS NOT NULL AND price > 0`, tick_store.py:692-693) and **front-month only** — the count-based most-frequent non-spread symbol: `SELECT symbol, count(*) ... WHERE symbol NOT LIKE '%-%' ... ORDER BY n DESC LIMIT 1` → `AND symbol = '<front>'` (tick_store.py:655-664). The trailing `<tick_count` bucket is KEPT as an incomplete bar ("no HAVING", tick_store.py:549-552), so the bar-extreme max/min covers every front-month trade print in the window. (The `bar_type == "1m"` branch has a cached `ohlcv_1m_session.parquet` shortcut at dashboard_utility_builder.py:308-314 — not the D-036 path.)
- **Empty/absent day**: returns the **passed-in `prev_full_hl` unchanged** (line 245-246) — carry-through, not None.
- **Seed use**: the passed seed is used ONLY as that empty-day fallback return value; it never influences the computed H/L (a non-empty day's own extremes are returned regardless of the seed — this is what makes the warmer's `None`-seeded probe equivalent, QL/scripts/w3_cache_warmer.py:180-183).

#### (c) KEY convention — whose H/L seeds day D

The seed for day D is the H/L of the **most-recent NON-EMPTY prior WINDOW day** — i.e., prior entry in the `sorted(dates)` list being iterated (dashboard_utility_builder.py:151), NOT the prior calendar day and NOT unconditionally the prior store day. In the D-036 pipeline the `dates` list is the Workbench's `dates_in_range` = store-directory scan (`get_available_dates`, one dir per session with a tick file, QL/scripts/ml_training_tab.py:52-61) filtered to the UI-selected range (ml_training_tab.py:2226-2229) and passed to `build_utility_dataset(dates_in_range, ...)` (ml_training_tab.py:2317-2322). Since the store is a curated subset, the prior window day equals the prior STORE day within the selected range, which can be arbitrarily far before D's prior calendar/trading day.

**Empty prior day**: because `_get_session_hl_for_date` returns the incoming seed unchanged on empty bars (dashboard_utility_builder.py:244-246), the serial loop carries THROUGH empty days to the most-recent non-empty one. The cache warmer replicates exactly this with an explicit backward walk: `for k in range(idx - 1, -1, -1): hl = _get_session_hl_for_date(..., window_dates[k], util_cfg, None); if hl is not None: return hl` (QL/scripts/w3_cache_warmer.py:169-197, docstring 176-183).

**Key date inside the engine**: the seed is registered under `td - timedelta(days=1)` (prior CALENDAR day, engine_decision.py:648-649), but SC's level emitter selects `prior = max((day for day in self._summaries if day < self._trading_day), default=None)` (SC/src/strategy_core/runtime/levels.py:96) — so the summary's key date only needs to be `< trading_day`; the H/L VALUES are what carry the prior-window-day semantics.

#### (d) The parquet seed-stamp + the 098e354 guard

`098e354ff1f878e6f3d2e9e16138c9143d990ebf` — "fix(ml): guard build_utility_dataset against seedless/wrong-seed day caches", 2026-06-17, immediately after the warmer commits (`5eb6f5b`, `aa05334`) in QL history. Motivation per the commit message: a standalone timing build wrote a `prev_full_hl=None` cache for 2026-02-12 that was silently consumed, surfacing as a W3b RED (train=4 vs serving=5; the missing `pdl|long @ 25058.25`).

- **Stamp**: `_write_day_cache` writes the seed into parquet **schema metadata** under key `b"ml_utility_prev_full_hl"`, serialized `f"{seed[0]!r},{seed[1]!r}"` (repr → exact float round-trip) or `b"none"` for a None seed (dashboard_utility_builder.py:54-78). No seed columns — file-level metadata only.
- **Guard at read**: the trust path is `if cache_path.exists() and _cache_seed_matches(cache_path, prev_full_hl):` (dashboard_utility_builder.py:161) — a mismatch logs "Rebuilding ... seed stamp does not match prev_full_hl=... (stale/seedless cache guard)" and rebuilds (dashboard_utility_builder.py:171-187). `_cache_seed_matches` (dashboard_utility_builder.py:81-107): unstamped/`none`-stamped caches match ONLY when `expected_seed is None`; a stamped cache matches only on float-exact `(high, low)` equality; unreadable metadata → False. The warmer applies the same check on its resumable-skip path and unlinks+rebuilds on mismatch (w3_cache_warmer.py:227-235).
- **STAMP-ORDERING FACT (builder vs warmer divergence)**: in `build_utility_dataset`, `_write_day_cache(df, cache_path, prev_full_hl)` (dashboard_utility_builder.py:195) executes AFTER `prev_full_hl` has been reassigned to day D's OWN H/L (dashboard_utility_builder.py:189-192). So a builder-written cache for day D is stamped with **day D's own H/L (the NEXT day's seed)** — not the seed the cache was built with — while the read-time check at line 161 compares the stamp against the seed ENTERING day D. The warmer stamps the ENTERING seed: `_write_day_cache(frame, cache_path, seed)` where `seed = _seed_for_day(...)` (w3_cache_warmer.py:222, 238-240). The two writers therefore use different stamp conventions against the same guard; the 098e354 diff shows the builder's write hunk replaced the old `df.to_parquet` at that same post-update position, so this ordering dates from the guard's introduction and is unchanged at HEAD 0b4a2e8 (098e354 is the last commit touching the builder). The guard test does not exercise the loop ordering — it calls `_write_day_cache` directly with a caller-chosen seed (QL/tests/agents/test_cache_seed_guard.py:23-43). Mechanical consequence: a builder-written day-D cache re-encountered on a later run matches only if D's own H/L equals the seed entering D; a warmer-written cache matches the serial loop's expectation by construction.

#### (e) Cold start — first day of a build window

The first iterated day enters with `prev_full_hl = None` (dashboard_utility_builder.py:149). `process_single_date_stream` then SKIPS the seed load entirely: `if prev_day_hl is not None: runtime.load_prior_day_summary(...)` (engine_decision.py:647-652). Inside SC, `_summaries` stays empty, so the level emitter's `prior = max((day for day in self._summaries if day < self._trading_day), default=None)` is None and **no `pdh`/`pdl` Level is appended at all** that day (SC/src/strategy_core/runtime/levels.py:92-100) — asia/london session levels still emit from the day's own trades (levels.py:101-107). The organic day-roll banking (levels.py:70-81) only banks a completed day's extremes on a MULTI-day stream's day change, so a one-day batch drive never self-seeds — the docstring states this: "The organic day-roll banking covers multi-day streams; one-day batch drives need the seed" (engine_decision.py:611-612). There is no pre-window computation: the first window day simply has no PDH/PDL touches. Nothing raises on a None seed; `load_prior_day_summary` itself only validates `high_ticks >= low_ticks` when it IS called (levels.py:61-64).

#### (f) Mid-window — where the carry value comes from

The carry is ALWAYS a fresh recompute of the previous iterated day's OWN H/L from store bars — never a re-read of the cache and never the cache stamp. On the fresh-build path the update is dashboard_utility_builder.py:189-192; on the **cache-hit path the H/L computation is NOT skipped** — the loop still recomputes it before `continue`: "`# Still need this date's full H/L as the next day's PDH/PDL seed.` `prev_full_hl = _get_session_hl_for_date(data_dir, symbol, date_str, util_cfg, prev_full_hl)`" (dashboard_utility_builder.py:166-170). So cache hits save the engine drive (touch detection/labeling) but still pay one `_build_bars_for_date` bar build per day for the carry; the carried value on a cache-hit day is identical to a fresh-build day's (same function, same store inputs). The only case where the carry is not the previous day's own H/L is the empty-bars pass-through of (b)/(c).

---

## 4. SC resolution semantics

#### (a) Implementation chain and what is banked

The touch_reversal plugin method is a pure delegation — SC/src/strategy_core/strategies/touch_reversal/plugin.py:241-243:

```python
def load_prior_day_summary(self, trading_day: date, *, high_ticks: int, low_ticks: int) -> None:
    """Seed the prior-day PDH/PDL summary (written through the runtime's lifecycle method)."""
    self._levels.load_prior_day_summary(trading_day, high_ticks=high_ticks, low_ticks=low_ticks)
```

The runtime lifecycle method is likewise a pure write-through: `StrategyRuntime.load_prior_day_summary` → `self._plugin.load_prior_day_summary(...)` (SC/src/strategy_core/runtime/state.py:274-276, comment "S-B3a: written through to the plugin's level state — the sole level fold"). The plugin OWNS the level state (`self._levels: StrategyLevelState`, plugin.py:212, rebuilt in `configure()` at plugin.py:225-228).

The terminal store is `StrategyLevelState.load_prior_day_summary` — SC/src/strategy_core/runtime/levels.py:61-64:

```python
def load_prior_day_summary(self, trading_day, *, high_ticks: int, low_ticks: int) -> None:
    if high_ticks < low_ticks:
        raise ValueError("high_ticks must be >= low_ticks")
    self._summaries[trading_day] = _DaySummary(high_ticks, low_ticks)
```

BANKED: key = the caller-supplied `trading_day` (the date the H/L belongs to; the protocol types it `date`, SC/src/strategy_core/strategies/protocols.py:270, but the store dict is typed `dict[object, _DaySummary]` — levels.py:48 — so the key type is unenforced at the store). Value = frozen `_DaySummary(high_ticks, low_ticks)` (levels.py:33-36), i.e. integer tick extremes, converted to price only at emission (`summary.high_ticks * self._tick_size`, levels.py:99).

#### (b) Lookup semantics — most-recent-banked-before-D, CONFIRMED as "W2 P1f"

The lookup is NOT an exact D-1 match. Emission happens inside `StrategyLevelState.levels()` — SC/src/strategy_core/runtime/levels.py:92-100:

```python
if self._trading_day is None:
    return self._static_levels
levels: list[Level] = list(self._static_levels)
prior = max((day for day in self._summaries if day < self._trading_day), default=None)
if prior is not None:
    summary = self._summaries[prior]
    levels.append(Level("pdh", summary.high_ticks * self._tick_size, Side.HIGH, self._trading_day_start_available()))
    levels.append(Level("pdl", summary.low_ticks * self._tick_size, Side.LOW, self._trading_day_start_available()))
```

i.e. the MOST RECENT banked key strictly LESS THAN the current trading day (levels.py:96). This is the sole runtime pdh/pdl emission point in SC src (grep for `"pdh"` hits only levels.py:99 plus a descriptor label at constants.py:221). The current trading day `self._trading_day` is set from each trade via `classify_session` (levels.py:67-70, 82) under the 18:00 ET rollover (SC/src/strategy_core/decisions/sessions.py:98-101; `TRADING_DAY_BOUNDARY = time(18, 0)` at SC/src/strategy_core/constants.py:145).

The requester's description "the W2 P1f behavior — most-recent-banked emission" is CONFIRMED. The ratifying pin is a test, not a comment in levels.py: SC/tests/test_runtime_levels.py:69-72 — `test_friday_bank_serves_monday_pdh_pdl_across_the_weekend_gap`: `"""W2 P1f rider: the emission lookup resolves the MOST RECENT banked day with key < the current trading day, so a Friday bank serves Monday across the weekend gap (verified: no fix needed — this pins the behavior)."""` (same rider text preserved in SC/W2_SC_DIFF.txt:318; the commit is logged as "test(runtime): W2 P1f weekend-gap PDH/PDL pin" in SC/ARCH_STATE_RECON.md:382). The test asserts a Friday bank emits as Monday's pdh/pdl (test_runtime_levels.py:73-87).

#### (c) Availability guard interplay — a seed is available for the WHOLE current trading day, regardless of when it was loaded

The emitted pdh/pdl `Level.available_from` is `_trading_day_start_available()` (levels.py:99-100), which is derived from the CURRENT trading day, not from the banked key or the wall-clock load time — SC/src/strategy_core/runtime/levels.py:113-117:

```python
def _trading_day_start_available(self) -> datetime:
    tz = ZoneInfo(self._scheme.timezone)
    local_date = self._trading_day - timedelta(days=1)  # type: ignore[operator]
    local_dt = datetime.combine(local_date, self._scheme.trading_day_boundary, tzinfo=tz)
    return local_dt.astimezone(ZoneInfo("UTC"))
```

i.e. 18:00 ET on the calendar evening before trading day D (`SESSION_TIMEZONE = "US/Eastern"`, `TRADING_DAY_BOUNDARY = time(18, 0)` — constants.py:144-145). The v3 enforcement is downstream: `build_zones` sets zone availability to the MAX of constituent levels' `available_from` (SC/src/strategy_core/decisions/zones.py:87-94), and `detect_touches` skips a zone whose availability postdates the bar close — `if zone.available_from is not None and bar.close_ts_utc < zone.available_from: continue` — WITHOUT consuming first-touch (SC/src/strategy_core/decisions/touch.py:98-102; docstring touch.py:78-86). Because every bar of trading day D closes at/after D's 18:00-ET start, a pdh/pdl seed loaded MID-DAY passes the guard immediately — it becomes touchable on the very next decision bar after it first emits. Two timing caveats, both cited: (1) the seed does not EMIT until the first trade sets `self._trading_day` (`levels()` returns static levels only while `self._trading_day is None`, levels.py:93-94); (2) if pdh/pdl merges into a zone with a later-available constituent (e.g. london_high), the merged zone is gated on the MAX (zones.py:87-94).

#### (d) Masking analysis — key-convention mismatches are silently absorbed

There is no error, warning, or exact-key assertion anywhere in the seed/lookup path. `load_prior_day_summary` validates ONLY `high_ticks >= low_ticks` (levels.py:62-63); the lookup at levels.py:96 is a bare `max(... if day < self._trading_day)` with `default=None`; levels.py contains no logging import or warning emission anywhere (full file, levels.py:1-125), and the runtime's `DataQualityWarning` channel (state.py:278-283) is never invoked from level seeding or emission. Consequences, mechanically: (1) if QL banks under "prior window day" (e.g. the most-recent non-empty STORE day) while TL banks under "exchange prior trading day", BOTH keys are `< D`, so `max()` silently emits whichever key sorts LATER — no signal distinguishes them; (2) if a single seeder uses the "wrong" convention but its key is still `< D`, the values emit identically as `pdh`/`pdl` with the same current-day `available_from` (levels.py:99-100) — the key never appears in the emitted `Level` at all (name/price/side/available_from only), so downstream consumers cannot see which day the values came from; (3) if both seeders bank (e.g. a TL live seed survives into a replay — see (e) reset semantics), two entries coexist in `_summaries` and the later key silently wins. The only mismatch that surfaces at all is a key `>= D`, which is silently NEVER emitted (excluded by `day < self._trading_day`, levels.py:96) — dropped, not errored.

#### (e) Multiple calls, future/past keys, lifecycle survival

- Multiple calls, same key: plain dict overwrite (`self._summaries[trading_day] = _DaySummary(...)`, levels.py:64) — last explicit call wins. Multiple calls, different keys: ACCUMULATE — `_summaries` is never pruned; only the max-below-D entry ever emits (levels.py:96).
- Explicit-vs-organic precedence: the day-roll banks the completed day organically only `if ... self._trading_day not in self._summaries` (levels.py:75-81), per the comment "An explicit load_prior_day_summary for the same day stays authoritative (the external seed is never overwritten by the organic roll; a later explicit load overwrites)" (levels.py:71-74); pinned by `test_explicit_prior_day_load_wins_over_organic_banking` (SC/tests/test_runtime_levels.py:46-56).
- Future/same-day key: NO validation on `trading_day` at load (levels.py:61-64 checks only tick ordering). A key `>= self._trading_day` is accepted and stored but silently excluded from emission (levels.py:96); if the stream later rolls past it, it becomes emittable. A stale past key is accepted and WILL emit whenever it is the most recent banked key below D.
- Reset vs configure: `StrategyLevelState.reset()` clears `_trading_day`/`_day_high`/`_day_low`/`_ranges` but NOT `_summaries` (and not `_static_levels`) — levels.py:52-56 — so banked seeds SURVIVE `runtime.reset()` (state.py:253-259 → `self._plugin.reset()` at state.py:259 → plugin.py:230-235 → `self._levels.reset()`). By contrast `plugin.configure()` REBUILDS the level state wholesale (`self._levels = StrategyLevelState(scheme=..., tick_size=...)`, plugin.py:225-228), discarding all banked summaries. Whether a TL seed persists across a replay restart therefore depends on whether the restart path calls `configure()` (wipes) or only `reset()` (preserves).

---

### Grounding annex: store + import surface (feeds §5)

#### (a) Store locations — TL and QL resolve to the SAME physical directory

- **TL configured store**: `Settings.data_path: Path | None = None` (TL/backend/src/trade_lab/config.py:30), loaded with `env_prefix="TRADE_LAB_"` from `backend/.env` (TL/backend/src/trade_lab/config.py:20-25, env file resolved at config.py:14). The local `backend/.env` sets `TRADE_LAB_DATA_PATH=C:\Users\gonza\Documents\Trade-Dashboard\data\databento\NQ` (local-only file, not in repo). It is consumed by `build_replay_catalog(data_path=settings.data_path, ...)` (TL/backend/src/trade_lab/api/app.py:341-345; catalog impl TL/backend/src/trade_lab/adapters/replay_catalog.py:101-116). Note the TL path points at the **symbol dir itself** (`...\databento\NQ`).
- **QL data dir**: package default `_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[4] / "data" / "databento"` → `C:\Users\gonza\Documents\Claude-Quant-Lab\data\databento` (QL/src/alpha_lab/agents/data_infra/ingest.py:29); the training scripts' default is `_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[0].parent / "data" / "databento"` (QL/scripts/ml_training_tab.py:32), re-exported into `run_dashboard_session_experiment.py` (QL/scripts/run_dashboard_session_experiment.py:22-23, `--data-dir` default at :65). QL appends the symbol: `symbol_dir = data_dir / symbol` (QL/scripts/ml_training_tab.py:54). So QL's effective NQ root = `C:\Users\gonza\Documents\Claude-Quant-Lab\data\databento\NQ`.
- **Same directory**: `C:\Users\gonza\Documents\Trade-Dashboard\data` is a `<SYMLINKD>` → `C:\Users\gonza\Documents\Claude-Quant-Lab\data` (verified via `dir /AL`; link created 2026-03-19). The two NQ roots list byte-identically (314 entries each, identical names/sizes/mtimes). TL's catalog explicitly tolerates a symlinked root (`if not root.exists() and not root.is_symlink()` — TL/backend/src/trade_lab/adapters/replay_catalog.py:124).
- **Contents**: 314 entries = **312 date dirs** (2021-12-02 … 2026-02-22) + 2 stray files (`trades_20260214_20260224.parquet`, `trades_20260217_20260224.parquet`). Of the 312 date dirs, **310 contain `mbp10.parquet`**; the 2 without are `2025-11-20` (EMPTY dir) and `2026-02-14` (only `ohlcv_1m_20260214_20260224.parquet`).

#### (b) Probe-day presence (all 7 present with mbp10.parquet)

| probe day | mbp10.parquet size (bytes) | 3 nearest preceding store dates |
|---|---|---|
| 2026-02-18 | 905,267,069 | 2026-02-15, 2026-02-16, 2026-02-17 |
| 2026-02-17 | 1,428,312,684 | **2026-02-14 (NO mbp10)**, 2026-02-15, 2026-02-16 |
| 2026-01-20 | 1,149,691,825 | 2026-01-16, 2026-01-18, 2026-01-19 |
| 2026-01-12 | 615,860,626 | 2026-01-08, 2026-01-09, 2026-01-11 |
| 2025-12-26 | 305,801,748 | 2025-12-23, 2025-12-24, 2025-12-25 |
| 2025-11-21 | 1,944,544,121 | 2025-11-18, 2025-11-19, **2025-11-20 (EMPTY dir)** |
| 2025-07-15 | 588,518,540 | 2025-07-11, 2025-07-13, 2025-07-14 |

Preceding-day mbp10 sizes: 2026-02-15=16,521,855; 2026-02-16=292,063,958; 2026-01-16=699,318,060; 2026-01-18=28,662,373; 2026-01-19=374,747,288; 2026-01-08=720,471,055; 2026-01-09=705,365,280; 2026-01-11=11,118,304; 2025-12-23=387,130,745; 2025-12-24=181,980,959; **2025-12-25=6,322,465 (present — Christmas Day has a tiny but non-empty file)**; 2025-11-18=1,580,575,617; 2025-11-19=1,316,080,509; 2025-07-11=511,783,158; 2025-07-13=21,026,079; 2025-07-14=395,729,630. 2025-12-26 is PRESENT, so no fallback naming needed; the post-Christmas run is contiguous (2025-12-24, 12-25, 12-26, 12-28, 12-29 … all present). Load-bearing for the seed script: `get_available_dates` requires one of `["mbp10.parquet","mbp1.parquet","trades.parquet"]` (QL/scripts/ml_training_tab.py:49,58-60), so **2025-11-20 and 2026-02-14 are excluded from QL's date list** — the "prior available date" of 2025-11-21 is 2025-11-19 and of 2026-02-15 is 2026-02-13, not the calendar predecessors. Note 2025-11-21 is the **first day of the D-036 training window** (`--start 2025-11-21 --end 2026-02-13`, QL/scripts/w3_cache_warmer.py:81-82), so its seed walk has no prior in-window day.

#### (c) Script import surface

- **(i) QL seed function**: `def _get_session_hl_for_date(data_dir: Path, symbol: str, date_str: str, util_cfg: DashboardUtilityConfig, prev_full_hl: tuple[float, float] | None) -> tuple[float, float] | None` (QL/src/alpha_lab/agents/data_infra/ml/dashboard_utility_builder.py:230-236). Returns "This date's FULL trading-day H/L — the NEXT day's PDH/PDL seed" in POINTS: `(float(bars_et["high"].max()), float(bars_et["low"].min()))` (:249); an empty day returns the passed-through `prev_full_hl` unchanged (:245-246). Its window comes from `_build_bars_for_date`: `start_utc = pd.Timestamp(f"{prev_day.isoformat()} 18:00:00", tz="America/New_York")…`, `end_utc = pd.Timestamp(f"{td.isoformat()} 18:00:00", tz="America/New_York")…` (:295-298) — [prev-day 18:00 ET, day 18:00 ET), DST-aware. For `bar_type == "1m"` it will prefer a cached `ohlcv_1m_session.parquet` (:307-314); D-036 uses `--bar-type 147t` (QL/scripts/w3_cache_warmer.py:80), which builds tick bars via `TickStore`. The serial carry it feeds: `prev_full_hl = _get_session_hl_for_date(...)` per date in `build_utility_dataset` (:190-192). The warmer's replication (`_seed_for_day`) walks back from the immediate predecessor **within `window_dates`** passing seed `None`, taking the first non-`None` result (QL/scripts/w3_cache_warmer.py:169-197). A second, legacy `_get_session_hl_for_date` exists at QL/src/alpha_lab/agents/data_infra/ml/legacy_decision.py:56 — parity/regression tests only (dashboard_utility_builder.py:11-13); do not import that one.
- **(ii) QL date-list resolution**: `def get_available_dates(symbol: str, data_dir: Path) -> list[str]` — sorted `symbol_dir.iterdir()` dirs containing any of `_TICK_FILENAMES = ["mbp10.parquet", "mbp1.parquet", "trades.parquet"]` (QL/scripts/ml_training_tab.py:49,52-61). Window slicing: `_date_slice(available, start, end)` = lexicographic `[d for d in available if start_value <= d <= end_value]` (QL/scripts/run_dashboard_session_experiment.py:48-53). The warmer resolves `available = exp.get_available_dates(ns.symbol, data_dir)` then `window_dates = exp._date_slice(available, ns.start, ns.end)` (QL/scripts/w3_cache_warmer.py:314-315).
- **(iii) SC reader**: `strategy_core.data.databento_parquet.DatabentoParquetSource` (SC/src/strategy_core/data/databento_parquet.py:227-241). Canonical one-day constructor: `for_trading_day(cls, symbol_dir: Path | str, trading_day: date, *, requested_symbol: str | None = None, front_month_only: bool = True, batch_size: int = 65_536)` (:248-256); window = `datetime.combine(prev_day, TRADING_DAY_BOUNDARY, tzinfo=ZoneInfo(SESSION_TIMEZONE))` → UTC, ditto for `trading_day` (:277-279); two-file composition split at UTC midnight (:280,316-324); a missing/schema-mismatched prior-day file degrades to single-file with a `MISSING_PRIOR_DAY_FILE` warning (:283-315). Per-date file priority `DAY_FILE_PRIORITY = (("mbp10.parquet","mbp-10"),("mbp1.parquet","mbp-1"),("trades.parquet","trades"))` (:75-79). Iterate via `source.events() -> Iterator[Trade | Quote | DataQualityWarning]` (:357-362); trades are rows with `action in {"T","TRADE"}` for mbp-10 (:845-848). Front-month filter: spread rows masked (`raw_symbol` falsy → falls through to `symbol`; spread = contains `'-'`, :758-760 — this store has `symbol`, no `raw_symbol`) and instrument restricted to `_front_month_instrument_id` = "Dominant non-spread instrument by TRADE-row count (ties -> larger id)" (:1021-1031, applied :817-826). **Price units**: parquet `price` is a real-dollar double; the reader converts `ticks = values / DEFAULT_TICK_SIZE` with exact-grid validity `ticks == np.floor(ticks)` (:661-662), emitting `Trade.price_ticks: int` (:220-223). QL's canonical construction for reference: `DatabentoParquetSource.for_trading_day(Path(data_dir) / symbol, td, requested_symbol=symbol)` (QL/src/alpha_lab/agents/data_infra/ml/engine_decision.py:638-640).
- **(iv) Session boundary**: `SESSION_TIMEZONE = "US/Eastern"` (SC/src/strategy_core/constants.py:144), `TRADING_DAY_BOUNDARY = time(18, 0)  # CME 6pm ET rollover (UNCHANGED in v3)` (:145), single-sourced into `RESEARCH_SESSION_SCHEME` (:168-177) and into `for_trading_day` (SC/src/strategy_core/data/databento_parquet.py:21,278-279).
- **(v) PDH/PDL constant + tick conversion**: `PDH_PDL_SOURCE = "prior_day_full"` (SC/src/strategy_core/constants.py:213); `DEFAULT_TICK_SIZE = 0.25` (:28). Seeder conversions: QL uses `def _round_to_ticks(value: float, tick: float) -> int: return int(round(float(value) / tick))` with `TRADE_TICK = 0.25` and `assert TRADE_TICK == DEFAULT_TICK_SIZE` (QL/src/alpha_lab/agents/data_infra/ml/engine_decision.py:108-109,86,88), seeding `runtime.load_prior_day_summary(td - timedelta(days=1), high_ticks=_round_to_ticks(prev_day_hl[0], TRADE_TICK), low_ticks=_round_to_ticks(prev_day_hl[1], TRADE_TICK))` (:648-652) — **key = calendar prev day**. TL live uses strict-Decimal `price_to_ticks` (`ticks = value / NQ_TICK_SIZE`, integral required; TL/backend/src/trade_lab/domain/prices.py:26-38, `NQ_TICK_SIZE = Decimal("0.25")` :10) keyed by `prior_trading_day(trading_day_for(now))` (TL/backend/src/trade_lab/services/live.py:322,328-330). The key difference is benign to lookup: the level state selects `prior = max((day for day in self._summaries if day < self._trading_day), default=None)` (SC/src/strategy_core/runtime/levels.py:96) and emits `pdh/pdl` at `summary.high_ticks * self._tick_size` points (:99-100). Seed API: `load_prior_day_summary(self, trading_day: date, *, high_ticks: int, low_ticks: int) -> None` (SC/src/strategy_core/strategies/protocols.py:270-273; plugin write-through SC/src/strategy_core/strategies/touch_reversal/plugin.py:241-243; runtime SC/src/strategy_core/runtime/state.py:274-276; validation `high_ticks >= low_ticks` SC/src/strategy_core/runtime/levels.py:61-64).

#### (d) PYTHONPATH / env for a scratch script run from the TL root

- **Interpreter**: there is NO project venv in TL, TL/backend, or QL root (verified by listing); everything runs on the global `C:\Users\gonza\AppData\Local\Programs\Python\Python313\python.exe` (Python 3.13.1). Both TL backend and QL declare `requires-python = ">=3.13"` (TL/backend/pyproject.toml:10; QL/pyproject.toml:9).
- **No PYTHONPATH needed for the packages**: `strategy-core`, `alpha-signal-lab`, and `trade-lab` are all **editable installs** in that interpreter (site-packages contains `__editable__.alpha_signal_lab-0.1.0.pth`, `_editable_impl_strategy_core.pth`, `_editable_impl_trade_lab.pth`; `pip show strategy-core` → "Editable project location: C:\Users\gonza\Documents\strategy-core"). Verified: `import strategy_core` → `C:\Users\gonza\Documents\Strategy-core\src\strategy_core\__init__.py`; `import alpha_lab` → `C:\Users\gonza\Documents\Claude-Quant-Lab\src\alpha_lab\__init__.py`. **Consequence**: although both pyprojects pin `strategy-core @ git+…@f9a1f63` (TL/backend/pyproject.toml:18; QL/pyproject.toml:36), the editable `.pth` serves the LOCAL checkout, currently @ `108d1a7` (`git rev-parse` verified). The `PYTHONPATH=src` gotcha from memory applies only to non-installed contexts (`pythonpath=["src"]` is a pytest-only setting: QL/pyproject.toml:56, TL/backend/pyproject.toml:41).
- **QL `scripts/` modules are NOT packaged** — importing `ml_training_tab` / `run_dashboard_session_experiment` from a scratch script requires `sys.path.insert(0, r"C:\Users\gonza\Documents\Claude-Quant-Lab\scripts")` (and the warmer's self-contained precedent also inserts `src`: QL/scripts/w3_cache_warmer.py:67-73).
- **Import-time side effects to avoid**: `ml_training_tab` imports `plotly.graph_objects` and `streamlit` at module top (QL/scripts/ml_training_tab.py:23-24) and `run_dashboard_session_experiment` imports `ml_training_tab` (QL/scripts/run_dashboard_session_experiment.py:22-25) — both import-heavy but execution-inert (verified importable: streamlit 1.54.0 installed). `dashboard_utility_builder` imports `strategy_core.constants` and `TickStore` at top (QL/src/alpha_lab/agents/data_infra/ml/dashboard_utility_builder.py:28-33); the SC reader lazy-imports pyarrow inside methods (SC/src/strategy_core/data/databento_parquet.py:482,564). Importing SC/QL modules never touches TL's `backend/.env` (that is read only by `trade_lab.config.Settings`, TL/backend/src/trade_lab/config.py:14,20-25). `import databento` is never triggered by the reader path (Databento SDK is only for live/Historical adapters).

#### (e) Compute-cost reality check (schema of one mbp10.parquet)

Read with pyarrow from the smallest probe-adjacent day, `NQ/2025-12-25/mbp10.parquet` (6,322,465 bytes): **113,522 rows, 1 row group, 74 columns**. Columns: `ts_event: timestamp[ns, tz=UTC]`, `rtype: uint8`, `publisher_id: uint16`, `instrument_id: uint32`, `action: string`, `side: string`, `depth: uint8`, `price: double`, `size: uint32`, `flags: uint8`, `ts_in_delta: int32`, `sequence: uint32`, then 10 levels × `{bid_px_NN, ask_px_NN: double; bid_sz_NN, ask_sz_NN, bid_ct_NN, ask_ct_NN: uint32}` (NN=00…09), `symbol: string`, `ts_recv: timestamp[ns, tz=UTC]`. There is **no `raw_symbol` column** — the SC spread mask's `symbol` fallback is what fires on this store (SC/src/strategy_core/data/databento_parquet.py:777-786). A trade-extremes projection needs only `("ts_event","action","price","symbol","instrument_id")` — the reader's own decode already projects to `DECODE_SELECTED` ∩ schema (SC/src/strategy_core/data/databento_parquet.py:43-68,512) and prunes row groups by ts_event stats (:521-525). **Price units confirmed real dollars, 0.25-aligned, not nanos**: first trade rows read back as `price=25868.0/25868.0/25868.25` with `bid_px_00/ask_px_00` in the same 25.8k range; first 2,000 trade prices all exactly divisible by 0.25 (min 25864.75, max 26123.25). Multi-instrument contamination confirmed even on this tiny day: row counts `NQH6=78,198, NQM6=33,864, NQU6=1,460`; trade rows `NQH6=2,192, NQM6=5` — front-month filtering (or the reader's `front_month_only=True` default, :254) is required or back-month prints corrupt extremes. Full-size days run 0.3–1.9 GB (see (b)), so column projection + trades-only filtering is the difference between MBs and GBs per day scanned.

---

## 5. Computed comparison (scratch script, read-only over the store)

#### Method

`scratch_seed_parity.py` (TL root, untracked) computes, per probe day D:

- **(a) QL seed for D** via QL's OWN functions: the cache-warmer backward-walk replication of the serial carry — first non-`None` `_get_session_hl_for_date(data_dir, "NQ", prev, util_cfg, None)` walking back from D's predecessor (QL/src/alpha_lab/agents/data_infra/ml/dashboard_utility_builder.py:230-249; walk semantics proven equal to the serial carry, QL/scripts/w3_cache_warmer.py:169-197), with `MLPipelineConfig().dashboard_utility` (`bar_type="147t"` default == D-036, QL/src/alpha_lab/agents/data_infra/ml/config.py:333-336). Points→ticks via QL's own `_round_to_ticks(x, TRADE_TICK)` (QL/src/alpha_lab/agents/data_infra/ml/engine_decision.py:108-109, the exact conversion `process_single_date_stream` applies at :650-651). Computed under BOTH window conventions: **all store days** and the **D-036 training window** (2025-11-21..2026-02-13; cold-start `None` on its first day).
- **(b) TL replay seed for D** — §1 found NO seed mechanism on the dashboard replay path, so there is nothing to reproduce: marked **UNSEEDED** (TL/backend/src/trade_lab/services/replay.py:183-229 contains no seed call; fresh plugin per reset, TL/backend/src/trade_lab/services/runtime.py:245-251).
- **(c) Ground truth** — full `[18:00 ET → 18:00 ET)` extremes of the most recent prior store day with ≥1 front-month trade, from the canonical SC reader: max/min of `Trade.price_ticks` over `DatabentoParquetSource.for_trading_day(symbol_dir, P, requested_symbol="NQ")` (`front_month_only=True` default; SC/src/strategy_core/data/databento_parquet.py:248-256, 357-362).
- **Cross-check** — an INDEPENDENT pyarrow re-computation (different code path: raw parquet, trade rows of the dominant-by-trade-count non-spread instrument inside the same UTC window). It agreed with the reader on **every** ground-truth day, including exact trade counts.
- **TL live key day** — `prior_trading_day(D)` via TL's own `trade_lab.domain.trading_day` (definition only; no API calls), for attribution.

Store presence: all 7 probe days present with `mbp10.parquet` (no substitutions needed). `2025-11-20/` is an empty dir and `2026-02-14/` is ohlcv-only — both are excluded from QL's date list by construction (`get_available_dates` requires a tick file, QL/scripts/ml_training_tab.py:49-61). Total instrumented compute ≈ 186 s.

#### Results (all values in ticks; tick = 0.25 pt)

| probe day D | QL seed (all-store): day → H/L | QL seed (D-036 window) | TL dashboard replay | ground truth (canonical reader): day → H/L | Δ(QL−GT) H/L | pyarrow xcheck |
|---|---|---|---|---|---|---|
| 2026-02-18 | 2026-02-17 → 99559/97797 | N/A (outside window) | **UNSEEDED** | 2026-02-17 → 99559/97797 (n=437,324) | 0/0 | AGREES (NQH6) |
| 2026-02-17 (after Presidents' short session) | 2026-02-16 → 99689/98683 | N/A (outside window) | **UNSEEDED** | 2026-02-16 → 99689/98683 (n=78,527) | 0/0 | AGREES (NQH6) |
| 2026-01-20 (after MLK short session) | 2026-01-19 → 101888/100946 | 2026-01-19 → 101888/100946 | **UNSEEDED** | 2026-01-19 → 101888/100946 (n=118,945) | 0/0 | AGREES (NQH6) |
| 2026-01-12 (Monday) | 2026-01-09 → 103942/102462 | 2026-01-09 → 103942/102462 | **UNSEEDED** | 2026-01-09 → 103942/102462 (n=327,486) | 0/0 | AGREES (NQH6) |
| 2025-12-26 (day after Christmas) | 2025-12-24 → 103568/103110 | 2025-12-24 → 103568/103110 | **UNSEEDED** | 2025-12-24 → 103568/103110 (n=101,451) | 0/0 | AGREES (NQH6) |
| 2025-11-21 (first day after the 11-20 hole) | 2025-11-19 → 99952/97780 | **None (cold start)** | **UNSEEDED** | 2025-11-19 → 99952/97780 (n=535,784) | 0/0 | AGREES (NQZ5) |
| 2025-07-15 (old regime) | 2025-07-14 → 92266/91212 | N/A (outside window) | **UNSEEDED** | 2025-07-14 → 92266/91212 (n=263,663) | 0/0 | AGREES (NQU5) |

#### Walked-candidate evidence (the carry-through actually exercised)

- **2026-01-12**: both the QL walk and the GT walk visited `2026-01-11` first — QL bars empty → carry-through; reader finds **0 in-window trades** (the 11 MB Sunday file's rows fall at/after 18:00 ET, i.e. inside trading day 2026-01-12) — then both landed on `2026-01-09`. Key-convention agreement demonstrated on a Sunday-file day.
- **2025-12-26**: both walks visited `2025-12-25` first — reader: **0 in-window trades** in `[12-24 18:00, 12-25 18:00)` (the 6.3 MB file's 2,192 front-month trades are the 18:00-ET Christmas-evening reopen, which belongs to trading day 12-26) — then both landed on `2025-12-24`. Holiday carry-through demonstrated.
- **2025-11-21**: `2025-11-20` never enters either walk (empty dir, not in the available-dates list). All-store QL seed = GT = 2025-11-19, tick-exact. Under the actual D-036 window this day is the window's first day → QL cold start (`prev_full_hl=None`, QL/src/alpha_lab/agents/data_infra/ml/dashboard_utility_builder.py:149) → **no PDH/PDL in training either** (engine seed-load skipped, QL/src/alpha_lab/agents/data_infra/ml/engine_decision.py:647-652).
- Front-month election: the reader/xcheck elect by trade count (SC/src/strategy_core/data/databento_parquet.py:1021-1031), TickStore (QL's bars) by total row count over the two-day union (QL/src/alpha_lab/agents/data_infra/tick_store.py:655-662). The elections agreed on all 7 probes across three contract regimes (NQZ5, NQH6, NQU5) — the mechanism difference exists but did not bite on any probe day.
- **TL live key day** (`prior_trading_day`, weekday-only walk): matches the QL/GT seed day on 5/7 probes; diverges on **2025-12-26** (live key = 2025-12-25, a Thursday holiday whose trading-day window contains zero trades — a live `ohlcv-1h` fetch for that window returns empty and the live seed is SKIPPED with a warning, no backward walk, TL/backend/src/trade_lab/services/live.py:314-339) and on **2025-11-21** (live key = 2025-11-20; live reads the Historical API, not the store, so the store hole does not bind it — but whatever the API returns for 11-20 is keyed differently from both QL conventions). Store-computable facts only; no API values were fetched.

#### Divergence flags

Zero QL-vs-ground-truth divergences: **7/7 tick-exact on the same seed day** under the all-store convention; D-036-window differences are confined to the designed cold start (2025-11-21) and out-of-window probes. The only divergence class present on the axis the BACKLOG asks about is **absence**: the TL dashboard replay is UNSEEDED on every probe day.

---

## 6. Verdict table

| probe day D | TL dashboard replay vs QL carry | QL carry vs ground truth | cause class |
|---|---|---|---|
| 2026-02-18 | **UNSEEDED** | GREEN (tick-exact) | absence (replay path has no seed mechanism) |
| 2026-02-17 | **UNSEEDED** | GREEN (tick-exact) | absence |
| 2026-01-20 | **UNSEEDED** | GREEN (tick-exact) | absence |
| 2026-01-12 | **UNSEEDED** | GREEN (tick-exact) | absence |
| 2025-12-26 | **UNSEEDED** | GREEN (tick-exact) | absence |
| 2025-11-21 | **UNSEEDED** (coincidentally equal to D-036 training, which is also seedless here — cold start) | GREEN (all-store) / ABSENCE-by-design (D-036 cold start) | absence |
| 2025-07-15 | **UNSEEDED** | GREEN (tick-exact) | absence |

**Can the BACKLOG item close on this evidence?** The *verification* half can close: QL training's carry is proven correct — tick-exact against canonical-reader prior-day extremes on all 7 probes, with the carry-through semantics exercised on real holiday/Sunday/store-hole days and an independent pyarrow cross-check agreeing to the trade count — and the TL dashboard replay is proven structurally unseeded (no seed call on the path, §1(b); fresh plugin per `runtime.reset`, TL/backend/src/trade_lab/services/runtime.py:245-251; single-day window → no organic roll, §1(d)), so during dashboard replays PDH/PDL levels/zones simply never exist while training had them from day-start — a known, root-caused, deterministic divergence, not an open question. What remains is a small **build** window, not more verification: seed `HistoricalReplayService` replays the way the w3b harness already does (seed inside the source generator so it lands after `runtime.reset()` and before the first event — the `_SeedingSource` pattern, TL/backend/scripts/w3b/headless_replay.py:99-115, parity-proven bit-exact on 236 touches), with the value from the QL backward-walk over the local store (§5 shows this equals canonical prior-day extremes exactly) and any key `< D` (the SC lookup is most-recent-banked-below-D, SC/src/strategy_core/runtime/levels.py:96; QL uses calendar D-1, engine_decision.py:648-649). Two adjacent facts the window should carry: (i) the **builder stamp-ordering churn** (§3(d)): `build_utility_dataset` stamps a day-D cache with day D's OWN H/L (write at dashboard_utility_builder.py:195 happens after the carry reassignment at :190-192) while the read guard at :161 expects the seed ENTERING D — builder-written caches therefore self-invalidate on the next run (rebuild churn, content stays correct; the warmer's stamp convention is the matching one); (ii) the **live-path key/skip divergence** (§2): live's weekday-only key + skip-on-empty-fetch (no backward walk) concretely diverges from the QL convention on holiday keys (2025-12-26 → empty 12-25 window) — post-fce7846 the 2-prior-day warm-start's organic banking (§2(e)) is what papers over the skip, which is a different mechanism than training's carry and remains unverified against it (out of scope here; no API calls were made).
