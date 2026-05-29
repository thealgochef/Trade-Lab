# Implementation Readiness and Status

The original implementation-readiness items are complete through Phase 5C. This
document now records what was delivered, what remains from the original plan, and
the next roadmap after live validation.

## Completed contracts

- [x] WebSocket message contract, snapshot/backfill behavior, and throttling
  cadence.
- [x] Databento adapter specification, including schema selection and canonical
  event conversion rules.
- [x] Replay semantics for start, pause, resume, stop, bounded speeds, timestamp
  ordering, and end-of-stream handling.
- [x] Configuration and secrets policy, including gitignored local `backend/.env`,
  `TRADE_LAB_DATA_PATH`, and environment-specific settings.
- [x] Testing strategy for domain rules, adapters, replay parity, API DTOs, and
  frontend state.
- [x] Data-quality policy for warnings, invalid prices, stale feeds, missing
  definitions, schema drift, and sanitized Event Blotter details.
- [x] Frontend API/state model for bars, levels, touches, observations, feed
  health, replay controls, and live status.

## Completed phase checklist

- [x] Phase 1 — new project foundation/specs/docs.
- [x] Phase 2A — backend domain core: canonical events, integer tick-price
  utilities, America/Chicago session calendar, `147t`/`987t`/`2000t` tick candle
  engine, session level engine, touch/observation engine, and benchmark gate.
- [x] Phase 2B — FastAPI app, `/health`, `/api/v1/status`, `/ws/v1`, historical
  Parquet adapter foundation, market-data ports, bounded queues/backpressure, and
  DTOs.
- [x] Phase 2C — `ApplicationRuntime`, `HistoricalReplayService`,
  `WebSocketBroadcaster`, replay status, runtime snapshots, and backfill.
- [x] Phase 3A — React/Vite/TypeScript workstation shell, API/WS clients, split
  stores, dark terminal layout, runtime/feed/status panels, and Event Blotter.
- [x] Phase 3B — lightweight-charts chart rendering, tick-bar bars,
  levels/touches/observations overlays, chart reconciliation, and stable
  `bar_index`/`bar_id` chart coordinates.
- [x] Phase 4A — synthetic replay source `synthetic:nq-demo` and replay controls.
- [x] Phase 4B — safe historical replay catalog via `TRADE_LAB_DATA_PATH`, opaque
  source ids, bounded traversal, no path leakage, and no arbitrary path input.
- [x] Phase 5A — live Databento onboarding/config/status/UI, explicit live opt-in,
  no auto-start, and live controls security/fake-feed validation.
- [x] Phase 5B — Databento SDK adapter, subscription/callback queue, `stype_in`,
  fixed-price conversion, bounded async callback queue, BBO fanout throttling, and
  post-stop callback hardening.
- [x] Phase 5C — live Databento validation runbook and safe preflight helper.

## Post-phase fixes completed

- [x] Frontend fetch binding bug fixed.
- [x] Backend `.env` loading for local development enabled while `.env` remains
  gitignored.
- [x] Tick-candle charting fixed with stable backend `bar_index`/`bar_id` instead
  of timestamp collisions.
- [x] Databento live heartbeat/control messages no longer spam normalization
  warnings.
- [x] Event Blotter rows expandable with safe warning details.
- [x] Databento control/error handling improved, with numeric schema fallback,
  MBP/BBO top-of-book levels extraction, and safer redaction.
- [x] Local Databento-export Parquet MBP-10 replay support added for supported
  filenames/schema under `TRADE_LAB_DATA_PATH`, including variants `mbp10`,
  `mbp-10`, `mbp_10`, and `cmbp-10`.
- [x] Historical replay catalog hardening added: opaque source ids only, no full
  paths or filenames, no arbitrary path input, blocked symlink/reparse traversal,
  path-like/Windows drive-like id rejection, and sanitized invalid-row warnings.
- [x] MBP-10 replay projection constrained to live-compatible fields: trade action
  rows (`T`/`Trade` variants) produce `TradeEvent`; optional level 0 bid/ask
  produces `TopOfBookEvent`; deeper book levels are ignored as runtime features.

## Latest verification

- Backend full pytest: `268 passed, 1 skipped`.
- Backend ruff: all checks passed.
- Benchmark gate: passed.
- Frontend lint/typecheck/test/build: passed.
- Frontend tests: `97 passed`.

## Remaining validation checklist

- [ ] Run manual live Databento validation during active market hours.
- [ ] Configure `TRADE_LAB_DATA_PATH` and manually verify that a supported MBP-10
  historical source appears and replays through the UI/API.
- [ ] Use only explicit operator start; no auto-start path should be introduced.
- [ ] Confirm live status, chart updates, timeframes, warnings, and stop behavior.
- [ ] Record sanitized evidence only; exclude secrets, API keys, raw market data,
  model binaries, and local sensitive paths.

## Future roadmap

- Phase 6 — stabilization/live market validation.
- Phase 7 — persistence/production hardening.
- Phase 8 — research/model integration.
- Phase 9 — trading/risk/execution layer, only when explicitly requested.
