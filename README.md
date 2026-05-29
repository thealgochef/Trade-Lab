# Trade-Lab

Trade-Lab is a clean v2 foundation for an institutional-grade NQ futures trading
and replay dashboard.

The original Phase 1-5 core plan is complete. Trade-Lab now includes the backend
domain/runtime foundation, safe replay controls, the React workstation UI, and
operator-controlled live Databento market-data wiring. The only remaining item
from the original plan is manual live validation during active market hours.

## Project shape

- `backend/` — backend foundation with typed config, domain models,
  serialization contracts, historical Parquet replay adapter, runtime/replay
  state management, safe synthetic replay controls, WebSocket broadcaster wiring, bounded backpressure behavior,
  and smoke/benchmark tests. See `backend/README.md` and backend docs for setup
  and commands.
- `frontend/` — workstation UI, charting client, safe replay controls, and an
  opt-in live Databento onboarding panel.
- `docs/` — architecture notes and operating guides.
- `scripts/` — development and operational helpers.

## Current status

- Core implementation: complete through Phase 5C.
- Latest backend verification: full pytest `268 passed, 1 skipped`; ruff passed;
  benchmark gate passed.
- Latest frontend verification: lint, typecheck, test, and build passed; frontend
  tests `97 passed`.
- Remaining original-plan work: manual Databento live validation with an operator
  present during active market hours, so real live bars and trades can be verified.

## Completed phases

- [x] Phase 1 — project foundation, specs, architecture docs, and safety
  constraints.
- [x] Phase 2A — backend domain core: canonical events, integer tick-price
  utilities, America/Chicago session calendar, `147t`/`987t`/`2000t` tick candle
  engine, session levels, touch/observation engine, and benchmark gate.
- [x] Phase 2B — FastAPI app, `/health`, `/api/v1/status`, `/ws/v1`, historical
  Parquet adapter foundation, market-data ports, bounded queues/backpressure, and
  DTOs.
- [x] Phase 2C — `ApplicationRuntime`, `HistoricalReplayService`,
  `WebSocketBroadcaster`, replay status, runtime snapshots, and backfill.
- [x] Phase 3A — React/Vite/TypeScript workstation shell, API/WS clients, split
  stores, dark terminal layout, runtime/feed/status panels, and Event Blotter.
- [x] Phase 3B — lightweight-charts rendering, tick-bar bars,
  levels/touches/observations overlays, chart reconciliation, and stable
  `bar_index`/`bar_id` chart coordinates.
- [x] Phase 4A — synthetic replay source `synthetic:nq-demo` and replay controls.
- [x] Phase 4B — safe historical replay catalog via `TRADE_LAB_DATA_PATH`, opaque
  source ids, bounded traversal, no path leakage, and no arbitrary path input.
- [x] Phase 5A — live Databento onboarding/config/status/UI with explicit live
  opt-in, no auto-start, and live controls security/fake-feed validation.
- [x] Phase 5B — Databento SDK adapter, subscription/callback queue, `stype_in`,
  fixed-price conversion, bounded async callback queue, BBO fanout throttling, and
  post-stop callback hardening.
- [x] Phase 5C — live Databento validation runbook and safe preflight helper.

## Post-phase fixes completed

- Fixed frontend fetch binding.
- Enabled backend `.env` loading for local development while keeping `.env`
  gitignored.
- Fixed tick-candle charting with stable backend `bar_index`/`bar_id` instead of
  timestamp-collision coordinates.
- Stopped Databento live heartbeat/control messages from spamming normalization
  warnings.
- Added expandable Event Blotter rows with safe warning details.
- Improved Databento control/error handling, numeric schema fallback, MBP/BBO
  top-of-book extraction, and redaction.

## Remaining validation checklist

- [ ] Configure `TRADE_LAB_DATA_PATH` and manually verify that a supported local
  MBP-10 historical source appears in the replay catalog and replays.
- [ ] Run the Phase 5C live Databento validation during active market hours.
- [ ] Confirm sanitized live status transitions: `idle` -> `connecting` ->
  `running` or sanitized `failed`.
- [ ] Confirm live bars update from trade events and BBO/context does not create
  raw tick spam.
- [ ] Verify `147t`, `987t`, and `2000t` chart rendering stays stable.
- [ ] Stop live from the UI/API and confirm no post-stop updates continue.
- [ ] Record only sanitized findings; do not capture secrets, raw market data, or
  local sensitive paths.

## Future roadmap beyond the original plan

- Phase 6 — stabilization and live market validation.
- Phase 7 — persistence and production hardening.
- Phase 8 — research/model integration through versioned contracts.
- Phase 9 — trading, risk, and execution layer only when explicitly requested.

Backend verification uses Python 3.13, for example:

```powershell
cd backend
python -m pytest
python -m ruff check src tests
```

When the backend is run from `backend/`, settings load `backend/.env` for local
development and still use the `TRADE_LAB_` prefix. Real `.env` files are
gitignored and must never be committed, shared, pasted into chat, or captured in
logs/screenshots. Shell/process environment variables still override `.env`
values.

Frontend verification from `frontend/`:

```powershell
npm install
npm run dev       # port 5174, connects to backend port 8001 by default
npm run lint
npm run typecheck
npm run build
npm run test
```

## Safe replay controls

Phase 4B keeps the allowlisted synthetic replay source (`synthetic:nq-demo`) and
can also discover replayable local historical Parquet files under
`TRADE_LAB_DATA_PATH`. Historical source ids are opaque allowlist entries such as
`historical:nq:2026-02-22:trades`; API/WS payloads never expose full local paths
or filenames, and replay start never accepts arbitrary path input. Discovery is
bounded to the configured data root, blocks root/child symlink or reparse-point
traversal, and rejects path-like or Windows drive-like source ids.

Local Databento-export Parquet MBP-10 files can be replayed when they are found
under `TRADE_LAB_DATA_PATH` (for example `data\databento\NQ`) and their filename
variant/schema is supported. Recognized MBP-10 filename variants include `mbp10`,
`mbp-10`, `mbp_10`, and `cmbp-10`. Unsupported deeper schemas such as MBO and
MBP-2 through MBP-9/depth-only files remain hidden from the catalog. MBP-10 replay
projects live-compatible fields only: `TradeEvent` is emitted only from trade
action rows (`T`/`Trade` variants) so candles and touches use last traded price,
and optional `TopOfBookEvent` context comes from level 0 bid/ask. Deeper book
levels are ignored as runtime features. The `max_events` cap counts replay
items/updates, including warnings, not just successful market events. Invalid-row
warnings are sanitized and do not expose raw values, paths, filenames,
secret-like source labels, sampled historical-only columns, or schema/column
names.

## Live Databento market-data wiring

Phase 5B kept the Phase 5A live controls and added real Databento SDK subscription
wiring behind explicit operator start. Live is market-data only and never
auto-starts; operators must explicitly call `POST /api/v1/live/start` or press
Start Live in the UI. The API key is backend environment only via
  `TRADE_LAB_DATABENTO_API_KEY`; for local development this may be placed in the
gitignored `backend/.env`. There is no browser key entry and safe status returns
only booleans such as `api_key_configured`, `sdk_available`, and
`subscription_ready`. Start Live also requires the explicit paid-data opt-in
`TRADE_LAB_DATABENTO_LIVE_ENABLED=true`; deliberately set both values in the
process environment or local `backend/.env` before starting/restarting the
backend. The example env file keeps live validation disabled by default.

Databento SDK callbacks are bridged through a bounded adapter queue, normalized to
canonical events, and then processed by the same runtime path used by fake feeds
and replay. Tests use fake SDK clients only and do not make Databento network
calls. There is no trading, execution, risk/accounts, or ML in this phase.

## Manual live Databento validation

Before the first real live Databento run, follow
`docs/live-databento-validation-runbook.md`. Live validation is manual/operator
controlled, market-data only, and has no auto-start path. The optional
`scripts/live_databento_preflight.py` helper checks local readiness endpoints but
never starts live and never prints secret values.
