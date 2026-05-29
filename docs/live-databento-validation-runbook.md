# Phase 5C Live Databento Validation Runbook

This runbook prepares the first manual live Databento validation for Trade-Lab. The
validation is intentionally operator controlled: the backend does not auto-start a
Databento session, and this document does not introduce trading, execution, risk,
accounts, ML inference, model loading, or raw tick-stream features.

## Current status

The Phase 1-5 implementation is complete through the live Databento adapter,
frontend controls, runbook, and safe preflight helper. Manual live validation is
the only remaining item from the original plan and should be performed during
active market hours. Do not mark live validation complete until the checklist below
has been run with sanitized evidence.

Latest verification before manual live validation:

- Backend full pytest: `268 passed, 1 skipped`.
- Backend ruff: all checks passed.
- Benchmark gate: passed.
- Frontend lint/typecheck/test/build: passed.
- Frontend tests: `97 passed`.

## Safety boundaries

- Market-data only: live mode subscribes to Databento data and feeds the existing
  chart/runtime path only.
- No trading, execution, risk/accounts, or ML/model binaries are involved.
- No raw tick stream should be captured in screenshots or reports.
- Never include `TRADE_LAB_DATABENTO_API_KEY` or an operator token in chat,
  screenshots, terminal output, browser console captures, tickets, or logs.
- When the backend is run from `backend/`, it loads the gitignored `backend/.env`
  for local development. Shell/process environment variables still override
  `.env` values.
- Do not use helper tooling to start live. Live starts only when the operator clicks
  **Start Live** or explicitly calls `POST /api/v1/live/start`.

## Required environment variables

Set these by name in gitignored `backend/.env` or in the backend process
environment. Do not put real values in docs, chat, screenshots, logs, or reports.

| Variable | Purpose |
| --- | --- |
| `TRADE_LAB_DATABENTO_API_KEY` | Backend-only Databento credential. Status APIs report only `api_key_configured: true/false`. |
| `TRADE_LAB_DATABENTO_DATASET` | Databento dataset to subscribe to. |
| `TRADE_LAB_DATABENTO_REQUESTED_SYMBOL` | Databento symbol to request. |
| `TRADE_LAB_DATABENTO_STYPE_IN` | Databento input symbol type. |
| `TRADE_LAB_ALLOWED_ORIGINS` | Browser origins allowed to call the backend. |
| `TRADE_LAB_DATABENTO_LIVE_ENABLED` | Must be set to `true` to enable the manual live-start control path. |

Start Live requires both `TRADE_LAB_DATABENTO_API_KEY` and a deliberate local
`.env` opt-in of `TRADE_LAB_DATABENTO_LIVE_ENABLED=true` in backend
configuration. The example file is intentionally safe by default; copy it, then
change this value to `true` only when ready for live validation. Restart the
backend after setting or changing either value.

If `TRADE_LAB_OPERATOR_TOKEN` is configured, remote/no-local operator clients must
send it in `x-trade-lab-operator-token`. Local browser validation from an allowed
origin does not require typing the token into the UI. Never print or capture the
token value.

## Expected local defaults

These are non-secret software defaults. They are documented for verification only;
do not copy them as environment assignment examples.

- Backend: `http://127.0.0.1:8001`
- Frontend: `http://127.0.0.1:5174`
- Dataset: `GLBX.MDP3`
- Requested symbol: `NQ.c.0`
- Databento input symbol type: `continuous`
- Tick timeframes: `147t`, `987t`, `2000t`

## Local startup

Use repo-relative commands from a clean terminal. For local development, create a
private `backend/.env` from `backend/.env.example` and fill values there, or use
equivalent shell/process-manager environment injection. Real `.env` files are
gitignored and must never be committed or shared.

### 1. Start backend

```powershell
cd backend
python -m trade_lab.api
```

Do not paste real secret values into issue trackers, chat, shared terminals,
screenshots, or command transcripts. If you prefer temporary shell variables,
they will override matching `backend/.env` values.

### 2. Start frontend

```powershell
cd frontend
npm install
npm run dev
```

The frontend defaults to backend `http://localhost:8001` and WebSocket
`ws://localhost:8001/ws/v1`. Override only with public `VITE_API_BASE` and
`VITE_WS_URL` values if needed; never store secrets in frontend variables.

### 3. Optional safe preflight helper

This helper checks local readiness endpoints only. It does not print secret values
and never calls `/api/v1/live/start`.

```powershell
python scripts/live_databento_preflight.py --backend http://127.0.0.1:8001 --frontend http://127.0.0.1:5174
```

Expected checks:

- `GET /health`
- `GET /api/v1/status`
- `GET /api/v1/live/status`
- Frontend URL reachability, if the frontend is running

## Manual validation steps

1. Start the backend from `backend/` with required values in `backend/.env` or
   environment variables.
2. Start the frontend.
3. Open `http://127.0.0.1:5174` or `http://localhost:5174`.
4. Check backend health/status:
   - `/health` returns `ok: true`.
   - `/api/v1/status` shows `engine_ready: true`.
   - `/api/v1/live/status` shows safe live status only.
5. Confirm `api_key_configured` is `true` or `false` only. Do not reveal the key
   value. If `false`, stop and fix the backend environment.
6. Confirm `enabled` is `true`. If `false`, set `TRADE_LAB_DATABENTO_LIVE_ENABLED=true` and restart the backend.
7. Confirm `dataset`, `requested_symbol`, `schemas`, and SDK readiness match the
   intended validation.
8. Click **Start Live** only when ready for a real Databento live session.
9. Watch expected status transitions:
   - before start: `idle`
   - after click: `connecting`
   - if subscription succeeds: `running` and `subscription_ready: true`
   - on failure: `failed` with a sanitized `last_error`
10. If market data arrives, verify chart updates through the normal UI path:
    - tick bars appear/update
    - levels remain visible
    - touches appear if level interaction occurs
11. Switch timeframes and verify rendering remains stable:
    - `147t`
    - `987t`
    - `2000t`
12. Click **Stop Live**.
13. Verify live status changes to stopped/disconnected behavior:
    - live state is no longer `running`
    - feed is disconnected/stopped
    - no additional bars arrive after stop
14. Close the frontend tab and stop the backend process when the run is complete.

## Operator checklist

### Preflight

- [ ] Backend dependencies installed, including Databento SDK if this run should
      connect for real.
- [ ] Required `TRADE_LAB_` values are set in gitignored `backend/.env` or the
      backend process environment.
- [ ] Shell/process environment overrides were checked if they may differ from
      `.env` values.
- [ ] `TRADE_LAB_DATABENTO_LIVE_ENABLED` is enabled for the manual run.
- [ ] `TRADE_LAB_ALLOWED_ORIGINS` includes the frontend origin being used.
- [ ] Terminal/screen recording will not show API key or operator token values.

### Start

- [ ] Backend started on port `8001`.
- [ ] Frontend started on port `5174`.
- [ ] Optional preflight helper completed without backend readiness failures.
- [ ] UI opened from an allowed local origin.

### Observe

- [ ] Health/status/live status checked before starting live.
- [ ] `api_key_configured` confirmed as boolean only.
- [ ] **Start Live** clicked only after readiness checks.
- [ ] Status transitions recorded without secrets.
- [ ] Bars/levels/touches observed if market data arrives.
- [ ] `147t`, `987t`, and `2000t` timeframes checked.
- [ ] BBO/context updates do not create raw tick spam or chart bars by themselves.
- [ ] Event Blotter warning details expand without exposing secrets or local paths.

### Stop

- [ ] **Stop Live** clicked.
- [ ] Status confirms no running live subscription.
- [ ] Browser console checked for sanitized errors only.

### Post-run cleanup

- [ ] Backend process stopped after validation.
- [ ] Secret environment variables cleared from the shell/process manager if needed.
- [ ] Screenshots/logs reviewed for accidental secrets before sharing.
- [ ] Findings recorded with sanitized evidence.

## Pass criteria

- Live starts only after explicit operator action.
- Status transitions are visible and sanitized.
- Trade events update tick bars; quote/context events remain contextual.
- Chart coordinates remain stable across `147t`, `987t`, and `2000t`.
- Stop Live ends the subscription path and post-stop callbacks do not keep updating
  the UI.
- No API keys, operator tokens, raw market data dumps, model binaries, or sensitive
  local paths are captured.

## Troubleshooting

| Symptom | Likely cause | Safe action |
| --- | --- | --- |
| `sdk_available: false` or SDK import error | Databento SDK missing from backend environment | Install backend dependencies/SDK in the active environment, restart backend, re-check status. |
| `api_key_configured: false` | `TRADE_LAB_DATABENTO_API_KEY` not present in backend process environment | Stop before live start, set the env var securely, restart backend. |
| Auth failure after Start Live | Key invalid, disabled, expired, or lacks required access | Capture sanitized `last_error` only; verify account/access outside shared logs. |
| No bars | Market closed, no trades for selected symbol, subscription not ready, or only quote/context events arriving | Check live status, `events_processed`, market hours, and schemas. Bars increment from trade events only. |
| CORS/origin rejected | Browser origin not listed in `TRADE_LAB_ALLOWED_ORIGINS` | Add the exact frontend origin, restart backend, retry from that origin. |
| Symbol/stype mismatch | `TRADE_LAB_DATABENTO_REQUESTED_SYMBOL` does not match `TRADE_LAB_DATABENTO_STYPE_IN` | Use the default `NQ.c.0` with `continuous`, or adjust both consistently. |
| Quote-only/no trades | Quote schema receives BBO/context but trade schema has no trade prints | Confirm `trades` is subscribed and wait for active market trade prints. |
| Benchmark result seems bad/irrelevant | Synthetic benchmark is unrelated to live Databento validation | Do not use benchmark output as pass/fail evidence for this manual validation. |

## Evidence to capture on failure

Capture only sanitized evidence:

- Exact manual step where failure occurred.
- Sanitized backend `GET /api/v1/live/status` JSON with no secrets.
- Sanitized backend `GET /api/v1/status` JSON with no secrets.
- Browser console errors with tokens/headers removed.
- Backend logs with API keys, operator tokens, local private paths, and provider
  credentials redacted.
- Whether the optional preflight helper passed or which endpoint failed.
