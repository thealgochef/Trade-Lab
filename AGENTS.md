# AGENTS.md

## Repo Shape

- This is a split repo, not a root Node or Python package: backend commands run in `backend/`, frontend commands run in `frontend/`.
- The root `package-lock.json` is empty metadata; do not run or add npm workflow at the repo root unless a root `package.json` is introduced.
- Backend uses a `src/` layout with package metadata in `backend/pyproject.toml`; install/editable setup from `backend/` before running `python -m trade_lab.api` in a fresh environment.

## Backend Commands

- Setup from `backend/`: `python -m pip install -e ".[dev]"`.
- Dev server from `backend/`: `python -m trade_lab.api` serves FastAPI on `127.0.0.1:8001` by default.
- Full verification from `backend/`: `python -m pytest` then `python -m ruff check src tests`.
- Focused backend test from `backend/`: `python -m pytest tests/test_api_contract.py::test_name` or `python -m pytest tests/test_api_contract.py -k replay`.
- Benchmark gate is opt-in: `python -m pytest -m benchmark --run-benchmark -s`; normal pytest skips `@pytest.mark.benchmark` tests.

## Frontend Commands

- Setup from `frontend/`: `npm install`.
- Dev server from `frontend/`: `npm run dev` serves Vite on `127.0.0.1:5174` with `strictPort`.
- Frontend verification from `frontend/`: `npm run lint`, `npm run typecheck`, `npm run test`, `npm run build`.
- Focused frontend test from `frontend/`: `npm run test -- src/config.test.ts` or another `src/**/*.test.{ts,tsx}` path.

## Runtime And Env

- Backend settings load `backend/.env` plus `TRADE_LAB_` environment variables; process env overrides `.env`.
- Never read, print, commit, or paste real `backend/.env` values; `.env.example` files are safe placeholders.
- Frontend env uses public `VITE_API_BASE` and `VITE_WS_URL` only; never put keys, tokens, local paths, or trading secrets in `VITE_*`.
- Frontend defaults to `http://localhost:8001` and `ws://localhost:8001/ws/v1`; backend CORS defaults also allow `127.0.0.1:5174` and `localhost:5174`.

## Architecture Boundaries

- `backend/src/trade_lab/domain` is pure Trade-Lab DTO/compatibility logic; keep FastAPI, Pydantic DTOs, Databento SDK objects, filesystem paths, and frontend payload assumptions out of it.
- Live and replay must both feed canonical events through `ApplicationRuntime.process_market_event()` and then `StrategyCoreService`; do not add separate candle/level/touch semantics for one mode.
- Only trade events increment Strategy-Core tick bars and create authoritative level/touch behavior; quote/book/status/definition/statistics events are context only.
- Prices are integer ticks for NQ (`0.25` points); do not compare or round price-like values with binary floats in backend domain code.
- API/WebSocket DTO mapping belongs at the API boundary; WebSocket envelopes are versioned as `ws.v1`.

## Replay And Live Safety

- Public replay starts from opaque allowlisted source ids such as `synthetic:nq-demo`; never accept browser-supplied filesystem paths.
- Historical replay discovery is rooted at `TRADE_LAB_DATA_PATH`, blocks symlink/reparse traversal, and must not expose full local paths or filenames in API/WS/errors.
- Supported historical runtime schemas are live-compatible projections such as trades, BBO/MBP-1, and supported Databento-export MBP-10; deeper MBP-10 levels are ignored as runtime features.
- Live Databento is market-data only and never auto-starts; starting it requires explicit operator action plus `TRADE_LAB_DATABENTO_API_KEY` and `TRADE_LAB_DATABENTO_LIVE_ENABLED=true`.
- The Databento SDK is optional and not part of the normal backend dev extra; tests use fake SDK clients and must stay offline.
- Live start/stop browser requests are origin-checked against `TRADE_LAB_ALLOWED_ORIGINS`; non-local no-origin operator clients need `x-trade-lab-operator-token` when configured.

## Frontend Notes

- The frontend intentionally tolerates an offline backend and shows degraded API/WS state; do not make initial render require backend availability.
- Charting consumes backend-aggregated OHLC tick bars, not raw ticks; `bar_index`/`bar_id` provide stable chart coordinates when wall-clock timestamps collide.
- Supported chart timeframes are `147t`, `987t`, and `2000t`; keep frontend state and backend settings aligned when changing them.
- Keep API/client error details sanitized; existing client code redacts path-like strings and secret-shaped fields before surfacing errors.

## Reference Docs

- `docs/architecture.md` has the boundary invariants and out-of-scope modules.
- `docs/market-data-contract.md` defines canonical event and tick-bar semantics.
- `docs/strategy-core-alignment-runbook.md` defines the Strategy-Core pin/bump validation process for keeping Trade-Lab aligned with research/runtime changes.
- `docs/live-databento-validation-runbook.md` is the source of truth for real live validation; the preflight script only performs safe GET checks and never starts live.
