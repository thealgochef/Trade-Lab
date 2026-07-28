# WEDGE_CAPTURE — live-feed post-drain wedge: state capture + instrumented repro

Capture date: 2026-07-07 evening (US/Central, UTC-05). TL @ c92f13b (platform-refactor), SC pinned @ 1650327.
Operator: Claude session, per LIVE-WEDGE CAPTURE instructions. **No code changes, no commits.** All timestamps local (UTC-05) unless suffixed Z.

Context: WARM_PERF_RECON.md (written tonight, file mtime 2026-07-07 20:57:00) observed the RUNNING backend
(PID 2668, `python -m trade_lab.api`, port 8001, symbol NQU6/raw_symbol, dataset GLBX.MDP3) drain all
24,979,246 warm-start events by ~01:30:32Z and then go silent: `warm_start_state` stuck at `"warming"`,
`events_processed` frozen, one ESTABLISHED gateway TCP, zero live events for 45+ minutes with Globex open.
That is the wedge this capture targets.

---

## PHASE A — capture the wedged state

### A.0 PREMISE CHANGE: the wedged backend is NOT running at capture time

The capture began at **21:25:43** local. The wedged process (PID 2668) no longer exists.

Evidence, in capture order:

- **21:25:43** — `Get-CimInstance Win32_Process` filtered on `uvicorn|trade_lab|databento|python` in
  CommandLine: only VS Code tooling (`pet.exe`, pylance) and the scan's own powershell matched. No backend.
- **~21:26** — full `Get-NetTCPConnection -State Listen` table: **no listener on 8001** (or any plausible
  backend port). Listeners present: 135/139/445 (system), 1337/13331/13337/13344 (Razer), 5040 (svchost),
  5432 (postgres), 6463 (Discord), 7680, 9180 (lghub), 21000 (Starter), 25347/54423 (VS Code), 42050
  (OneDrive), 49664-49678 (system), 55000 (RazerCortex), 65444 (RzDiagnostic).
- **~21:26** — process scan by Name (`python|uvicorn|py.exe`): none. `wsl --list --running`: "There are
  no running distributions." (rules out a WSL-hosted backend).
- **21:29:49.280** — endpoint probes, verbatim:
  ```
  http://127.0.0.1:8001/api/v1/live/status -> FAILED: Unable to connect to the remote server
  http://127.0.0.1:8001/api/v1/status      -> FAILED: Unable to connect to the remote server
  http://127.0.0.1:8001/health             -> FAILED: Unable to connect to the remote server
  ```
- **~21:29** — `Get-NetTCPConnection -State Established` filtered to RemotePort 13000 (Databento live
  gateway port): **NONE**. No process on the box holds a gateway connection.
- **~21:30** — Windows Application event log, last 6 h, Event IDs 1000/1001/1002 (application
  crash/hang/WER) matching `python`: **no events**. The process did not crash in an OS-recorded way.

**Exit window:** alive when WARM_PERF_RECON.md was finalized (mtime 20:57:00), gone by 21:25:43.
Cause of exit is unrecorded — consistent with a terminal close / Ctrl+C in that ~29-minute window, not a
crash. Consequence: Phase A items 1, 3, 4 cannot be executed against the wedged process; what could be
salvaged is below.

**Refinement (found during Phase C teardown):** `backend/data/journal/2026-07-06.jsonl` was last written
at **21:23:15** tonight (and `2026-07-07.jsonl` at 20:23:25). The only journal-write path at a frozen
feed is the stop flush — `live.py:308 flush_resolver` journals past-cutoff drops on `POST /live/stop`,
keyed by the record's event trading day. So the old process received a **live stop at ~21:23:15** and
exited within the following ~2.5 minutes. It was stopped deliberately, not crashed.

### A.1 Status endpoints (instruction item 1)

Not capturable — connection refused (verbatim probes above, 21:29:49.280). The last known status of the
wedged process, as recorded tonight in WARM_PERF_RECON.md (observed via the then-live status endpoint):

> `state=running`, `warm_start_state=warming` (never flipped), `events_processed` frozen at 24,979,246,
> `last_event_ts_utc` pinned at `2026-07-08T00:59:59.848Z` (the availability clamp), `feed_state=connected`,
> process ~idle (0.4% of one core), one ESTABLISHED TCP to the live gateway — and zero live events for
> 45+ minutes while Globex was open, with no DEGRADED status and no error surfaced.

### A.2 Backend log tail (instruction item 2)

**There is no backend log file — none has ever existed.** The backend logs only to the launching
terminal's stderr, and that terminal is gone with the process. Evidence:

- Glob `**/*.log` over the repo: only batch-run artifacts (W3_GATE_RUN*.log, w3b_repro/, w3b_bench/,
  scratch_seed_parity_run.log). Nothing written by the API server.
- No file in repo root or `backend/` modified in the last 36 h is a server log (newest files are the
  recon docs and CI zips).

Why nothing was persisted — the app never configures logging:

- Launch path `backend/src/trade_lab/api/__main__.py:10-15`: `uvicorn.run("trade_lab.api.app:create_app",
  factory=True, host=..., port=...)` — **no `log_config`, no `log_level`**.
- Grep over `backend/src/trade_lab` for `basicConfig|dictConfig`: **zero hits**. (The only
  `logging.basicConfig` in the repo is the offline script `backend/scripts/w3b/headless_replay.py:331`.)
- Every module gets `logger = logging.getLogger(__name__)` (live.py:42, databento.py:51, runtime.py:43,
  journal.py:21, replay.py:29, ...) and propagates to the **unconfigured root logger**.

Net effect for the wedged run: uvicorn's own loggers (`uvicorn`, `uvicorn.error`, `uvicorn.access`) had
handlers (uvicorn's defaults) and printed to the dead terminal; all `trade_lab.*` and `databento.*`
records ≥ WARNING went only through Python's `logging.lastResort` handler to the same dead terminal;
**INFO-level records from the app and the entire databento SDK were emitted to nowhere** (lastResort's
level is WARNING). So even if the terminal scrollback existed, subscribe/session/auth lines at INFO/DEBUG
were never rendered anywhere. This is itself a finding: the wedge was silent partly because the
deployment has no persistent logging surface.

### A.3 py-spy thread dump (instruction item 3)

Not capturable — no PID. py-spy 0.4.2 confirmed installed and ready for the Phase C repro.
(From WARM_PERF_RECON.md, the wedged process was ~idle at 0.4% of one core — consistent with all threads
parked, none spinning; a Phase C wedge dump will answer whether the SDK live-client thread is alive.)

### A.4 netstat / gateway sockets (instruction item 4)

Not capturable for the wedged process (gone). Present-state check at ~21:29: no ESTABLISHED connection
to remote port 13000 (Databento live gateway) from any process — the wedged process's gateway socket
(observed ESTABLISHED tonight in WARM_PERF_RECON.md) died with it. Byte-counter sampling therefore moot
for Phase A; it is planned for the Phase C wedge (Windows exposes no per-connection byte counters, so
per-process IO deltas over 60 s are the proxy, plus the SDK DEBUG log itself).

### A.5 Python logging state (instruction item 5)

Answered by code inspection (A.2): the `databento` SDK logger has **no handler and no level set anywhere
in the deployment** — it inherits the unconfigured root (effective WARNING via lastResort, stderr-only).
The backend's "logging config" does not exist; the closest thing is the absence of `log_config` in
`backend/src/trade_lab/api/__main__.py:10-15`. Root/backend effective state in the wedged run:

| Logger | Handler | Effective level | Where output went |
|---|---|---|---|
| root | none (lastResort) | WARNING | dead terminal stderr |
| `trade_lab.*` | none, propagate→root | WARNING-visible only | dead terminal stderr |
| `databento` (SDK, incl. live session) | none, propagate→root | WARNING-visible only | dead terminal stderr |
| `uvicorn`, `uvicorn.error`, `uvicorn.access` | uvicorn defaults | INFO | dead terminal |

---

## PHASE B — controlled stop

**Moot.** There is no process to stop (Phase A.0). No stop endpoint was POSTed; there is no flush
behavior, orphan-warning, or journal-drop evidence to capture because the feed and its queues died with
the process. For Phase C reference, the stop path is `live.py:288-309`: cancel reconnect task → stop the
strategy-core live runner → `flush_resolver(now)` finalizes past-cutoff open setups (flushed drops ride
the normal DroppedPrediction surface) → emit DISCONNECTED "live feed stopped".

---

## PHASE C — one instrumented repro attempt

### C.1 Instrumentation + launch (item 7)

The deployment has no logging config to edit (A.2), so DEBUG was enabled **for this run only** via a
wrapper launcher outside the repo (scratchpad `wedge_launcher.py`): it attaches a DEBUG file handler +
WARNING stderr handler to root, sets `databento` and `databento.live` loggers to DEBUG, then calls the
stock `trade_lab.api.__main__.main()`. uvicorn's default dictConfig has `disable_existing_loggers=False`
and does not touch root handlers, so the instrumentation survives `uvicorn.run()`. Zero repo changes.

- **21:33:35.721** — backend launched detached (WMI `Win32_Process.Create`, cwd `backend\` so `.env`
  loads), cmd wrapper PID 34196 → **python PID 36792** (created 21:33:36).
- **02:33:36.530Z** — first DEBUG-log line confirms the launcher is active. DEBUG log:
  scratchpad `WEDGE_RUN_debug.log`; status polls appended to `wedge_status_poll.jsonl` (20 s cadence).
- **21:33:46** — `/health` 200: `{"ok":true,"service":"trade-lab-backend","version":"0.1.0"}`.
- **21:33:56.489** — pre-start `/api/v1/status` (verbatim, key parts): `runtime_mode:"idle"`,
  `feed_state:"disconnected"`, live block `{"state":"idle","requested_symbol":"NQU6","dataset":"GLBX.MDP3",
  "schemas":["trades","mbp-1","definition","status","statistics"],"api_key_configured":true,"enabled":true,
  "sdk_available":true,"subscription_ready":true,"events_processed":0,...,"warm_start_state":null}`.
- **21:34:0x** — `POST /api/v1/live/start` issued (localhost, no Origin header — authorized per
  `app.py:90-130`). The call blocks server-side through the warm fetch; response captured when it returns.
- **02:34:12-18Z** — DEBUG log: `databento.historical.client` initialized (hist gateway),
  `prior-day summary loaded for 2026-07-07` (live.py:331). Warm fetch (trades + mbp-1, one
  `to_thread`, `databento.py:300-305`) in flight.
- **02:34:20.961Z** — poller: `UP state=connecting warm=warming events=0`.

Environment deltas vs the wedged run (for interpretation): no model activated this run (wedge is
feed-level; also avoids the known warm-replay journal-duplication), and no dashboard/WebSocket client
connected. Same symbol/dataset/schemas/config path otherwise.

**Expected DEBUG signatures at the post-drain live subscribe** (databento SDK 0.71.0, site-packages
`databento/live/`): `client.py:354` "adding user callback", `client.py:459` "starting live client",
`session.py:626/631` gateway resolve + "connecting to remote gateway", `protocol.py:191` "established
connection to gateway", `protocol.py:446/456` CRAM challenge/reply (`:471` on auth error),
`protocol.py:368` "sending start", then per-traffic lines: `protocol.py:257` "read N bytes from remote
gateway", `protocol.py:386` "dispatching <RecordType>", `protocol.py:399` "gateway heartbeat".
Diagnostic decision table if wedged: heartbeats but no dispatches → session alive, subscription/symbol
delivers nothing; no reads at all → TCP up but gateway never sends (start/subscribe not effective);
CRAM error line → auth; no "established connection" → connect never ran.

### C.2 Run timeline (recorded live)

- **21:34:11.964** — `POST /api/v1/live/start` issued.
- **21:37:09.173** — POST returned **HTTP 200** after 2 m 57 s (the server-side warm fetch: trades +
  mbp-1 in one `to_thread`). Response verbatim:
  ```json
  {"state":"running","requested_symbol":"NQU6","dataset":"GLBX.MDP3","schemas":["trades","mbp-1","definition","status","statistics"],"api_key_configured":true,"enabled":true,"sdk_available":true,"subscription_ready":true,"events_processed":0,"last_event_ts_utc":null,"last_error":null,"started_at_utc":"2026-07-08T02:34:18.958436+00:00","stopped_at_utc":null,"warm_start_state":"warming","warm_start_events":0}
  ```
- **02:37:20.978Z** — poller: drain moving, 282,701 events, replay cursor at Sun 7/5 23:52Z.
- **02:54:28.130Z** — drain complete: `Databento warm-start fallback replayed 25528682 historical
  records` + seam warning `slice ends 1468.1 s before live subscribe`. Live subscribe begins.
- **02:54:29.480Z** — LAST LINE EVER WRITTEN to the DEBUG log during the live phase (`sending start`).
- **02:59:41.663Z** — poller: `FROZEN events_processed unchanged for 5 min: state=running warm=warming
  events=25528682 warm_events=25528682 last_ts=2026-07-08T02:29:59.896228+00:00 err=None`.
- **03:04:41.690Z** — poller: `FROZEN-10MIN` → `WEDGE-CONFIRMED frozen >=10 min while warm_start_state
  != live`. **The wedge REPRODUCED, deterministically (2 for 2), with the identical signature** to the
  original: events frozen at exactly the warm total, `warm_start_state` never flipped, `last_event_ts_utc`
  pinned at the availability clamp, no error surfaced.

### C.3 The wedged state, instrumented capture (steps 1-4 repeated on the new wedge)

**Status endpoints (22:00:07.314), verbatim `/api/v1/live/status`:**
```json
{"state":"running","requested_symbol":"NQU6","dataset":"GLBX.MDP3","schemas":["trades","mbp-1","definition","status","statistics"],"api_key_configured":true,"enabled":true,"sdk_available":true,"subscription_ready":true,"events_processed":25528682,"last_event_ts_utc":"2026-07-08T02:29:59.896228+00:00","last_error":null,"started_at_utc":"2026-07-08T02:34:18.958436+00:00","stopped_at_utc":null,"warm_start_state":"warming","warm_start_events":25528682}
```
`/api/v1/status` concurrently: `runtime_mode:"live"`, `feed_ready:true`, `feed_state:"connected"`,
`session:"asia"`, `trading_day:"2026-07-08"`, `inference:{"error_count":0,"last_error":null}`. No
DEGRADED anywhere — the operator-facing surface is entirely green while the feed is dead.

**py-spy dump (22:00:00.402, PID 36792) — the SDK live-client thread IS alive:**
```
Thread 33328 (idle): "MainThread"
    _poll (asyncio\windows_events.py:775)  <- uvicorn's proactor loop, waiting in select
    ... run (uvicorn\server.py:75) ... main (trade_lab\api\__main__.py:10)
Thread 25580 (idle): "asyncio_0"   — idle executor worker
Thread 9652 (idle): "databento_live"
    _poll (asyncio\windows_events.py:775)  <- the SDK's PRIVATE event loop, parked in select,
    select (asyncio\windows_events.py:446)    waiting for IOCP completions that never come
    _run_once (asyncio\base_events.py:1995)
    run_forever (asyncio\base_events.py:678)
Thread 29220 (idle): "asyncio_0"   — idle executor worker
```
Second dump ~4.5 min later: identical. No thread is blocked on a lock, none is spinning; everything is
parked waiting on I/O. 20 OS threads total, CPU total 17:21 (all from the drain), RSS 1053 MB.

**Sockets (22:00:07):** exactly one gateway connection —
`192.168.1.229:54961 -> 209.127.153.140:13000 ESTABLISHED` (glbx-mdp3.lsg.databento.com). Plus the
8001 listener/HTTP pairs and two localhost self-pairs (asyncio self-pipes). Re-sampled at 22:04:42:
unchanged, still ESTABLISHED.

**Bytes moving? No.** Process-wide IO counters (Win32_Process), two samples 151 s apart:
`ReadTransferCount 81,970,723 -> 81,970,723` (delta **0**), `ReadOperationCount 719,819 -> 719,819`
(delta **0**), WriteTransfer +818 B / +12 ops (= the status poller's HTTP responses). Caveat: these are
process-level counters, but the authoritative per-socket witness is the SDK DEBUG log below — the
protocol logs every single `data_received` at DEBUG, and there were none.

### C.4 The DEBUG evidence — the live subscribe, verbatim (step 8)

The entire instrumented log through the wedge is 33 lines / 4.8 KB. The live-subscribe window, verbatim
(note the **thread annotations** — they are the finding):

```
02:54:28.130Z INFO  trade_lab.adapters.databento [MainThread]: Databento warm-start fallback replayed 25528682 historical records
02:54:28.130Z WARN  trade_lab.adapters.databento [MainThread]: warm-start fallback slice ends 1468.1 s before live subscribe (historical availability lag)
02:54:28.130Z INFO  databento.live.client  [MainThread]: adding user callback _provider_callback
02:54:28.130Z INFO  databento.live.client  [MainThread]: subscribing to schema=trades stype_in=raw_symbol symbols='['NQU6']' start=now snapshot=False
02:54:28.131Z DEBUG databento.live.session [databento_live]: using default gateway for dataset GLBX.MDP3
02:54:28.131Z INFO  databento.live.session [databento_live]: connecting to remote gateway
02:54:28.164Z DEBUG databento.live.protocol [databento_live]: established connection to gateway
02:54:28.164Z DEBUG databento.live.session [databento_live]: connected to glbx-mdp3.lsg.databento.com:13000
02:54:28.171Z DEBUG databento.live.protocol [databento_live]: read 18 bytes from remote gateway
02:54:28.171Z DEBUG databento.live.protocol [databento_live]: greeting received by remote gateway version='0.9.1'
02:54:28.171Z DEBUG databento.live.protocol [databento_live]: read 38 bytes from remote gateway
02:54:28.171Z DEBUG databento.live.protocol [databento_live]: received CRAM challenge cram='JknuRX7ZL9nqvpMSWLBMW6pW6FBAmSPV'
02:54:28.171Z DEBUG databento.live.protocol [databento_live]: sending CRAM challenge response auth='<redacted>-LUjQT' dataset=GLBX.MDP3 encoding=dbn ts_out=0 compression=none heartbeat_interval_s=30 client='Databento/0.71.0 Python/3.13.1 Windows/11'
02:54:29.479Z DEBUG databento.live.protocol [databento_live]: read 31 bytes from remote gateway
02:54:29.479Z DEBUG databento.live.protocol [databento_live]: CRAM authentication successful
02:54:29.479Z INFO  databento.live.session [databento_live]: authenticated session_id='641353080'
02:54:29.479Z DEBUG databento.live.protocol [MainThread]: sending subscription request schema=trades stype_in=raw_symbol symbols='['NQU6']' start='now' snapshot=False id=0
02:54:29.479Z INFO  databento.live.client  [MainThread]: subscribing to schema=mbp-1 ...
02:54:29.479Z DEBUG databento.live.protocol [MainThread]: sending subscription request schema=mbp-1 ... id=1
02:54:29.479Z INFO  databento.live.client  [MainThread]: subscribing to schema=definition ...
02:54:29.479Z DEBUG databento.live.protocol [MainThread]: sending subscription request schema=definition ... id=2
02:54:29.480Z INFO  databento.live.client  [MainThread]: subscribing to schema=status ...
02:54:29.480Z DEBUG databento.live.protocol [MainThread]: sending subscription request schema=status ... id=3
02:54:29.480Z INFO  databento.live.client  [MainThread]: subscribing to schema=statistics ...
02:54:29.480Z DEBUG databento.live.protocol [MainThread]: sending subscription request schema=statistics ... id=4
02:54:29.480Z INFO  databento.live.client  [MainThread]: starting live client
02:54:29.480Z DEBUG databento.live.protocol [MainThread]: sending start
```

After `sending start`: **nothing, ever.** Over the following 11+ minutes (subscribe 02:54:29 → stop
03:05:50): zero `read N bytes` lines, zero `dispatching <Record>` lines, zero `gateway heartbeat` lines,
zero ERROR lines. The last gateway byte ever received was the 31-byte auth-success at 02:54:29.479Z.

**Reading of the evidence (facts first, then inference):**

*Proven by the capture:*
1. TCP connect, greeting, CRAM auth all succeeded — and every one of those writes/reads executed on the
   SDK's own `databento_live` loop thread.
2. The five subscription requests and the session start were written from **MainThread** — a foreign
   thread to the proactor event loop that owns the gateway transport. SDK code path:
   `Live.subscribe()` → `session.subscribe()` (session.py:519-536) → `protocol.subscribe()` →
   `transport.writelines(...)` (protocol.py:359) directly on the calling thread; `Live.start()` →
   `session.start()` (session.py:473-479) → `protocol.start()` → `transport.write(...)`
   (protocol.py:371), same. By contrast the SDK's `stop()` (session.py:461) and connect (session.py:610)
   correctly marshal onto the loop via `call_soon_threadsafe` / `run_coroutine_threadsafe`. asyncio
   transports are documented not thread-safe; on the Windows proactor loop a foreign-thread
   `write()` races `_loop_writing` buffer handling on the loop thread. The race window here was real:
   all six writes landed within ~1 ms of the auth-completion callback running on the loop thread.
3. The gateway negotiated `heartbeat_interval_s=30` in the CRAM exchange, and per the SDK's own
   docstring (session.py:305-307) the gateway sends heartbeat records **even when no data flows** —
   for a *started* session. NQU6 during an active Globex session would also stream data immediately.
   Zero inbound bytes for 11 min (and 45+ min in the original wedge) with the TCP socket never closing
   is therefore inconsistent with "gateway received start and went quiet".
4. **The SDK's own liveness watchdog is structurally blind to this exact state.**
   `_heartbeat_monitor` (session.py:677-690) computes `gap = loop.time() - _last_msg_loop_time` and
   disconnects+reconnects when the gap exceeds interval+margin — but `_last_msg_loop_time` initializes
   to `math.inf` (session.py:228) and is only ever set in `received_record` (session.py:252). A session
   that never receives its FIRST record has `gap = -inf` forever: the watchdog never fires, so the
   `_reconnect` path (session.py:692-717, which would re-subscribe and re-start) never engages, no
   exception is ever raised, and the client sits ESTABLISHED-and-silent indefinitely. This explains the
   original 45+ minute silent wedge with zero self-healing.
5. TL-side, nothing detects it either: the adapter's `events()` loop idles on 0.25 s queue timeouts
   (databento.py:390), `FeedStatus` stays CONNECTED (set optimistically at :341-351 before any live
   byte arrives), `warm_start_state` flips to "live" only when a post-anchor event arrives
   (live.py:356-364) — which never happens — and no timer anywhere compares "time since last live
   event" to wall clock. The operator surface is green by construction.

*Inference (strong, but not provable from inside the process):* the subscribe/start writes never
reached the wire — lost in the un-synchronized cross-thread hand-off to the proactor transport — so the
gateway holds an authenticated session that was never subscribed/started and (correctly, from its view)
sends nothing. Discriminating this conclusively from a gateway-side fault would need Databento's
server-side session log for session_id **641353080** (and '...' for the original wedge's session) or a
packet capture on a future repro.

*Consistency check with history:* databento 0.71.0 + databento_dbn 0.49.0 were installed 2026-02-23 —
the same SDK build served the June live sessions that demonstrably flowed (the queue-overflow-flood
era). The failure is timing-dependent, not a version regression; what changed is the surrounding
context (post-drain connect after ~20 min of drain at 1 GB RSS vs immediate connect in June). 2-for-2
tonight suggests the current shape lands in the losing side of the race reliably on this box.

### C.5 Controlled stop of the wedged repro (Phase B semantics, step 9 "then stop")

- **22:05:50.322** — `POST /api/v1/live/stop` → **HTTP 200 in 30 ms**. Response verbatim:
  ```json
  {"state":"stopped","requested_symbol":"NQU6","dataset":"GLBX.MDP3","schemas":["trades","mbp-1","definition","status","statistics"],"api_key_configured":true,"enabled":true,"sdk_available":true,"subscription_ready":true,"events_processed":25528682,"last_event_ts_utc":"2026-07-08T02:29:59.896228+00:00","last_error":null,"started_at_utc":"2026-07-08T02:34:18.958436+00:00","stopped_at_utc":"2026-07-08T03:05:50.340657+00:00","warm_start_state":"warming","warm_start_events":25528682}
  ```
  Note `warm_start_state` still reads `"warming"` in the stopped state — the label is never cleared.
- Stop log lines, complete and verbatim — and diagnostic gold in themselves:
  ```
  03:05:50.340Z INFO databento.live.client  [MainThread]:      stopping live client
  03:05:50.341Z INFO databento.live.protocol [databento_live]: connection closed
  ```
  `stop()` goes through the SDK's **thread-safe** path (`call_soon_threadsafe(transport.close)`,
  session.py:461) — and the sleeping `databento_live` loop executed it **within 1 ms**. The loop thread
  was healthy and responsive all along; the only cross-thread operations that coincided with the dead
  session are the unsynchronized subscribe/start writes.
- Flush behavior: no orphan warnings, no drops journaled (no model was activated this run, so
  `flush_resolver` had nothing to finalize). Feed exited cleanly; gateway socket verified GONE at
  22:06:12; uvicorn kept serving; final `/api/v1/status` showed `feed_state:"disconnected"`,
  `live.state:"stopped"`.
- **22:06:32** — instrumented backend (PID 36792) terminated. Nothing left running; the repro run
  wrote **zero journal rows** (both journal files' mtimes predate the run).

### C.6 Verdict + fix-design implications (step 9)

**It wedged again — deterministically (2 for 2), same signature, and it is NOT intermittent-flow.**
The livelock lives at the databento-SDK boundary:

1. **Subscribe fix needed:** `Live.subscribe()`/`Live.start()` calls issued from the uvicorn MainThread
   write directly to a transport owned by the SDK's private loop thread (unsafe per asyncio; racy on
   Windows proactor). Any fix must marshal the post-drain `_connect()` sequence
   (`databento.py:362 → :254-286`) so the writes execute on (or are safely handed to) the SDK's loop —
   or avoid the race window entirely (e.g. connect+subscribe *before* the drain with deferred start, or
   serialize subscribe-after-auth with a delay/ack discipline), or take it upstream to databento-python.
2. **AND a liveness watchdog is required regardless:** both the SDK's own monitor (blind before the
   first record: `_last_msg_loop_time = inf`) and TL (CONNECTED-by-construction FeedStatus, no
   last-live-event-age check) failed to detect 45 minutes of dead feed. TL needs its own
   watchdog — e.g. "if `warm_start_state != live` for > N minutes after drain end, or no live event for
   > M seconds while state=running, surface DEGRADED and/or reconnect" — because even with the subscribe
   race fixed, this class of silent-gateway failure must never be invisible again.
3. Secondary surface bugs worth filing from this capture: `warm_start_state:"warming"` persists into
   the stopped state; `feed_state:"connected"` is asserted before any live byte is observed; the
   deployment has no persistent logging (A.2) — tonight's DEBUG file is the first time the subscribe
   handshake was ever observable on this box.

**Artifacts:** DEBUG log (34 lines): scratchpad `WEDGE_RUN_debug.log` · status polls (20 s cadence,
full drain + wedge): `wedge_status_poll.jsonl` · launcher/poller sources: `wedge_launcher.py`,
`wedge_poller.py` (same scratchpad). Session id for a Databento-side inquiry: **641353080**
(2026-07-08 02:54:28-03:05:50 UTC, GLBX.MDP3, NQU6, key suffix ...LUjQT).
