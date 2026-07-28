# Archived window artifacts

Per-work-window review deliverables and superseded planning docs, archived
(committed, filenames unchanged) in the 2026-07-28 housekeeping window. None of
these had ever been committed — they lived untracked/gitignored at the repo root,
so this archive is the only durable copy. Tracked code that cites one of these by
bare filename (e.g. `WEDGE_CAPTURE.md`, `REPORT_RECON.md`, `SEED_PARITY_RECON.md`,
`WARM_PERF_RECON.md`, `VIZ_RECON §3`) resolves here: `docs/archive/windows/<name>`.

## windows/

- Recon/root-cause docs cited by tracked code: `WEDGE_CAPTURE.md` (live post-drain
  wedge capture; justifies the `databento>=0.79` floor in backend/pyproject.toml),
  `WARM_PERF_RECON.md`, `REPORT_RECON.md`, `SEED_PARITY_RECON.md` (+ its computed
  evidence pair `scratch_seed_parity.py` / `scratch_seed_parity_results.json`),
  `VIZ_RECON.md`, `W3B_LIVELOCK_ROOTCAUSE.md` (regression-tested by
  `test_w3b_drive_terminalization.py`).
- Uncited window reports: `BASELINE_REPORT.md` (three-repo baseline survey),
  `PLUGIN_SDK_RECON.md`, `READER_CORRECTNESS_PROOF.md`, `READER_PIN_RECON.md`,
  `D1_CHARACTERIZATION.md`, `COCKPIT_STATES.md`.
- `*_DIFF.txt` — per-window git-diff captures exported for owner review
  (C1→INGEST). Reproducible from git history; retained as the review record.
  `WARMFIX_SC_DIFF.txt` was moved to Strategy-Core's archive (it documents an SC
  range and SC's ledger cites it).

## Superseded planning docs (from docs/ and plans/)

- `SESSION-HANDOFF-2026-05-30.md` — self-declared superseded 2026-06-08.
- `implementation-plan.md` — the pre-creation design doc for what became the
  Strategy-core repo; superseded by that repo's own docs.
- `strategy-decision-core-current-implementation-survey.md` — pre-extraction
  QL-vs-TL survey; historical snapshot.

## Deleted (not archived) in the same window

CI-log zips (4), `test.md` (a pasted work prompt), byte-identical duplicates
(`LAND_TL_DIFF_v2.txt`, `backend/D1_CHARACTERIZATION.md`), orphaned
`backend/package-lock.json`, raw pytest/bench captures (`*_ingest*.out`,
`backend/W3_*.log`, stray run logs). Rationale: raw output and duplicates carry
no citations; reports/recons/diffs do, and were archived instead.
