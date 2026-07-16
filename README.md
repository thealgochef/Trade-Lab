# Trade-Lab

Trade-Lab is a clean v2 foundation for an institutional-grade NQ futures trading
and replay dashboard: a FastAPI backend that owns the market-data runtime, and a
React workstation UI that renders it.

---

## Running the application

### What has to be running

**Two processes: the backend API and the frontend dev server. The frontend alone
does nothing useful — it is a pure client.**

| Process | Command | Address | Required? |
|---|---|---|---|
| Backend API (FastAPI + uvicorn) | `python -m trade_lab.api` from `backend/` | `http://127.0.0.1:8001` | **Yes** — serves the REST API *and* the `/ws/v1` WebSocket the chart streams from |
| Frontend (Vite dev server) | `npm run dev` from `frontend/` | `http://127.0.0.1:5174` | Yes, to use the UI |

**Nothing else runs.** There is no database, no message broker, no background
worker, no separate WebSocket process, and no Redis/Celery-style service. The
REST API and the WebSocket are served by the same uvicorn process, journaling
writes plain JSONL files under `backend/data/journal/`, and Strategy-Core is a
pip-installed library (not a service). Databento is an external paid API that is
contacted **only** when you explicitly start live market data.

The UI tolerates an offline backend — it loads and shows degraded API/WS state —
so start order does not matter, though starting the backend first avoids a
transient disconnected banner.

### Prerequisites

- **Python 3.13+** (backend)
- **Node 20+ / npm** (frontend)
- **git** — the backend pins `strategy-core` to a git commit, so the first
  install clones it over the network
- Optional, per feature: a Databento API key (live market data), local Parquet
  files (historical replay), a model-bundle directory (inference)

### First-time setup

```powershell
# Backend
cd backend
python -m pip install -e ".[dev]"      # add the live extra for real market data: ".[dev,live]"
copy .env.example .env                 # then edit .env (see Configuration below)

# Frontend
cd ..\frontend
npm install
```

The `[live]` extra installs the optional Databento SDK (`databento>=0.79`).
Without it the backend runs fine — replay and the UI work; live start reports
`sdk_available: false`. The `>=0.79` floor is deliberate: earlier releases write
the live subscribe/start to the gateway from the calling thread, which can wedge
the feed silently.

### Run it (two terminals)

```powershell
# Terminal 1 — backend
cd backend
python -m trade_lab.api
```

```powershell
# Terminal 2 — frontend
cd frontend
npm run dev
```

Then open **http://127.0.0.1:5174**.

The backend logs to stderr at INFO with UTC timestamps (set
`TRADE_LAB_LOG_LEVEL=DEBUG` for the full Databento session handshake).

### Verify it is up

```powershell
curl http://127.0.0.1:8001/health          # {"ok":true,...}
curl http://127.0.0.1:8001/api/v1/status   # runtime/feed/replay/live/inference state
```

If the UI shows a connected status bar and an empty chart, both processes are
healthy — an empty chart is expected until you start a replay or go live.

### What you can do once both are running

1. **Synthetic replay — works with zero configuration.** Pick
   `synthetic:nq-demo` in the Replay panel and press start. This is the fastest
   way to confirm the whole stack (bars, levels, touches, WebSocket) is wired.
2. **Historical replay — needs `TRADE_LAB_DATA_PATH`.** Local Databento-export
   Parquet files under that root appear in the Replay panel as opaque ids.
3. **Live market data — needs a Databento key, the paid-data opt-in, and the
   `[live]` extra.** Set `TRADE_LAB_DATABENTO_API_KEY` and
   `TRADE_LAB_DATABENTO_LIVE_ENABLED=true` in `backend/.env`, restart the
   backend, then press **Start Live** (or `POST /api/v1/live/start`). Live never
   auto-starts.
4. **Inference — optional, off by default.** No model is active at startup. Point
   `TRADE_LAB_MODELS_PATH` at a directory of model bundles and activate one from
   the Model panel (or `POST /api/v1/models/activate`). Predictions, outcomes,
   and drops are appended to `backend/data/journal/<trading-day>.jsonl`.

**Live start is billable.** Starting live fetches ~2 prior trading days of trade
history plus a retention-sized quote window from the Databento Historical API
(~50 MB), replays it through the engine to warm the chart, and then subscribes to
the live gateway. Expect roughly **2 minutes** of `warm_start_state: "warming"`
before the status flips to `"live"`; inference is suppressed for the whole
warm-replay drain so it cannot journal fabricated real-time rows. If no live
message arrives within `TRADE_LAB_LIVE_WATCHDOG_SECONDS` (default 120) after the
drain, the feed is marked DEGRADED and reconnects once, then FAILED.

### Configuration

Backend settings load from `backend/.env` and use the `TRADE_LAB_` prefix. That
path is resolved from the installed package location, not the working directory,
so an editable install (`pip install -e .`) picks it up no matter where you launch
from. Real `.env` files are gitignored and must never be committed, shared, pasted
into chat, or captured in logs/screenshots. Process environment variables override
`.env` values.

| Variable | Default | Purpose |
|---|---|---|
| `TRADE_LAB_BACKEND_HOST` / `_PORT` | `127.0.0.1` / `8001` | Where the API listens |
| `TRADE_LAB_ALLOWED_ORIGINS` | `http://localhost:5174,http://127.0.0.1:5174` | Browser origin allowlist (CORS + WS) |
| `TRADE_LAB_LOG_LEVEL` | `INFO` | Root log level; `DEBUG` also raises the Databento SDK logger |
| `TRADE_LAB_DATABENTO_API_KEY` | *(unset)* | Secret. Backend-only; the UI never accepts a key |
| `TRADE_LAB_DATABENTO_LIVE_ENABLED` | `false` | Explicit paid-data opt-in; required for Start Live |
| `TRADE_LAB_DATABENTO_REQUESTED_SYMBOL` | `NQ.c.0` | Symbol to subscribe (with `_STYPE_IN`) |
| `TRADE_LAB_LIVE_WATCHDOG_SECONDS` | `120` | Post-drain silence before DEGRADED + single reconnect |
| `TRADE_LAB_DATA_PATH` | *(unset)* | Root for historical replay discovery |
| `TRADE_LAB_MODELS_PATH` | *(unset)* | Root for model-bundle discovery |
| `TRADE_LAB_JOURNAL_PATH` | `backend/data/journal` | Append-only prediction journal |
| `TRADE_LAB_OPERATOR_TOKEN` | *(unset)* | Lets non-localhost clients use live/model controls |

Frontend configuration is build-time and **public** (bundled into browser code —
never put secrets here): `VITE_API_BASE` (default `http://localhost:8001`) and
`VITE_WS_URL` (default `ws://localhost:8001/ws/v1`).

### Ports

`8001` backend (REST + `/ws/v1`), `5174` Vite (strict — it will not fall back to
another port). If you change the backend port, update `VITE_API_BASE` /
`VITE_WS_URL`; if you change the frontend port, add it to
`TRADE_LAB_ALLOWED_ORIGINS`.

### Production note

`npm run build` emits a static bundle to `frontend/dist/`. The backend does **not**
serve it — there is no static-file mount — so a real deployment needs a static
host (and `VITE_API_BASE`/`VITE_WS_URL` baked in at build time) plus the backend
process. The two-terminal dev flow above is the supported path today.

### Troubleshooting

| Symptom | Cause / fix |
|---|---|
| UI loads but status bar shows disconnected | Backend not running, wrong port, or origin missing from `TRADE_LAB_ALLOWED_ORIGINS` |
| `Start Live` → 400 "onboarding is disabled" | `TRADE_LAB_DATABENTO_LIVE_ENABLED` is not `true`; restart the backend after editing `.env` |
| `Start Live` → 403 | Live/model controls require localhost access or `x-trade-lab-operator-token` |
| `sdk_available: false` | Install the optional SDK: `pip install -e ".[dev,live]"` |
| Live status sits at `warming` | Normal for ~2 min (warm-start replay). Genuine silence trips the watchdog and reconnects |
| Replay panel lists only `synthetic:nq-demo` | `TRADE_LAB_DATA_PATH` is unset, unreadable, or holds no supported schema |
| `.env` edits have no effect | Restart the backend (settings load once at startup); note a shell/process env var of the same name wins over `.env` |
| `No module named trade_lab` | The backend package is not installed: `pip install -e ".[dev]"` from `backend/` |

`scripts/live_databento_preflight.py` checks local readiness endpoints before a
live run; it never starts live and never prints secret values.

### Tests and checks

```powershell
cd backend
python -m pytest                    # 466 passed, 1 skipped
python -m ruff check src tests

cd ..\frontend
npm run typecheck
npm run test                        # 154 passed
npm run lint
npm run build
```

---

## Project shape

- `backend/` — typed config, domain models, serialization contracts, historical
  Parquet replay adapter, runtime/replay state management, live Databento wiring,
  inference/journal seam, WebSocket broadcaster, bounded backpressure, and
  smoke/benchmark tests. See `backend/README.md`.
- `frontend/` — workstation UI, charting client, replay controls, live Databento
  panel, and the model-activation panel.
- `docs/` — architecture notes and operating guides, including the Strategy-Core
  promotion/alignment runbook and the live Databento validation runbook.
- `scripts/` — development and operational helpers.

## Current status

- Core implementation complete through Phase 5C, plus the inference/journal seam
  and the live warm-start hardening.
- Latest backend verification: `466 passed, 1 skipped`; ruff clean.
- Latest frontend verification: typecheck clean; `154 passed` across 16 files.
- Live market data has been exercised against the real gateway during an open
  session (warm start ~2 min, `warming` → `live` flip, live events flowing,
  clean stop), market-data only with no model active. The full operator
  validation checklist below is still the gate for trusting live numbers.

## Completed phases

- [x] Phase 1 — project foundation, specs, architecture docs, and safety
  constraints.
- [x] Phase 2A — backend domain core: canonical events, integer tick-price
  utilities, session calendar, `147t`/`987t`/`2000t` tick candle engine, session
  levels, touch/observation engine, and benchmark gate.
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

Live is market-data only and never auto-starts; operators must explicitly call
`POST /api/v1/live/start` or press Start Live in the UI. The API key is backend
environment only via `TRADE_LAB_DATABENTO_API_KEY`; for local development this may
be placed in the gitignored `backend/.env`. There is no browser key entry, and
safe status returns only booleans such as `api_key_configured`, `sdk_available`,
and `subscription_ready`. Start Live also requires the explicit paid-data opt-in
`TRADE_LAB_DATABENTO_LIVE_ENABLED=true`; deliberately set both values in the
process environment or local `backend/.env` before starting/restarting the
backend. The example env file keeps live validation disabled by default.

On start the runtime resets, the prior trading day's PDH/PDL seed loads, and the
feed replays two prior trading days plus the current partial one from the
Historical API before subscribing to the live gateway. Databento SDK callbacks are
bridged through a bounded adapter queue, normalized to canonical events, and then
processed by the same runtime path used by fake feeds and replay. Tests use fake
SDK clients only and do not make Databento network calls. There is no trading,
execution, or risk/accounts layer.

## Manual live Databento validation

Before the first real live Databento run, follow
`docs/live-databento-validation-runbook.md`. Live validation is manual/operator
controlled, market-data only, and has no auto-start path. The optional
`scripts/live_databento_preflight.py` helper checks local readiness endpoints but
never starts live and never prints secret values.
