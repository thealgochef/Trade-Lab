# W3b replay `_drive` livelock — root-cause report

**Symptom.** `python -u -m w3b.run_window --workers 1 --days 2025-12-18,…` wedged on
day 1 (2025-12-18). No report and no per-day journal were emitted. A 30 s sample showed
`read_ops=0`, `other_ops≈109 469`, `cpu≈1.42 cores`; py-spy caught MainThread looping in
`strategy_core/.../replay.py:status → trade_lab/.../replay.py:status → headless_replay.py:_drive`
(~144) `← replay_day_to_journal` (~201); executor threads sat idle in
`concurrent.futures.thread._worker`.

**Verdict: this is a terminalization/liveness bug, not slow progress, not memory/cache/density.**
Owner = the harness `_drive` (livelock), with the underlying defect in the core runtime
`start()` (non-terminal state on `BaseException`). Confirmed by code, by a 9-agent read-only
review (3 adversarial verdicts, all `refuted=false`, high confidence), and by a deterministic
reproduction of the mechanism.

---

## A. Exact failing condition

`backend/scripts/w3b/headless_replay.py:144` (git HEAD):

```python
while replay.status().state not in _TERMINAL_STATES:
    await asyncio.sleep(0)
```

This predicate is **permanently `True`**. `replay.status().state`
(`backend/src/trade_lab/services/replay.py:151-181`) delegates to the **core** runtime's
state whenever core ≠ IDLE (`replay.py:155-157`, via the total map `_map_core_replay_state`
`:412-420`). The core's `_state` is set to `RUNNING` at the top of
`strategy_core/runtime/replay.py:111` and is only advanced to a terminal value **inside**
`ReplayRuntime.start()` — `COMPLETED` at `:171`, `FAILED` at `:175`, `STOPPED` at `:137/:147`.
If `start()` is exited by a `BaseException` (`asyncio.CancelledError`, `KeyboardInterrupt`,
`SystemExit`, `GeneratorExit`), it bypasses `except Exception` (`:174`, which has **no
`finally`**), so `_state` stays `RUNNING`. `status()` then reports `running` forever and the
loop never exits.

## B. Exact owner layer

- **Livelock owner — the harness `_drive`** (`headless_replay.py:140-146`). It decides
  completion *solely* by polling `status()`; it never inspects the worker
  `asyncio.Task` (`HistoricalReplayService._task`, created at `services/replay.py:229`), has
  no timeout/watchdog, and busy-yields with `asyncio.sleep(0)`.
- **Underlying-defect owner — the core runtime `ReplayRuntime.start()`**
  (`strategy_core/runtime/replay.py:109-178`). Its terminalization is not exhaustive: a
  `BaseException` leaves `_state=RUNNING`, stranding every `status()` poller (this harness
  *and* the live API status endpoint).

## C. The code path that should transition to terminal — and doesn't

`strategy_core/runtime/replay.py:174` `except Exception as exc:` is the handler that should
flip `_state` to `FAILED`. It does so for ordinary `Exception`s but **not** for
`BaseException`, and there was no `finally`. So any `BaseException` raised at an `await`/call
in the loop body (`:132 await asyncio.to_thread`, `:160 self._process_item`,
`:169 await self._record_update`, …) escapes with `_state` still `RUNNING`.

Secondary: `services/replay.py:286-339` `_run_strategy_core_replay` mirrors the core state
into the TL `_state` only **after** `await core.start()` returns (`:307-339`) and has no
`try/finally`; and the committed `_drive` never observes `replay._task`.

## D. Why `_drive` spins instead of failing

It trusts `status()` as the only completion signal and has no liveness/timeout. A worker that
has **died** (task done, exception unretrieved) or **stalled** (suspended) with a non-terminal
`status()` is indistinguishable from a slow run. `await asyncio.sleep(0)` is a *bare yield*,
not a sleep, so the loop becomes a hot spin. (Answer to **Q12**: on the Windows
`ProactorEventLoop`, every `sleep(0)` turn polls the IOCP — `GetQueuedCompletionStatus` — which
is an `OtherOperation`, not a read; millions of turns/sec ⇒ `other_ops` flood, `read_ops=0`
because the worker isn't reading, ~1.4 cores burned.) Because the inner call never returns and
never raises, `run_window`'s per-day `except Exception` (`run_window.py:93`) cannot catch it,
the serial `for` loop (`:88-97`) never advances past day 1, and `report()`/`write_text`
(`:238-242`) are never reached — hence **no report and no journal**.

---

## The 12 questions

| # | Answer (with refs) |
|---|---|
| 1 | **Core states** (`strategy_core/runtime/replay.py:19-25`, `StrEnum`): `idle, running, paused, completed, failed, stopped`. **TL states** (`services/replay.py:60-69`, `StrEnum`): `idle, loading, ready, running, paused, completed, failed, stopped, cancelled`. |
| 2 | `_TERMINAL_STATES` = `{COMPLETED, FAILED, STOPPED, CANCELLED}` (TL `ReplayState`), `headless_replay.py:63-68`. |
| 3 | `status().state` is a **TL `ReplayState` (a `StrEnum`)**, defined `services/replay.py:60-69`; returned via `ReplayStatus` (`:88-90`). When core ≠ IDLE it is the **mapped core** state (`:155-157`). |
| 4 | **No mismatch.** Both sides of the `in` test are the same TL `StrEnum`; `_map_core_replay_state` (`:412-420`) is total over all 6 core states. No `DONE/COMPLETED`, no member-vs-`.value`. (All 3 adversarial verdicts confirm.) |
| 5 | The **core `start()`** is the sole writer of the core terminal state (`:171/:175/:137/:147`); TL mirrors it (`:307-339`). The path that finishes **without** writing terminal: a `BaseException` escaping `except Exception` (`core :174`) → `_state` stuck `RUNNING` (`core :111`). |
| 6 | **Yes** — the worker `asyncio.Task` `replay._task` (`services/replay.py:229`). Committed `_drive` ignores it entirely. |
| 7 | Worker exits → committed `_drive` **does not** detect it (status-only). Fixed `_drive` does. |
| 8 | Worker raises → committed `_drive` **does not** surface it; the exception sits unretrieved on `_task` (`"Task exception was never retrieved"`). Fixed `_drive` re-raises `task.exception()`. |
| 9 | EOF should transition in **core `start()`**: the source iterator returns the `_REPLAY_DONE` sentinel (`:55-56`), the loop breaks (`:133-134`), `_state→COMPLETED` (`:171`). Normal EOF is handled correctly; the gap is **non-normal** exit. |
| 10 | In the headless path, no pending condition blocks DONE: `_pause_event` is set, `_on_update` is `None` (no awaiting flush), `to_thread` returns the sentinel at EOF. DONE is blocked **only** when the terminal write is bypassed (BaseException). The post-stream resolver flush (`services/replay.py:297`) runs after the core is already terminal. |
| 11 | **No — 2025-12-18 is not data-special.** `mbp10.parquet` is valid (`PAR1` at head+tail, 1.83 GB), same mbp-10 schema as prior day 12-17, and is actually the **smallest** of its neighbours (12-16 = 2.03 GB, 12-17 = 1.92 GB). **Empirically proven:** an isolated re-run decodes 10 M+ events with zero stall (see G). It wedged "first" only because it is iteration 1 of the serial `--days` list; the original freeze was a **transient `BaseException`** (external/cancellation/resource), not the data. |
| 12 | See **D** — `asyncio.sleep(0)` hot-spin on the Windows IOCP proactor: IOCP polls = `OtherOperation`s, no reads, worker dead/idle ⇒ `other_ops` flood / `read_ops=0` / ~1.4 cores. |

---

## E. Minimal patch

**(1) Harness — the livelock owner.** `backend/scripts/w3b/headless_replay.py` `_drive`
rewritten to **wait on the worker task** (event-driven, no busy-poll), **categorise its exit**,
**assert a terminal state**, and bound a genuine hang with a **no-progress watchdog** (replaces
`asyncio.sleep(0)`). Diagnostic dump is gated behind `W3B_DRIVE_DEBUG`; watchdog window via
`W3B_DRIVE_WATCHDOG_S` (default 120).

```python
await replay.start(source, config)
task = replay._task
while task is not None and not task.done():
    done, _ = await asyncio.wait({task}, timeout=_DRIVE_POLL_SECONDS)   # not sleep(0)
    if done:
        break
    ...   # 5 s debug tick; 120 s no-progress watchdog → cancel + raise ReplayTaskFailed
if task is not None:
    if task.cancelled():
        raise ReplayTaskFailed("replay task was cancelled before reaching a terminal state")
    exc = task.exception()
    if exc is not None:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise exc                       # genuine interrupt → propagate, ABORT the run
        if isinstance(exc, Exception):
            raise exc                       # ordinary failure → catchable → RED + continue
        raise ReplayTaskFailed(             # other non-Exception BaseException (incl.
            f"replay task aborted with {type(exc).__name__}: {exc}"   # CancelledError)
        ) from exc                          # → catchable domain error → RED + continue
state = replay.status().state
if state not in _TERMINAL_STATES:
    raise ReplayTaskFailed(f"replay task completed but state={state!r} is non-terminal")
return state
```

**Exception-handling contract (corrected — the earlier draft overclaimed "RED + continue" for
*all* `BaseException`; that is false because `run_window` only catches `Exception`).**
`ReplayTaskFailed` is a `RuntimeError` (so an `Exception`). The behaviour is:

| What the replay task does | `_drive` raises | `run_window` (`except Exception`, `run_window.py:95`) | Outcome |
|---|---|---|---|
| Ordinary `Exception` (e.g. resolver-flush tail; or error in `replay_day_to_journal`/`_process_day`) | the same `Exception` (raw) | caught | **day RED, report emitted, next day continues** |
| Task cancelled / raised `asyncio.CancelledError` | `ReplayTaskFailed` (wrapped) | caught | **day RED, continues** |
| Watchdog: no progress, non-terminal | `ReplayTaskFailed` | caught | **day RED, continues** |
| Task done but state non-terminal | `ReplayTaskFailed` | caught | **day RED, continues** |
| Other non-`Exception` `BaseException` (e.g. `GeneratorExit`) | `ReplayTaskFailed` (wrapped, cause preserved) | caught | **day RED, continues** |
| `KeyboardInterrupt` / `SystemExit` | the same interrupt (raw) | **not** caught (`run_window.py:91-93` re-raises explicitly) | **run ABORTS; never RED** |

Note: most *in-stream* processing errors never reach `_drive` as a raise at all — the core's
`except Exception` (`strategy_core/runtime/replay.py:174`) already converts them to a `FAILED`
replay state, which surfaces as a normal terminal result (then a diff-based RED), not an
exception. The table above is about exits that escape that handler.

`run_window` was made explicit to match: a `except (KeyboardInterrupt, SystemExit): raise`
guard precedes `except Exception` in both the serial (`run_window.py:91-99`) and pool
(`:113-118`) loops, so an interrupt can never be silently recorded as a RED day even if the
broad clause were ever widened.

**(2) Defense-in-depth — the underlying-defect owner.**
`strategy_core/src/strategy_core/runtime/replay.py` `start()` gets a `finally` **liveness
guard** that records terminal `FAILED` if a `BaseException` bypassed `except Exception`,
**without swallowing** it (a bare `finally` re-raises after running). No-op on every normal
exit. This protects *all* `status()` pollers, including the live API.

```python
finally:
    if self._state in (ReplayState.RUNNING, ReplayState.PAUSED):
        self._state = ReplayState.FAILED
        self._last_error = self._last_error or "aborted"
        self._last_message = self._last_message or "historical replay aborted before terminal state"
        self._failed_at_utc = datetime.now(UTC)
```

## F. Regression tests (all deterministic — no real replay)

**`backend/tests/test_w3b_drive_terminalization.py`** (8 cases, no store) — `_drive` exit
categorisation, each wrapped in `asyncio.wait_for(timeout=5)` so a revert to the busy-poll
**hangs → fails**:
- non-`Exception` `BaseException` worker → wrapped as `ReplayTaskFailed` (cause preserved);
- `asyncio.CancelledError` worker → wrapped as `ReplayTaskFailed` (not raw → not an abort);
- ordinary `Exception` worker → re-raised raw;
- `KeyboardInterrupt` worker → propagated raw; `SystemExit` worker → propagated raw;
- silent non-terminal completion → `ReplayTaskFailed`; clean `COMPLETED` → returns it;
- frozen-progress worker → watchdog raises `ReplayTaskFailed`.

**`backend/tests/test_w3b_run_window_exceptions.py`** (5 cases, no store) — `run_window`'s
per-day contract via a monkeypatched `_process_day` + stubbed `resolve_window`:
- **A** ordinary `Exception` → day RED, report built + written, next day still processed;
- **B** `ReplayTaskFailed` → day RED, continues; **and** raw `asyncio.CancelledError` reaching
  `run_window` → **not** caught → aborts (documents why `_drive` must wrap it);
- **C** `KeyboardInterrupt` → propagates, aborts (never RED);
- **D** `SystemExit` → propagates, aborts (never RED).

**`strategy_core/tests/test_runtime_replay.py::test_replay_baseexception_still_reaches_terminal_failed_state`**:
a `BaseException` leaves the core in terminal `FAILED` (and re-raises).

**Mechanism proof:** the original committed `_drive` (verbatim) run against the `BaseException`
stub **hangs → `TimeoutError`**, emitting the exact original tell `Task exception was never
retrieved`. The fixed `_drive` raises promptly.

## G. Command output

Both runs use the fixed `_drive` with `W3B_DRIVE_DEBUG=1` (5 s progress ticks). The wedged
PID was already dead (only the live `trade_lab.api` server was running), so there was nothing
to kill — the original facts in the prompt are the captured state.

**2025-12-18 repro** (`python -u -m w3b.headless_replay 2025-12-18`, the day that wedged):

```
day=2025-12-18 state=completed events=13,483,063
  seed_pdh_pdl=(25508.5, 24887.75)
  predictions=2 outcomes=2 dropped=0
  cache_present=True journal=.../w3b_journal/2025-12-18/2025-12-18.jsonl
```

- **`state=completed`** — the exact day that wedged now reaches a terminal state under the fix.
- 13,483,063 events, **298 monotonic progress ticks over ~25 min, zero stalls** (events climbed
  steadily from 45 k → 13.48 M; `last_event_ts` advanced through the whole session to the
  ~16:31 ET cutoff). The watchdog never fired and the task never raised.
- ⇒ **2025-12-18 does not deterministically wedge.** The original freeze was a *transient*
  `BaseException`; the old busy-poll `_drive` converted it into a permanent hot-spin.
  (Full tick log: `backend/data/w3b_repro/repro_12-18.log`.)

**2025-12-23 control** (`python -u -m w3b.headless_replay 2025-12-23`):

```
day=2025-12-23 state=completed events=5,358,323
  seed_pdh_pdl=(25794.75, 25628.0)
  predictions=6 outcomes=6 dropped=0
  cache_present=True journal=.../w3b_journal/2025-12-23/2025-12-23.jsonl
```

- **`state=completed`** — a normal day drives cleanly to terminal under the fix (5,358,323
  events, 120 monotonic ticks, ~10 min). (Full tick log: `backend/data/w3b_repro/control_12-23.log`.)

**Mechanism proof — the original `_drive` deterministically hangs:** running the verbatim
committed busy-poll `_drive` against a worker task that raises a `BaseException` (state left
`RUNNING`):

```
Task exception was never retrieved
future: <Task finished ... exception=_Boom()>
RESULT: old_drive HUNG -> TimeoutError (regression test WOULD catch the bug) [OK]
```

The `Task exception was never retrieved` line is the exact original symptom. The fixed `_drive`
re-raises promptly instead.

