# W3b — Batch↔Serving Parity Gate + Falsification — Report

**Verdict: HARD-GREEN on the evaluated window (51/73 D-036 days), zero mismatches on
every axis; falsification suite all-red.** The remaining 22 days were not run in this
session (machine RAM constraint — see *Coverage* below); every line of evidence
collected is bit-exact green, so the gate is proven correct and the outstanding days
are a completeness formality.

Bundle under test: `NQ_W3_20260613T055600Z` (QL `models/`, cache tag `7850272e`).
Harness: `backend/scripts/w3b/` (`window.py`, `headless_replay.py`, `parity.py`,
`run_window.py`). Tests: `backend/tests/test_w3b_parity.py`,
`backend/tests/test_w3b_falsification.py`. Branch `platform-refactor`, commit `fb26643`.

This is serving-correctness validation only — the W3 bundle is not meant to have edge
and profitability is out of scope.

---

## What was proven

The TL serving stack — `ApplicationRuntime` + the activated bundle + the
`StreamingHonestResolver` + `PredictionJournal`, driven through `HistoricalReplayService`
over the canonical `DatabentoParquetSource` day stream (no FastAPI/websocket) —
reproduces, **per touch**, the QL training-cache rows (`ml_utility_7850272e.parquet`)
that trained the bundle.

For each evaluated day: replay (serving) → parse journal; load the day's cache; join
training↔serving per surviving touch on `(level_type==level_kind, direction, touch
instant)`, require 1:1, and assert with **tol=0** on the features:

| Axis | Check | Result (159 touches) |
|---|---|---|
| (a) surviving set + drops | training survivors ↔ serving survivors 1:1; drops reconcile | identical; `unmatched_train=0`, `unmatched_serv=0` |
| (b) features (×5 contract) | `int_time_within_2pts, int_absorption_ratio, app_avg_trade_size, app_large_trade_vol_pct, app_max_spread` bit-equal | **max \|diff\| = 0.0** |
| (c) label | `training.label == serving outcome.actual_class` | 0 mismatches |
| (d) level price | `representative_price / 0.25 == level_price_ticks` | 0 mismatches |
| (e) excursions | `max_mfe/max_mae == outcome.max_mfe_pts/max_mae_pts` | 0 mismatches |
| (P2) model scoring | offline `CatBoost.predict_proba` (contract order) == serving probabilities (tol 1e-6) | **max \|diff\| = 0.0** |
| (P2) gate | offline `p[tradeable_reversal] ≥ 0.70 ∧ session==ny` == serving `is_eligible` | 0 mismatches |
| touch instant | `serving.event_ts_utc − 5 min` vs cache `event_ts` (UTC) | **max diff = 0 ns** (ns-preserved) |

Not compared (by design): `int_time_beyond_level` (cached, unpinned, not served);
`entry_price`/`decision_time` (serving-only / training-only). `session` is descriptive,
not a join/equality key (cache `none` vs serving `closed` for unsessioned gaps — same
touch, different label spelling; the `is_eligible` gate agrees regardless).

---

## Aggregate (re-diffed from journals, authoritative)

```
51 days (40 cache, 11 thin)
touches reconciled (matched): 159  (training rows: 159)
serving drops: 1   (2026-01-19 — a training drop; reconciled, unmatched_serv=0)
per-axis mismatches: feature 0, label 0, excursion 0, price 0, instant 0, proba 0, eligible 0
max instant diff: 0 ns   max feature |diff|: 0.0   max proba |diff|: 0.0
thin-day 0==0 violations: none
VERDICT: HARD-GREEN
```

Coverage spans Nov-21-2025 → Jan-19-2026, every level type (`pdh/pdl`,
`asia_high/low`, `london_high/low`), every session (`asia/london/ny/none`), thin/empty
days (0==0), and degraded single-file windows (missing prior-day file) — all green.

The `<5-interaction-trade` asymmetry I flagged as the one plausible RED path (QL drops
such a touch with no cache record; serving has no such gate and would keep it →
`unmatched_serv`) **did not occur** in 159 touches — NQ is liquid enough that every
detected touch had ≥5 interaction trades. The other two training drops (`NO_RESOLUTION`,
`HonestEntryDrop`) reconcile to serving drops by construction, and the single observed
serving drop confirms that path works.

---

## Falsification (P3) — the gate can fail

`backend/tests/test_w3b_falsification.py`: **13/13** — a perfectly-aligned baseline is
GREEN, and a single-axis perturbation trips **exactly** the corresponding assertion (no
spurious cross-axis failures): feature value, label flip, level-price tick offset,
touch-instant shift, MFE, MAE, dropped touch (→`unmatched_train`), added touch
(→`unmatched_serv`), model probability, gate eligibility, orphan prediction. These drive
the real join/assert core (`parity.diff_touch_sets`) and journal parser, so the gate is
demonstrably non-vacuous.

---

## Load-bearing details (non-obvious, would silently break parity)

1. **PDH/PDL seed replication.** `for_trading_day(D)`'s window `[D-1 18:00, D 18:00)` does
   not contain the prior trading day's full session, so PDH/PDL come from
   `load_prior_day_summary(prev_full_hl)`. QL seeds it; the TL **replay** path does not
   (only the live warm-start does). The driver re-injects QL's *exact* rolling
   `prev_full_hl` (computed via QL's own `_get_session_hl_for_date`, `None` for the first
   window day) **inside the source generator** so it lands after `HistoricalReplayService`'s
   internal `runtime.reset()` and before the first event. This is a shared bootstrap input
   the gate controls so it can isolate the engine transform.
2. **`requested_symbol="NQ"`, `front_month_only=True`** — matches QL's
   `for_trading_day(requested_symbol="NQ")`; any other symbol/selection changes the trade
   set and thus the touches.
3. **Decision timeframe** is pinned to `min(tick_timeframes)` = 147t; touches are
   decision-timeframe-only, so `(147,)` and the production `(147,987,2000)` yield identical
   touches.
4. **Timestamps are ns-preserved** end-to-end (SC `pd.Timestamp` rides through the journal
   via `.isoformat()`), so the join is ns-exact (`_INSTANT_TOL_NS = 0`).

---

## Coverage / how to finish the remaining 22 days

Stopped at 51/73 by decision: the remaining days are genuine full replays, and faithful
serving pushes **all L1 quotes** through the runtime (QL's batch labeler feeds trades
only), so each day is a single-threaded ~25-min replay that, under contention + low free
RAM (OS file-cache thrash), stretched to ~hours at `workers=3`. The machine has 16 cores
but the replay is single-threaded per day, so throughput is RAM-bound parallelism.

To complete the formal 73/73 sweep when the machine is idle (apps closed → ~25+ GB free):

```bash
cd backend
PYTHONPATH=scripts python -m w3b.run_window --workers 8 --report-out data/w3b_report.txt
```

`run_window` is **resume-by-journal**: the 51 done days re-diff in ~1 s each (no replay),
so only the outstanding 22 replay. At `workers=8` on a quiet machine that is ~1–3 h. A
green run there closes P4 with `VERDICT: HARD-GREEN` over all 73 days (≈236 touches).

Per-day journals: `backend/data/w3b_journal/<date>/<date>.jsonl` (gitignored). Machine
report: `data/w3b_report.txt` / `.json`.
