# Trade-Lab Backend

The original Phase 1-5 core plan is implemented. Remaining original-plan work is
manual live Databento validation during active market hours; live mode remains
explicitly operator controlled and market-data only.

Phase 2A implements pure backend domain foundations: typed configuration, canonical
market events, integer-tick price utilities, session calendar rules, tick candles,
session levels/touches, and an observation lifecycle placeholder.

Phase 2B adds the API/WebSocket contract and market-data adapter boundaries. Phase
2C wires the Phase 2A engines into an application runtime plus a lightweight
historical replay controller. Phase 4B adds safe, allowlisted synthetic and local
historical replay controls. Phase 5B adds operator-controlled live Databento SDK
subscription wiring, API-key/SDK status, and fake-SDK pipeline validation. Replay and
live feeds share the same `process_market_event()` path so historical behavior
stays live-compatible.

Run the dev API on backend port `8001` with:

```powershell
cd <repo>\backend
python -m trade_lab.api
```

Contract endpoints:

- `GET /health`
- `GET /api/v1/status` — includes runtime/feed state and a safe replay summary.
- `GET /api/v1/replay/status`
- `GET /api/v1/replay/sources` — returns allowlisted ids/labels only, plus a safe
  historical availability status.
- `POST /api/v1/replay/start` — starts an allowed source id such as
  `synthetic:nq-demo`; optional `speed` and `max_events` are bounded. The
  `max_events` cap applies to replay items/updates, including data-quality
  warnings, not only successfully processed market events.
- `POST /api/v1/replay/pause`
- `POST /api/v1/replay/resume`
- `POST /api/v1/replay/stop`
- `GET /api/v1/live/status` — safe live status; reports key/SDK/subscription
  readiness booleans, never key values.
- `POST /api/v1/live/start` — explicit operator action for market-data-only live
  Databento subscription startup.
- `POST /api/v1/live/stop`
- `WS /ws/v1` — sends versioned `system.snapshot` and `system.heartbeat` envelopes,
  then domain deltas such as bar updates, level updates, touches, observations,
  feed status, and data-quality warnings. Raw tick spam is intentionally avoided.

Replay service notes:

- The service consumes a `HistoricalMarketDataSource`; tests use in-memory fakes
  and the deterministic `synthetic:nq-demo` source.
- Configure `TRADE_LAB_DATA_PATH` to a local data root to discover historical
  Parquet sources. If it is missing or unavailable, only `synthetic:nq-demo` is
  listed and the API reports historical replay as unavailable without failing.
- Historical ids are opaque allowlist keys, not paths. The catalog and status
  payloads do not expose full paths or filenames. `POST /replay/start` resolves
  ids back to catalog paths internally and rejects unknown, path-like, and Windows
  drive-like ids to prevent path traversal and filesystem probing.
- Historical discovery is rooted at `TRADE_LAB_DATA_PATH` and blocks root/child
  symlink or reparse-point traversal.
- Historical replay projects selected live-compatible columns (`trades`, `bbo`,
  `mbp-1`, or supported Databento-export MBP-10) through
  `HistoricalParquetAdapter`. Local MBP-10 files may be discovered under roots
  such as `data\databento\NQ` when filenames/schema are supported. Recognized
  filename variants include `mbp10`, `mbp-10`, `mbp_10`, and `cmbp-10`.
- Unsupported deeper schemas such as MBO and MBP-2 through MBP-9/depth-only remain
  hidden. For MBP-10, only trade action rows (`T`/`Trade` variants) produce
  `TradeEvent` records; candles and touches therefore use last traded price only.
  Level 0 bid/ask may produce optional `TopOfBookEvent` context. Deeper levels are
  ignored as runtime/model features.
- Invalid-row warnings are sanitized and do not expose raw values, full paths,
  filenames, secret-like source labels, sampled historical-only columns, or
  schema/column names.
- Phase 5B live Databento is market-data only. No ML inference, risk, execution,
  Rithmic, accounts, trading controls, or model UI is implemented.

Live Databento notes:

- No auto-connect occurs at import/app startup; this prevents surprise paid data
  sessions and keeps tests offline.
- When run from this `backend/` directory, local development settings load from
  gitignored `backend/.env` and use the `TRADE_LAB_` prefix. Shell/process
  environment variables still override `.env` values.
- Supply credentials only through backend configuration
  (`TRADE_LAB_DATABENTO_API_KEY` in the process environment or gitignored
  `backend/.env`); browser UI never accepts keys because bundled frontend code
  and browser storage are not a safe secret boundary.
- Start Live requires both `TRADE_LAB_DATABENTO_API_KEY` and the explicit paid-data
  opt-in `TRADE_LAB_DATABENTO_LIVE_ENABLED=true` set deliberately in your local
  `backend/.env` or process environment; the example file is safe by default.
  Restart the backend after setting or changing either value.
- Phase 5B wires real SDK callbacks to a bounded queue and canonical events only
  after explicit operator start. Tests use fake SDK clients and stay offline.
- Intended schemas: `trades` for canonical tick counts, `mbp-1`/`cmbp-1` BBO for
  quote context, plus `definition`, `status`, and `statistics` as optional
  context. Only `TradeEvent` increments bars.
- Live and replay both call `ApplicationRuntime.process_market_event()` so chart,
  level, touch, observation, and WebSocket behavior stays consistent.
- Live start/stop POSTs include CSRF-style browser-origin protection. Browser
  requests with `Origin` must match `TRADE_LAB_ALLOWED_ORIGINS`; when `Origin` is
  absent but `Referer` is present, the referer origin is checked the same way.
  Local no-origin CLI/operator clients are allowed for development/operator use.
  Non-local no-origin clients must provide the configured operator token.
- Phase 5C manual validation steps are documented in
  `../docs/live-databento-validation-runbook.md`. Real Databento validation is
  operator controlled, does not auto-start, and should use status/preflight checks
  before pressing Start Live.

## Current verification

Latest recorded verification:

- Full pytest: `268 passed, 1 skipped`.
- Ruff: all checks passed.
- Benchmark gate: passed.

## Verification commands

```powershell
cd <repo>\backend
python -m pytest
python -m ruff check src tests
```

The benchmark smoke test is intentionally opt-in:

```powershell
python -m pytest -m benchmark --run-benchmark -s
```

The provisional Phase 2A synthetic candle+level hot-path gate is 100,000 events/sec
on Python 3.13.

## Secrets policy

Runtime configuration reads gitignored local `backend/.env` plus `TRADE_LAB_`
environment variables by default when running from this directory. Environment
variables override `.env` values. Real credentials must not be committed, shared,
pasted into chat, or captured in logs/screenshots. Secret fields are excluded
from `repr`/safe dumps and should remain backend-only.
