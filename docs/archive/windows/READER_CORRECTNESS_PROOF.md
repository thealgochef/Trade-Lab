# Reader Correctness Proof — vectorized `DatabentoParquetSource` @ strategy-core `37359ae`

**Question.** Does the vectorized Strategy-Core MBP-10 reader (SC repo, commit `37359ae`,
`src/strategy_core/data/databento_parquet.py`) faithfully decode the raw NQ MBP-10 parquet
bytes into the correct neutral `(Trade, Quote)` event stream — to a standard fit to serve
real money? This is a **correctness proof against the raw parquet**, not a consistency check
against any other reader.

---

## VERDICT

> ## ✅ VECTORIZED READER CORRECT
>
> The vectorized reader at `37359ae` decodes the raw NQ MBP-10 store **byte-faithfully** and
> **completely**. Across three trading days spanning dense / thin / session-boundary
> conditions, every sampled event traces back to its exact raw row with matching
> price / size / side / nanosecond-timestamp, and every bounded raw-row window — up to and
> including a **full trading day of 1,968,223 events** — produces **exactly** the reader's
> emitted events: right count, right canonical order, every field identical, no dropped rows,
> no spurious extras. No divergence was found.

No part of the audit produced a wrong-valued event or a missing/extra event. Therefore there
is no "first divergence" to report.

---

## 1. Subject, isolation, and method

### 1.1 The reader under test (isolation proof)
The constraint is to test the **vectorized** reader at `37359ae`, **not** the old 583-line
reader installed in site-packages.

| | path | lines |
|---|---|---|
| **Under test** (isolated worktree @ `37359ae`) | `C:\Users\gonza\Documents\sc_37359ae\src\strategy_core\data\databento_parquet.py` | **1148** |
| site-packages (the OLD reader — explicitly avoided) | `…\Python313\Lib\site-packages\strategy_core\data\databento_parquet.py` | 583 |

`37359ae` ("W3A-READER P3c — delete the row-wise reader path on full green") is an ancestor of
SC `HEAD` (`4b08ba1`); the reader file is byte-identical at `37359ae` and `HEAD` (only docs
changed after). The worktree was created read-only with `git worktree add --detach
C:\Users\gonza\Documents\sc_37359ae 37359ae`. **Every** harness run asserts and prints the
loaded module path, e.g.:

```
reader module: C:\Users\gonza\Documents\sc_37359ae\src\strategy_core\data\databento_parquet.py
```

`sys.path` is prepended with the worktree `src`, so the 1148-line vectorized reader wins over
site-packages in every run (verified).

### 1.2 What "ground truth" means here
Ground truth is reconstructed **directly from the raw MBP-10 bytes** with an independent
`pyarrow` decoder (not the reader):
- raw `ts_event` int64 nanoseconds → event timestamp;
- raw `price` (float dollars) → ticks via `price / 0.25`;
- raw `action ∈ {T,TRADE}` → Trade; any front-month row whose top-of-book (`bid_px_00`,
  `ask_px_00`, `bid_sz_00`, `ask_sz_00`) is **both** grid-valid → Quote candidate;
- front-month = dominant non-spread `instrument_id` by **trade-row count** (ties → larger id);
- window `[prev-day 18:00 ET, day 18:00 ET)`, DST-aware;
- canonical order = the engine's **pure sort spec** `strategy_core.data.ordering`
  (`(ts, sequence, side_signed_price, size)`, B=+price / A=−price) — a sort *function*, not a
  reader; it is the order the reader is *required* to produce;
- **L1 top-of-book dedup**: a Quote is emitted only when level-0 state changes from the
  previously-emitted quote.

The reader's output is then diffed against this raw-derived stream **event-by-event**. Any
dropped row, spurious event, mis-valued field, or mis-order would surface as a mismatch.

### 1.3 Raw byte semantics (confirmed against the store)
For every audited day the raw schema is:

| column | arrow type | meaning |
|---|---|---|
| `ts_event` | `timestamp[ns, tz=UTC]` | **genuine ns precision** (e.g. `…998242721`, `ns%1000` nonzero) |
| `price`, `bid_px_00`, `ask_px_00` | `double` | **dollars, 0.25-aligned** (e.g. `25509.25`) — *not* nanos |
| `size`, `bid_sz_00`, `ask_sz_00` | integer | sizes |
| `action` | string | `∈ {A,C,M,T,R}`; only `T` is a trade |
| `side` | string | `∈ {A,B,N}` |
| `instrument_id` / `symbol` | int / string | multi-instrument files (front + back month + spreads) |

So the reader's `price / 0.25` decode and its ns-preserving `ts_event` path are the correct
operations for this data.

### 1.4 Constraint compliance (read-only)
- **No** reinstall, bundle rebuild, push, pin bump, env mutation, or commit.
- Reader read from an isolated detached `git worktree` of `37359ae` (a checkout I added; the
  SC repo's tracked content / HEAD were untouched).
- Audit tooling was a **throwaway** harness in a temp dir outside all tracked repos, used only
  to read raw parquet and the reader, emitting to stdout; it is removed after this report.
- The **only deliverable write is this file** (`READER_CORRECTNESS_PROOF.md`, untracked, repo
  root).

---

## 2. Requirement 1 — SC reader test suite at `37359ae`

Run with `PYTHONPATH` pointing at the isolated worktree `src` (import path asserted above).

**What the tests assert (interpretation):** these are **golden-value fixture tests**, not
smoke. They write tiny synthetic parquet files and assert *exact* decoded values and behavior:
- exact `price_ticks` (e.g. `17000.0 → 68000`, `170000 → 680000`), trade/quote counts;
- MBP-10 trade rows emit a Trade **and** a Quote; quotes don't increment bars;
- L1 TOB dedup emits a quote only on a level-0 change (price *or* size);
- trading-day two-file composition: window inclusion/exclusion at `[18:00ET, 18:00ET)`,
  UTC-midnight partition, dedup of rows duplicated across the split, DST transition;
- side-signed deterministic tie-break (buy ascending / sell descending);
- bytes-typed `action`/`side`/`symbol` cells decode; naive (tz-less) timestamps rejected with
  `INVALID_TIMESTAMP`; missing-prior-day warns and serves a single file;
- front-month dominance by **trade-row count** with ties → larger instrument id.

They prove the decode **logic** on known inputs but use only synthetic data — which is exactly
why §3 audits the real store.

| suite | result |
|---|---|
| Reader subset — `test_databento_parquet_source.py` (6), `test_databento_parquet_day_mode.py` (9), `test_databento_live_source.py` (6), `test_data_ordering.py` (2) | **23 passed, 0 failed, 0 skipped** |
| Full `tests/` directory | **178 passed, 0 failed, 0 skipped** (0.85s) |

All tests pass against the `37359ae` vectorized reader.

---

## 3. Requirement 2 — direct raw-data audit (3 days)

Days chosen to span conditions:

| role | day | prev file | rows (day file) | front-month |
|---|---|---|---|---|
| **dense** (and the W3b suspected-divergence day) | 2026-02-12 | 2026-02-11 | 26,595,110 | `42002475` (NQH6) |
| **thin** (post-Thanksgiving half-day, 13:15 ET close) | 2025-11-28 | 2025-11-27 | 3,368,272 | `158704` (NQZ5) |
| **session boundary** (Monday; prev = Sunday Globex reopen; weekend gap) | 2026-02-09 | 2026-02-08 (Sun) | 19,008,955 | `42002475` (NQH6) |

Front-month action mix (front-month, in-window) confirms **`T` is the only trade-type code**
(no `F`/other), so the reader's `action ∈ {T,TRADE}` rule misses no trades:

| day | T (trades) | A | C | M | R |
|---|---|---|---|---|---|
| 2026-02-12 | 445,777 | 9,910,312 | 9,841,425 | 2,975,535 | 0 |
| 2025-11-28 | 92,590 | 1,207,261 | 1,177,790 | 282,101 | 1 |
| 2026-02-09 | 331,620 | 6,865,698 | 6,767,153 | 2,081,494 | 0 |

### 3a. FIDELITY — sampled events traced to exact raw rows
For each day the reader's **first** event, **last** event, a **mid-session** sample (RTH open,
trades + quotes), and events **bracketing the session seam** (the UTC-midnight file split)
were each traced to the exact raw MBP-10 row and checked field-by-field
(`price_ticks`, `price$ == ticks×0.25`, `size`, `side`, `bid/ask ticks & sizes`, `ts_ns` vs
raw int64). **Every check passed.** Representative traces:

**2026-02-12 — day first event** (head of reader stream):
```
EVENT ('T', ts=2026-02-11 23:00:00.000000000Z, 101156t, sz=10, side='N')
RAW  2026-02-11#402464: action=T side=N price=25289.0 size=10 inst=42002475(NQH6)
  OK price_ticks 101156 == 25289.0/0.25     OK price$ 25289.0 == 101156×0.25
  OK size 10        OK side 'N'        OK ts_ns 1770850800000000000 == raw int64
reader-first ts == true-first front-month row ts ? True
```

**2026-02-12 — session seam** (UTC-midnight 02-11→02-12 file split; continuity, no gap/dup):
```
last BEFORE split: ('Q', 2026-02-11 23:59:59.998607349Z, 100937/100941, 1/2)  RAW 2026-02-11#609658 (bid 25234.25 / ask 25235.25)  ALL FIELDS OK
first AFTER  split: ('Q', 2026-02-12 00:00:00.000017767Z, 100936/100941, 1/1)  RAW 2026-02-12#5      (bid 25234.00 / ask 25235.25)  ALL FIELDS OK
```

**2025-11-28 — day last event** (13:15 ET early-close confirms the thin half-day):
```
EVENT ('Q', 2025-11-28 18:15:00.068071121Z, 101918/101940, 1/3)
RAW  2025-11-28#222543: action=C bid=25479.5 ask=25485.0 bidsz=1 asksz=3 inst=158704(NQZ5)  ALL FIELDS OK
reader-last ts == true-last front-month row ts ? True
```

**2026-02-09 — day first event** (Sunday 18:00 ET Globex reopen, at the window start):
```
EVENT ('T', 2026-02-08 23:00:00.000000000Z, 100615t, sz=16, side='N')
RAW  2026-02-08#114: action=T side=N price=25153.75 size=16 inst=42002475(NQH6)  ALL FIELDS OK
```

(Full mid-session trade+quote traces for all three days were produced and all passed; the
`ts_ns` decoded value equals the raw int64 nanoseconds in every case — ns precision is
preserved, not µs-truncated.)

### 3b. COMPLETENESS — bounded raw-row windows, exact row→event correspondence
Each window decodes the reader and the raw-derived ground truth over the **identical** raw rows
and diffs event-by-event. "EXACT MATCH" = same count, same canonical order, every field equal,
no dropped rows, no spurious extras. The reader's emitted-ts is also verified **monotonic
non-decreasing** in every window.

| day | window | reader events | ground-truth events | result |
|---|---|---|---|---|
| **2025-11-28** | **FULL TRADING DAY** `[18:00ET, 18:00ET)` | **1,968,223** (92,590 T / 1,875,633 Q) | **1,968,223** (92,590 T / 1,875,633 Q) | **EXACT** |
| 2025-11-28 | early `[start, +180s)` | 7,149 | 7,149 | EXACT |
| 2025-11-28 | file seam `[split±120s)` (375 before / 1,180 after) | 1,555 | 1,555 | EXACT |
| 2026-02-09 | early `[start, +180s)` | 13,275 | 13,275 | EXACT |
| 2026-02-09 | file seam `[split±120s)` (7,591 / 28,395) | 35,986 | 35,986 | EXACT |
| 2026-02-09 | end window `[end−15m, end)` | 6 | 6 | EXACT |
| 2026-02-12 | early `[start, +180s)` | 9,629 | 9,629 | EXACT |
| 2026-02-12 | file seam `[split±120s)` (3,553 / 9,785) | 13,338 | 13,338 | EXACT |
| 2026-02-12 | end window `[end−15m, end)` | 10 | 10 | EXACT |
| 2026-02-12 | **W3b disputed PDL touch** `[10:52, 10:57) ET` | **159,256** | **159,256** | **EXACT** |

The **full-day** result on 2025-11-28 closes any "bounded-windows-only" gap: nearly two million
events match exactly, and the emitted trade count (92,590) equals the raw `T`-action count
(92,590) — so **every** trade row produced exactly one Trade, none dropped or invented.

**Explicit row→event enumeration (the "raw rows beside reader events" artifact).** For a 120 ms
window on 2025-11-28 `[14:30:00.000, 14:30:00.120)`, all **230** candidate items derived from
the raw front-month rows were listed in canonical order and annotated; the reader emitted
**exactly 137** events, **1:1 identical** to ground truth. The enumeration shows, per raw row:
- `action=T` rows emit a Trade **and** their book snapshot becomes a Quote candidate
  (dedup-suppressed when the TOB is unchanged) — e.g. row `171769` (T): Trade emitted, its
  quote-item suppressed;
- quote rows emit only on a level-0 change (`TOB CHANGED → EMIT`) and are dropped otherwise
  (`TOB unchanged → DEDUP-SUPPRESS`);
- the canonical tie-break is faithful: at equal `(ts, sequence)`, sell trades (side `A`,
  negative side-signed price) and quotes order ahead of buy trades (side `B`); the
  trade-before-quote insertion order breaks remaining ties — all reproduced exactly.

### 3c. Ordering correctness
The store's parquet files are **not globally ts-sorted at the row level** (interleaved
multi-instrument blocks), which makes ordering a real risk for a per-batch-sorting reader.
Verified that the front-month row groups are themselves ts-ordered (e.g. 2026-02-09 day file
`rg13 < rg14 < … < rg18`, ranges strictly increasing), so the reader's per-batch sort + in-file
batch order coincides with the **global** canonical order. This was confirmed empirically: the
reader's emitted timestamps are monotonic non-decreasing in every window above, and every
window (incl. the full day) matches the **globally** canonical-sorted ground truth exactly.

---

## 4. Note on the W3b 2026-02-12 finding
The W3b parity gate had flagged 2026-02-12: the vectorized reader's stream yields one extra
`pdl|long` first-touch (10:54 ET) absent from a frozen, **row-wise-built** cache, with the
hypothesis "reader-vectorization drift." This proof settles the reader question independently
of the row-wise reader: the **vectorized reader decodes 2026-02-12 correctly vs the raw bytes**
— first/last/mid/seam fidelity all pass, the early and seam completeness windows are exact, and
the **159,256-event window straddling the disputed 10:54 ET touch is an EXACT MATCH** with
ground truth (the market genuinely traded down through the PDL level 25058.25 →
25058.75/25058.5 prints, faithfully decoded). Conclusion: the extra touch arises from a
**correct** event stream; it is **not** a reader-decode error. (Consistent with W3b's own
"stale cache" root-cause: the frozen row-wise cache is the divergent artifact, not the
vectorized reader's decode.)

---

## 5. Observations (non-blocking — do not affect the verdict)
- `strategy_core/types.py`'s `Trade.side` docstring comment reads `'A' = buy/ask-lift,
  'B' = sell/bid-hit`, which is **backwards** relative to the verified/ratified convention
  (`BUY_AGGRESSOR_SIDE = 'B'`; `ordering.py` and the reader both treat `B` as buy). This is a
  stale **documentation comment**, not a decode behavior: the reader passes the raw `side` byte
  through uppercased, which the audit confirmed is byte-faithful (`side` decoded == raw in every
  trace). Side affects only canonical *ordering*, which uses `BUY_AGGRESSOR_SIDE='B'`
  consistently. No correctness impact.
- Rows with a one-sided book (a null/NaN `bid_px_00` or `ask_px_00`) correctly emit **no**
  quote (the reader raises an `INVALID_PRICE` data-quality warning instead); ground truth
  agrees. These warnings are not events and do not affect the event stream (0 warnings on the
  full 2025-11-28 day, which has two-sided books throughout the session).

---

## 6. Scope & honesty
- Completeness was proven over: one **entire trading day** (2025-11-28, 1.97M events) plus
  bounded windows on all three days totaling **~245k** additional events, deliberately
  including the busiest region (RTH open) and the W3b-disputed touch. Fidelity was traced for
  day-first, day-last, mid-session, and session-seam events on all three days. The other two
  days (2026-02-12, 2026-02-09) were audited by representative windows rather than full
  event-by-event replay (each full day is tens of millions of rows); their row groups are
  ts-ordered and every audited window is exact, so the bounded coverage is representative.
- The proof is a decode-fidelity proof of `DatabentoParquetSource` (parquet replay path). The
  separate live `DatabentoLiveSource` is covered only by its unit tests (§2), not by this
  raw-bytes audit.

---

### Final verdict line

> **VECTORIZED READER CORRECT** — backed by: 23/23 reader (178/178 full) golden-fixture tests
> green against the isolated `37359ae` reader; byte-faithful fidelity traces (price/size/side/
> ns-timestamp/bid/ask) for first, last, mid-session, and session-seam events across dense
> (2026-02-12), thin (2025-11-28), and session-boundary (2026-02-09) days; and exact
> row→event completeness over a full 1,968,223-event trading day plus bounded windows
> (including the 159,256-event W3b-disputed-touch window) — no dropped rows, no spurious
> extras, no mis-valued or mis-ordered events.
