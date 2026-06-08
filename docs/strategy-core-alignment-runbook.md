# Strategy-Core Promotion and Trade-Lab Alignment Runbook

## Goal

Prevent drift between research strategy behavior and Trade-Lab runtime behavior.

Trade-Lab must answer one question deterministically before replay, paper, or live market-data validation:

```text
Which exact Strategy-Core engine behavior is this Trade-Lab backend running?
```

The answer must be an explicit Strategy-Core commit/version, not "whatever local Strategy-Core folder happens to be on disk."

## Why this exists

Research can change strategy behavior in ways that materially alter runtime output:

- tick-bar construction;
- session/trading-day boundaries;
- level/zone construction;
- touch semantics;
- feature definitions;
- label/outcome rules;
- replay ordering and warm-up behavior;
- model contract schema.

If Trade-Lab silently follows local, unpushed, or unvalidated Strategy-Core edits, replay and live validation can appear green while another machine, CI run, or later deployment executes different logic.

Therefore alignment is handled as a release boundary:

```text
Strategy-Core change -> pushed commit -> Trade-Lab pin bump -> Trade-Lab validation
```

## Architecture boundary

The current boundary is intentional:

```text
Databento files / sockets / provider messages
    -> Trade-Lab source adapters and operator controls
    -> canonical live-compatible market events
    -> Strategy-Core runtime semantics
    -> Trade-Lab API/WebSocket/UI DTOs
```

Strategy-Core owns the meaning that must not drift:

- bars;
- sessions and trading days;
- levels/zones;
- touches;
- runtime/replay semantics;
- versioned strategy contracts.

Trade-Lab owns the application boundary:

- FastAPI routes;
- WebSocket transport;
- frontend/operator controls;
- Databento live adapter startup and stop controls;
- local historical replay catalog rooted at `TRADE_LAB_DATA_PATH`;
- source allowlists and path/secret redaction;
- DTO mapping and UI presentation.

This means Strategy-Core does not need to be a separately running service. Trade-Lab imports it as the canonical engine dependency and feeds it canonical events.

## Pinning rule

Trade-Lab backend must pin Strategy-Core to an explicit Git commit in:

```text
backend/pyproject.toml
```

Current dependency shape:

```toml
"strategy-core @ git+ssh://git@github.com/thealgochef/Strategy-Core.git@<strategy-core-commit-sha>",
```

Do not claim Trade-Lab is aligned with a Strategy-Core change until this pin points at the intended pushed Strategy-Core commit and the validation checklist below passes.

## Development exception

For fast local development, an editable/local Strategy-Core install may be used temporarily to iterate on a change.

That is a development convenience only. It is not an aligned release state.

Before calling Trade-Lab aligned, replace any local/editable dependency with a pushed Strategy-Core commit SHA in `backend/pyproject.toml`, reinstall Trade-Lab backend, and run the full validation checklist.

## Alignment states

### Aligned

Trade-Lab is aligned when all of the following are true:

1. Strategy-Core change is committed and pushed.
2. Trade-Lab `backend/pyproject.toml` pins that exact Strategy-Core commit.
3. Trade-Lab backend environment is reinstalled from that dependency.
4. Strategy-Core tests pass.
5. Trade-Lab backend tests/lint pass.
6. Trade-Lab frontend lint/typecheck/test/build pass when UI/API behavior is affected.
7. Historical replay validation confirms events flow through the Strategy-Core runtime path.
8. Any model-serving change passes the versioned contract/model-bundle checks before paper/live serving.

### Not aligned yet

Trade-Lab is not aligned when any of these are true:

- Strategy-Core has local uncommitted changes used by Trade-Lab.
- Strategy-Core has a new pushed commit but Trade-Lab still pins an older commit.
- Trade-Lab was not reinstalled after the pin changed.
- Validation was run against an editable local checkout but the final pin was not validated.
- A model bundle targets a Strategy-Core engine/contract version Trade-Lab does not validate.

## Promotion process

### 1. Finish and verify Strategy-Core

```bash
cd /root/trading-algos/Strategy-Core
python -m pytest
python -m ruff check src tests
git status --short --branch
git rev-parse HEAD
```

Requirements:

- tests pass;
- lint passes;
- working tree contains only intentional changes;
- final commit is pushed to the Strategy-Core remote.

### 2. Bump Trade-Lab's Strategy-Core pin

Edit:

```text
/root/trading-algos/Trade-Lab/backend/pyproject.toml
```

Change only the commit SHA in the Strategy-Core dependency line unless the package URL/name intentionally changes.

If a backend lockfile is introduced later, update it in the same change. At the time this runbook was added, the Trade-Lab backend has no lockfile.

### 3. Reinstall Trade-Lab backend

```bash
cd /root/trading-algos/Trade-Lab/backend
python -m pip install -e ".[dev]"
```

Then verify the installed Strategy-Core resolves to the expected engine/commit/version. Use direct package metadata or a small Python import check appropriate to the current package fields.

### 4. Run Trade-Lab backend validation

```bash
cd /root/trading-algos/Trade-Lab/backend
python -m pytest
python -m ruff check src tests
```

If the Strategy-Core change affects replay/runtime/model contracts, add focused tests for that path before relying on broad suite pass/fail.

### 5. Run frontend validation when API/UI contracts can change

```bash
cd /root/trading-algos/Trade-Lab/frontend
npm install
npm run lint
npm run typecheck
npm run test
npm run build
```

Frontend validation is required when the change affects API DTOs, WebSocket payloads, chart behavior, warnings/notices, model status, replay status, or operator controls.

### 6. Run historical replay validation

Use an allowlisted historical source from `TRADE_LAB_DATA_PATH`; do not pass raw filesystem paths through the browser/API.

Minimum acceptance:

- selected source id is `historical:*` and schema is live-compatible, such as `mbp-10` projection;
- `events_processed > 0`;
- `last_error == null`;
- bars/touches/session state come from Strategy-Core runtime path;
- expected info notices, such as ignored historical-only MBP-10 deeper-book fields, are not treated as true data-quality failures;
- no browser JavaScript errors during the run.

### 7. Model-serving gate, when applicable

Market-data runtime alignment is not the same as model-serving alignment.

Before model/paper serving, additionally verify:

- model artifact declares a supported Strategy-Core `engine_version` / contract version;
- feature order and schema match exactly;
- class mapping and thresholds are present;
- checksums validate;
- one raw-data slice produces matching research vs Trade-Lab touches, features, and outcomes.

If these checks are not complete, Trade-Lab may be market-data-runtime aligned but model serving remains blocked.

## Reporting template

Use this wording in handoffs:

```text
Strategy-Core alignment status: aligned / not aligned
Strategy-Core pinned commit in Trade-Lab: <sha>
Strategy-Core intended commit: <sha>
Backend reinstall: done / not done
Strategy-Core verification: <commands + pass/fail>
Trade-Lab backend verification: <commands + pass/fail>
Trade-Lab frontend verification: <commands + pass/fail or not applicable>
Historical replay validation: <source id, schema, events_processed, last_error>
Model-serving parity: passed / blocked / not in scope
Limitations: <anything unverified>
```

## Hard rules

- Do not claim Trade-Lab is aligned from memory alone.
- Do not claim alignment from local editable Strategy-Core alone.
- Do not let Trade-Lab silently auto-track local Strategy-Core for release validation.
- Do not duplicate Strategy-Core strategy semantics inside Trade-Lab adapters.
- Do not treat synthetic replay as Strategy-Core data-path validation.
- Do not use live Databento validation as a substitute for historical replay and unit tests.
- Do not enable live trading or broker execution as part of this alignment process.
