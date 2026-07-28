# WARM_PERF_RECON — where the live warm-start's time goes, and what a schema-scoped fetch changes

Recon date: 2026-07-07 (evening, US/Central). TL @ c92f13b (platform-refactor), SC @ 1650327. Read-only; no repo files modified.
Deployment under measurement: the RUNNING backend (PID 2668, `python -m trade_lab.api`, port 8001), symbol **NQU6 / raw_symbol** (backend/.env override of the NQ.c.0/continuous defaults, `config.py:44-45`), dataset GLBX.MDP3, active bundle **NQ_W3_20260617T220752Z** (approach 15 min / interaction 5 min / 5 features / eligible ny / gate 0.7).

Everything below was verified against current code (file:line) and, where a number is claimed, **measured** — from the production process's warm start that ran tonight (started 01:10:34Z), from free Databento metadata counts, and from an instrumented offline run of the exact production pipeline over a real 30-min slice (scripts + raw results in the session scratchpad: `warm_stage_bench*.py/json`, `warm_waypoints.json`, `warm_poll.jsonl`).

> **⚠ Observed anomaly (not a recon section, but you should know):** tonight's warm start drained all 24,979,246 historical events by ~01:30:32Z and then went **silent**: `state=running`, `warm_start_state=warming` (never flipped), `events_processed` frozen at 24,979,246, `last_event_ts_utc` pinned at `2026-07-08T00:59:59.848Z` (the availability clamp), `feed_state=connected`, process ~idle (0.4% of one core), one ESTABLISHED TCP to the live gateway — and **zero live events for 45+ minutes** while Globex was open, with no DEGRADED status and no error surfaced. The post-drain live subscribe (`databento.py:362 _connect(replay_start=None)`) appears to be delivering nothing, silently. This also means the warm→live flip (`live.py:361-364`) never happened, so the status still reads "warming". Worth its own investigation; it is exactly the state a mid-evening operator would misread as "still warming".

---

## 1. Fetch shape today

**The 2-prior-trading-day constant:** `_WARM_START_PRIOR_TRADING_DAYS = 2` at `backend/src/trade_lab/adapters/databento.py:61`, applied in `DatabentoMarketDataFeed.start()` at `databento.py:238-241`: `day = trading_day_for(now)`, then `prior_trading_day(day)` twice, then `replay_start = trading_day_start_utc(day)` — i.e. the 18:00-ET open of the trading day two trading days back (`domain/trading_day.py:13-14` ET + 18:00 boundary; `:42-48` prior_trading_day skips Sat/Sun only, holidays not modeled; `:27-33` start = (D−1) 18:00 America/New_York → UTC).

**What is requested, per schema:**

| Schema | Span | Where | Measured size tonight |
|---|---|---|---|
| `trades` | `[replay_start, now)` — full 2 prior days + current partial | `databento.py:300-305` → `databento_historical.py:83-96` (one `get_range`) | 775,668 records, 37.2 MB billable |
| `mbp-1` | **same full span** | same call, second schema | **24,203,578 records, 1.936 GB billable** |
| `ohlcv-1h` | prior trading day only, `[start, end)` bounds | `live.py:311-339 _load_prior_day_summary` → `databento_historical.py:98-113` (`_OHLCV_SCHEMA='ohlcv-1h'` at `:33`); reduced to (max high, min low) → `load_prior_day_summary` | 23 rows; fetch measured 9.2 s |

- Both DBN fetches happen in **one `asyncio.to_thread` call, sequentially, trades first** (`databento.py:300-305`; `databento_historical.py:83-96` — `tuple(...)` genexp evaluates both `get_range` calls eagerly). Only the fetch runs in the thread; record decode happens later on the event loop during the drain (`databento.py:437-470`).
- `end = now` is clamped to the dataset's availability end (`databento_historical.py:79, 128-146`, per-schema range looked up with `schemas[0]` = trades). Tonight the clamp landed at `2026-07-08T01:00:00Z` — the drain's last record was `00:59:59.848Z`, i.e. a ~10-min seam at start (logged by `databento.py:316-328`). If the clamp makes `end <= start`, empty streams are returned and the adapter still proceeds to the live subscribe (`databento_historical.py:81-82`, drain logs "replayed 0", `databento.py:470`).
- **Current-day partial vs prior-day spans: there is no per-day splitting.** One `[replay_start, clamped_now)` request per schema covers 2 full prior trading days + the current partial day. Tonight's per-day decomposition (measured via free `metadata.get_record_count`): td 2026-07-06 = 335,198 trades / 10.50M quotes; td 2026-07-07 = 418,692 / 12.95M; td 2026-07-08 partial (Tue 18:00 ET → clamp, ~3 h) = 21,778 / 752k. Sum = 24,979,246 = **exactly** the `warm_start_events` the live status reported. Quotes are **96.9%** of warm events (~31:1 quote:trade).
- The gateway intraday-replay subscribe (`subscribe(start=...)`, `databento.py:135-146, 271`) is dead code in production: both `_connect` call sites pass `replay_start=None` (`databento.py:251, 362`); the Historical fallback is the only warm-history source (the >24 h window exceeds the gateway replay limit, comment `databento.py:231-237`). Warm-fetch failure and ohlcv-seed failure are both **warn-only** (`databento.py:289-314`; `live.py:315-339`) — the feed then subscribes live with no history.
- After the drain, the live subscribe covers all five schemas (trades, mbp-1, definition, status, statistics) from now (`databento.py:362 → :254-286`; schema set from `config.py:46-50`).

## 2. Inference + journal during warm

**Yes — the model predicts on warm-replay touches, and the journal appends them. There is no gate.**

- `live.py:341-354 _process_live_item`: `_mark_warm_start(item)` (line 348) only increments the counter/label; every market event then goes to `runtime.process_market_event` (line 354) with no warm-state branch. Grep over `backend/src` for `warm_start_state|_warm_start_state`: only `live.py:101,153,207,240,363,364` (status plumbing) and `app.py:173` (payload). Nothing gates inference.
- `runtime.py:843-870 _process_trade` unconditionally runs `observations.refresh` (:848) → `_run_inference` (:851) → `_track_outcomes` (:853) → `_journal_records` (:857-859). Observations open/expire on replayed **event time** (`observations.py:50-64, 72-85`; expiry status is EXPIRED, selected by `runtime.py:556`), so the whole predict→resolve lifecycle replays at drain speed.
- Journal rows are tagged `mode="live"` during warm — `runtime.py:674` reads `self._feed_status.mode`, which `live.py:417-431` hardcodes to `"live"` at start and `runtime.py:872-893` preserves per trade. **A warm-replay row is byte-indistinguishable from a real-time row** (journal.py has no other discriminator; `ts_utc` is event time, `journal.py:32,56,80`).
- **Mid-day restart ⇒ duplicates, yes:** the journal is append-only, keyed by the record's *event* trading day (`journal.py:93-100`, `trading_day_for(ts_utc)`), with zero dedup. `runtime.reset()` (`live.py:228-235` → `runtime.py:245-273`) rebuilds the SC service and observation engine, so the warm replay re-detects the same touches with all-new uuid4 ids (`inference_engine.py:210` prediction_id, `strategy_core_service.py:264` touch_id, `observations.py:52` observation_id) and re-appends them to the *same per-day files*. Note the blast radius: the replay covers **2 full prior trading days**, so a restart re-journals those days' touches too, not just the current morning's.
  - **Empirical:** `backend/data/journal/2026-06-16.jsonl` holds the same touch up to **10×** with distinct prediction ids, all `mode="live"` (e.g. `asia_high@123440 ts=08:10:55.294` ×10; `london_low@123232` ×9; `asia_low@123022` ×9) — one copy per live (re)start in that debugging window. Tonight: `2026-07-06.jsonl` (created 01:14:56Z) and `2026-07-07.jsonl` (created 01:21:15Z) were both written **during the warm drain** minutes after the 01:09:15Z process start — 3 predictions + 3 outcomes each, journaled from replayed touches as `mode="live"`.
  - One bound on the dup mechanism: activation does **not** persist across restarts — `ModelRegistry` starts with `_active=None` (`model_registry.py:390`; `app.py:249-273`, no startup auto-activation; only POST `/api/v1/models/activate`, `app.py:417-455`). Duplicates occur when the operator activates a model before (or during) the live warm run — which is the normal runbook, and what happened tonight.
- **What the user surfaces show during warm predictions today:** a typed `model.reset {"reason":"live_reset"}` frame at live start (`runtime.py:273`, `broadcaster.py:95-98`); `/api/v1/live/status` shows `warm_start_state:"warming"` + `warm_start_events` (`app.py:150-175`). With `throttle_warm_start=True` (`app.py:335`), per-event deltas — including `prediction.created` — are **suppressed** while the frontier lags (`live.py:437-466`; suppressed frames are dropped, never replayed); warm predictions surface only inside the ~1/s throttled full `system.snapshot` (`live.py:474-481`, `broadcaster.py:78-89`), whose payload carries the predictions/outcomes/dropped rings (`dto.py:198-200, 416-426`). `model.status` emits only on activation change (`broadcaster.py:136-148`). So the UI shows warm predictions as ordinary rows/markers materializing via snapshots, distinguishable only by the "warming" label elsewhere on the status surface.
- Warm outcomes resolve during the same drain (`runtime.py:853` on replayed 147t closes) and are journaled; `live.stop()` during warm flushes open setups at wall clock (`live.py:308` → `runtime.py:335-366`) and journals the drops.

## 3. Quote consumption engine-side

Complete inventory of what reads quotes after normalization (verified exhaustively; refutation attempted):

1. **TL `ApplicationRuntime._process_quote`** (`runtime.py:804-841`): (a) `market_context.append_quote` (:811-813; one-sided quotes dropped, `market_context.py:92-93`); (b) feed-status liveness (latest-wins, :814-841); (c) forwards to SC (:815).
2. **SC `StrategyRuntime._process_quote`** (`state.py:313-317`): sets `_last_quote` and `_last_event_ts_utc` (both latest-wins scalars) + transient FeedStatus. `_last_event_ts_utc` feeds session/trading-day classification (`state.py:373-377`) — also latest-wins. Quotes **never** reach the candle engine (`state.py:343` trades only), the plugin/level fold (`state.py:351` trades only; the plugin's quote branch is inert and never invoked), or the trade ring (`state.py:334-342`).
3. **Feature layer:** exactly one feature reads quotes — `app_max_spread` over the approach window `[touch−15m, touch)` (`feature_functions.py:240-248` via `_sc_quotes` :166-178 → `buffer.quotes_in_window`). The other four active features read trades only. `latest_mid_price_ticks` (`market_context.py:120-126`) has **zero callers** in `backend/src`. SC's `RuntimePlatformContext.quotes_in_window` is a stub returning `()` (`SC runtime/context.py:103-109`). `last_quote` has zero TL readers (TL never calls SC `to_dict()`; `_map_update`/`_map_snapshot` drop it, `strategy_core_service.py:176-249`). `Quote.bid_size/ask_size` are carried (`strategy_core_service.py:298-307`) but read nowhere on the serving path.

**Buffer retention under the active contract:** effective retention = approach 15 + interaction 5 + `MARKET_CONTEXT_RETENTION_SLACK_MINUTES` 10 = **30 minutes** (`runtime.py:47, 368-390`), applied at activation via `set_retention` (`runtime.py:333`, immediate re-evict `market_context.py:128-138`) — a shrink from the configured 45-min baseline (`config.py:64`, ge=45). Eviction cutoff = newest-appended-ts − retention (`market_context.py:155-176`), plus a 6M-element safety cap.

**The safety claim, confirmed:** a quote older than (retention + slack) before now has zero effect on any state that survives to the live flip — (i) the buffer evicts it as the drain appends newer events; (ii) `_last_quote` / `_last_event_ts_utc` / feed status are latest-wins scalars overwritten by any newer event; (iii) no other sink exists. Strict exceptions, all ops diagnostics unreadable by any feature/level/bar/prediction path: `warm_start_events` counts every warm event (`live.py:356-364`), `events_processed` likewise (SC `runtime/live.py:142-146`), and a *malformed* quote can leave a DataQualityWarning in the 100-cap ring (`runtime.py:685-702`). A scoped fetch that still supplies the last (retention + slack) of quotes reproduces everything a feature can read.

**Per-state provenance (what survives to the flip):**

| State | Built from | Where |
|---|---|---|
| Tick bars (147/987/2000) | trades only | SC `state.py:343`, `candles/streaming.py` |
| Levels, PDH/PDL banking, session extremes | trades (plugin fold) + ohlcv-1h prior-day seed | SC `state.py:351, 358`; seed `live.py:330` → `state.py:274-276` |
| Touches (first-touch dedup) | 147t decision-bar closes | SC `state.py:353-359` |
| Session / trading-day label | latest event ts (any type, latest-wins) | SC `state.py:373-377` |
| Trade ring (`trade_price_at`, resolver entry) | trades only, 60-min retention (2× the 30-min lookback) | SC `state.py:231-236, 334-342` |
| Market-context buffer | trades + two-sided quotes, 30-min effective retention | `runtime.py:811-813, 844-846`; `market_context.py` |
| Observations | touches + trade-event clock | `runtime.py:848, 854-855` |
| Honest resolver | 147t closes + trade ring | `runtime.py:592-641, 416-425` |
| Feed status / TOB `_last_quote` | latest event / latest quote (scalars) | `runtime.py:814-841`; SC `state.py:314-317` |
| Instrument/statistics metadata | definition/statistics events | `runtime.py:716-754` |

## 4. Time split (measured)

**Production, tonight's warm start** (journal-waypoint method: journal file create/write wall times vs record event-time positions, event counts between positions from free metadata):

| Segment (event-time span) | Events | Wall | Rate |
|---|---|---|---|
| live start 01:10:34Z → first td-07-06 touch pred (12:09:26 ev) | 3,425,128 | 262 s | 13.1k ev/s (includes the full fetch) |
| → 14:18:37 ev | 2,378,168 | 98 s | 24.3k ev/s (4.12 s/100k) |
| → 03:35:03 ev (07-07) | 6,888,594 | 281 s | 24.5k ev/s (4.08 s/100k) |
| → 13:39:03 ev | 2,983,247 | 130 s | 22.9k ev/s (4.36 s/100k) |
| tail → 00:59:59.848 ev | 10,248,582 | (extrapolated ~427 s) | — |

Steady-state production drain = **~4.1–4.4 s per 100k events (~23–24.5k events/s)**. Backing out the drain from segment 1 leaves **fetch ≈ 120 s** (both `get_range` downloads, serial, ~2 GB DBN billable + the 9 s ohlcv seed). Total warm ≈ **20 min** (drain end extrapolates to ~01:30:32Z): **fetch ≈ 10%, drain ≈ 90%**.

**Offline decomposition of the drain** (instrumented run of the production code path over a real 30-min RTH slice, 1,065,014 events = 31,898 trades + 1,033,116 mbp-1; `warm_stage_bench.py`):

| Stage | s / 100k events | share of production 4.2 s/100k |
|---|---|---|
| DBN decode (DBNStore iteration) | 0.02–0.025 | **~0.6%** — decode is NOT the wall |
| `normalize_provider_message` | 1.36 (trades 1.23 / quotes 1.36) | **~33%** |
| `runtime.process_market_event` (engine, TL+SC) | 1.77 | **~42%** |
| heapq merge iteration | 0.03 | ~0.7% |
| SC LiveRuntime per-item `await asyncio.sleep(0)` (SC `runtime/live.py:154`) | 0.23 | ~5% |
| snapshot builds (~1/s, `live.py:474-481`) + WS fanout + GC + misc | remainder | ~19% |

Queue transit is **absent during the fallback drain** (records yield straight from `_drain_warm_start_streams`, `databento.py:352-359`; the provider queue is only the live phase). Microbenched for the live phase: `asyncio.Queue` put/get 0.044 s/100k, `call_soon_threadsafe` hop ~0.3 s/100k.

Snapshot serialization (measured on the real broadcaster path, `warm_stage_bench2.py`): 31.8 ms + 1.47 MB per `system.snapshot` at 4,056 bars → ~55–60 ms and ~2.6 MB at the warm-complete ~7k bars; at the 1/s throttle over an ~18-min drain that's ~60–70 s of CPU and ~2–3 GB of WS bytes **per connected client**.

**What a schema-scoped fetch changes (measured, tonight's shape):** trades full-span (775,668) + mbp-1 scoped to the last 40 min (30-min retention + 10 slack; measured 185,246 records) = **~0.96M events instead of 24.98M (26×)** and **~52 MB instead of 1.97 GB billable (38×)**. At the measured 4.2 s/100k drain rate: drain ≈ 40 s; plus fetch (37 MB + ~15 MB; note the slice fetch showed ~20 s fixed server latency per get_range) and the 9 s ohlcv seed → **~1.5–2 min total vs ~20 min today (~10–13×)**. The scoping matches what the buffer keeps anyway: full-span warm quotes are progressively evicted during the drain (`market_context.py:160-166`); only the final retention window survives.

## 5. Bar math

Measured 147t bars/day (trades ÷ 147):

- **July regime (metadata, exact):** td 2026-07-06 → **2,280** (987t 339, 2000t 167); td 2026-07-07 → **2,848** (987t 424, 2000t 209). Current-partial (3 h asia) → 148.
- **Feb regime (local store, front-month outright only):** 2026-02-17→20: 2,979 / 2,399 / 2,352 / 2,874 at 147t (sums across all three timeframes 2,874–3,640/day). Store's latest session is 2026-02-22, so these are older-regime numbers.

**8000-bar retention:** `recent_closed_bar_limit=8_000` is set only at `app.py:244` and is **one list shared across all three timeframes** in SC (`state.py:223-225, 344-347` — not per-timeframe; SC/TL defaults elsewhere are 500). Two full July warm days = **6,267 bars total** (Feb pairs: 5,806–6,572) → headroom ≈ **1,700 bars ≈ ~205k trades of the current partial day** (bars accrue at 1/147+1/987+1/2000 ≈ 0.0083/trade). So the fixed 2-day warm depth fits, but a **restart from ~midday onward on average-or-heavier days pushes past 8000 and silently evicts the oldest warm bars** (worst measured Feb case 2-full+1-full ≈ 10,212 → ~2,212 evicted). The `app.py:241-243` "never truncated … ~5-7k" comment holds only for early-session restarts. The frontend cap is separate and per-timeframe (`viewModels.ts:8` MAX_BARS_PER_TIMEFRAME=8,000 — 2 days of 147t ≈ 5.1–5.7k, never truncates). The honest resolver's decision bars: ~5,128 147t closes across the two full July days, plus the partial day.

## 6. Size estimate (file-touch lists, no design)

**(a) Schema-scoped fetch** — 5 files (4 prod + 1 test + 0 docs):

| File | Where the work lands |
|---|---|
| `adapters/databento.py` | `start()` :226-252 (per-schema anchor), `_fetch_warm_start_streams` :289-314 (the single start=… call), comments :59-61/:230-237. `_drain_warm_start_streams` needs **no code change** — heapq.merge only needs each stream individually sorted; asymmetric spans just yield trades alone until the quote head appears (:447-450). |
| `adapters/databento_historical.py` | `dbn_record_streams` :68-96 takes ONE `start` for all schemas → per-schema starts; the `end<=start` guard :82 becomes per-schema; `_clamp_end_to_available` uses `schemas[0]` only :79; module docstring :5-9. Injected `RecordFetcher` type :29 already passes (schema, start, end) — test fixtures keep their shape. |
| `services/live.py` | docstring :8-15 + (if the retention plumb rides `LiveConfig`) `LiveConfig` :67-81. Note `warm_start_events` values shrink (readers: `live.py:101-102/207-208`, `app.py:173-174`, `test_live_warm_start.py:350-351`, `test_api_contract.py:195-196`; frontend reads none). |
| `api/app.py` | `live_feed_factory` :310-325 — the retention value is **not reachable from the adapter today** (feed ctor `databento.py:168-224` has no retention/runtime param; `FeedFactory` passes only LiveConfig). Reachable candidates: contract-driven effective value via `runtime.market_context.retention` (live service holds the runtime, `live.py:124`; updated at `runtime.py:205/333`) or the settings baseline in the factory closure. Staleness fact: retention can change post-construction on hot-swap (`runtime.py:333`). |
| `backend/tests/test_live_warm_start.py` | the ONLY test pinning the both-schemas-full-span shape: `test_intraday_replay_warm_starts_from_historical_api_then_subscribes_live` :156-215 (asserts `fetch_calls == [('trades', ANCHOR), ('mbp-1', ANCHOR)]` :172-175) + fixture :122-153 + anchor comment :18-21. No standalone doc states the fetch shape (grep: AGENTS.md/README/docs have no warm-start prose — REFUTED expectation). |

**(b) Warm inference gate** (predict only when state=live; bars/levels/touches/observations/market-context still build — all of `_process_trade` except `_run_inference` keeps running) — 2 prod files + tests:

| File | Where the work lands |
|---|---|
| `services/live.py` | the warm flag is per-LiveMarketDataService (`_warm_start_state` :152-154, flipped in `_mark_warm_start` :356-364, which already runs BEFORE `process_market_event` :348→:354 — correctly ordered for same-event gating). The runtime is shared with replay (`app.py:236-282`), so the flag must be threaded from here. |
| `services/runtime.py` | gate insertion point = `_run_inference` (:535-590) / its call at :851 — the single place predictions are produced AND resolver-registered (:584-589), so gating there suppresses predict+register+journal atomically (journaling is downstream of production, :857-859). The what-happens-to-observations-expiring-during-warm decision lands in the EXPIRED filter loop :555-578. |
| Tests | **No existing test asserts predictions during live warm** (warm-start tests build runtimes without an inference engine, `test_live_warm_start.py:24-29`; throttle tests use a bare runtime, `test_live_databento.py:870, 944`) — new gate assertions land in those two files. What IS pinned is unconditional predict-on-completion: `test_inference_engine.py` (:381, :406, :420, :439), `test_inference_api.py` (:422, :464, :516), `test_honest_resolver_serving.py` (:146-288), `test_lifecycle_w2.py` (:306+) — touched only if the gate changes ApplicationRuntime's constructor/defaults rather than staying default-on. |
| Untouched / review-only | `services/journal.py` (pure sink), `api/app.py` status surface (already exposes warm_start_state), `test_api_contract.py`; frontend render surfaces as a would-need-review list: `IntelligencePanel.tsx`, `ChartWorkspace.tsx`, `chart/viewModels.ts`, `chart/overlayManager.ts` (no frontend file reads warm_start_state today). SC needs no touch for either list. |

Core totals: list (a) = 5 files; list (b) = 2 prod + 2 warm-test files (+4 inference-test files only on a constructor-shaped gate; +3 backend review-only; +4 frontend review-only). Overlap: `services/live.py`, `test_live_warm_start.py` appear in both.

---
*Measurement artifacts: scratchpad `warm_stage_bench_results.json`, `warm_stage_bench2_results.json`, `warm_waypoints.json`, `warm_poll.jsonl` (537 polls, all frozen at 24,979,246), `scan_journal_dupes.py` output. Verification run: workflow wf_94d58022-356 (5 agents, 33 verdicts: 28 CONFIRM / 4 PARTIAL-with-nuance / 1 REFUTE — the refuted item being the expectation that standalone docs describe the fetch shape).*
