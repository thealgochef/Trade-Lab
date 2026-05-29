# Trade-Lab Frontend

Workstation runtime chart for the backend WebSocket contract. The chart renders
backend-aggregated tick bars, display levels, touches, and active observations
with lightweight-charts. Phase 5B shows opt-in Databento live readiness and controls.
ML controls, trading/execution, risk/accounts, and model UI remain intentionally
out of scope.

The original Phase 1-5 frontend scope is complete. Latest recorded frontend
verification passed lint, typecheck, test, and build with `97 passed` tests. The
remaining original-plan item is manual live Databento validation during active
market hours.

The replay panel loads allowlisted source ids from `/api/v1/replay/sources` and
can start, pause, resume, or stop the deterministic `synthetic:nq-demo` replay or
opaque historical ids discovered by the backend under `TRADE_LAB_DATA_PATH`,
including supported local Databento-export MBP-10 Parquet sources. It never asks
for filesystem paths and never displays full paths or filenames. Replay
visualization uses the existing WebSocket/domain store/chart path; no chart bypass
or raw tick stream is added. For MBP-10 replay, chart bars and touches come only
from trade action rows; top-of-book quote context may be shown by backend-derived
state, but deeper book levels are not frontend runtime features.

The live panel calls `/api/v1/live/status`, `/start`, and `/stop`, shows dataset,
requested symbol, schemas, status, and whether the backend has a key configured.
It also shows safe SDK/subscription readiness booleans. It never renders an
API-key input; keys must stay in backend env. Live start remains an explicit
operator action and is market-data only: no trading, execution, risk/accounts, or
model UI is enabled.

For the first real live Databento validation, follow
`../docs/live-databento-validation-runbook.md`. The UI does not auto-start live;
the operator must review status and press **Start Live** manually.

## Commands

```powershell
npm install
npm run dev       # http://127.0.0.1:5174
npm run lint
npm run typecheck
npm run build
npm run test
npm audit --omit=dev
```

The frontend defaults to `http://localhost:8001` and `ws://localhost:8001/ws/v1`.
Override with `VITE_API_BASE` and `VITE_WS_URL` if needed. These values are bundled
into browser code and are public placeholders only; do not store secrets in them.

The UI tolerates an offline backend and shows degraded API/WS state until the
backend is available. Runtime visualization uses snapshot and delta OHLC bars;
raw ticks, model binaries, and local data files are not required by the frontend.
