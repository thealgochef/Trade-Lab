"""W3b P4 — full-window batch<->serving parity gate + report.

Replays every D-036 day through the activated bundle, diffs the serving journal
against the QL training cache per touch, and reports a hard-green / red verdict
over the whole 73-day window:

  * cache days   -> serving must reproduce every cached touch bit-for-bit;
  * thin days    -> no cache AND serving yields no surviving touch (0 == 0);
  * serving-only survivors are reconciled (or flagged) via QL's drop rules.

Serial by default; ``--workers N`` runs per-day replays in a process pool (each
day is event-decode-bound at ~3-5GB RSS, so keep N modest — the cache warmer used
N=4). Per-day journals are written under ``--journal-base`` (one subdir per day),
cleared per run so stale append-only records cannot poison the join.

  python -m w3b.run_window                      # full window, serial
  python -m w3b.run_window --workers 4          # full window, 4-way
  python -m w3b.run_window --days 2026-02-13    # one day (smoke)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from w3b.parity import DayDiff, OfflineScorer, diff_day  # noqa: E402
from w3b.window import resolve_window  # noqa: E402

_DEFAULT_JOURNAL_BASE = Path(__file__).resolve().parents[2] / "data" / "w3b_journal"


def _diff_one_day(date_str: str, journal_base_str: str, score: bool) -> DayDiff:
    """Worker: replay one day -> journal, diff vs cache. Picklable, spawn-safe."""

    # Heavy imports inside the worker so spawned children don't pay them at import.
    from w3b.headless_replay import replay_day_to_journal

    window = resolve_window()
    journal_dir = Path(journal_base_str) / date_str
    replay_day_to_journal(date_str, journal_dir=journal_dir, window=window)
    scorer = OfflineScorer.for_bundle(window) if score else None
    return diff_day(date_str, journal_dir / f"{date_str}.jsonl", window=window, scorer=scorer)


def run_window(
    days: list[str] | None = None,
    *,
    journal_base: Path = _DEFAULT_JOURNAL_BASE,
    workers: int = 1,
    score: bool = True,
) -> list[DayDiff]:
    window = resolve_window()
    targets = days or list(window.window_dates)
    journal_base.mkdir(parents=True, exist_ok=True)
    results: list[DayDiff] = []

    if workers <= 1:
        scorer = OfflineScorer.for_bundle(window) if score else None
        for i, day in enumerate(targets, 1):
            t0 = time.perf_counter()
            try:
                from w3b.headless_replay import replay_day_to_journal

                journal_dir = journal_base / day
                replay_day_to_journal(day, journal_dir=journal_dir, window=window)
                diff = diff_day(day, journal_dir / f"{day}.jsonl", window=window, scorer=scorer)
            except Exception as exc:  # one bad day must not kill a multi-hour run
                diff = _failed_diff(day, exc)
            results.append(diff)
            elapsed = time.perf_counter() - t0
            print(f"[{i}/{len(targets)} {elapsed:.0f}s] {diff.summary()}", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers, max_tasks_per_child=4) as ex:
            futs = {
                ex.submit(_diff_one_day, day, str(journal_base), score): day for day in targets
            }
            for done, fut in enumerate(as_completed(futs), 1):
                day = futs[fut]
                try:
                    diff = fut.result()
                except Exception as exc:
                    diff = _failed_diff(day, exc)
                results.append(diff)
                print(f"[{done}/{len(targets)}] {diff.summary()}", flush=True)

    results.sort(key=lambda d: d.day)
    return results


def _failed_diff(day: str, exc: Exception) -> DayDiff:
    """A day whose replay/diff raised — recorded as RED with the error, never green."""
    diff = DayDiff(day=day)
    diff.notes.append(f"RUN ERROR: {type(exc).__name__}: {exc}")
    diff.duplicate_keys.append(f"run-error:{day}")  # forces green=False
    return diff


def report(results: list[DayDiff], window) -> tuple[str, bool]:
    """Build the window report + overall green verdict."""

    lines: list[str] = []
    axes = {
        "feature": "feature_mismatches",
        "label": "label_mismatches",
        "excursion": "excursion_mismatches",
        "price": "price_mismatches",
        "instant": "instant_mismatches",
        "proba": "proba_mismatches",
        "eligible": "eligible_mismatches",
    }
    total_matched = sum(d.matched for d in results)
    total_train = sum(d.training_touches for d in results)
    total_drops = sum(d.serving_drops for d in results)
    cache_days = [d for d in results if d.cache_present]
    thin_days = [d for d in results if not d.cache_present]
    axis_counts = {name: sum(len(getattr(d, attr)) for d in results) for name, attr in axes.items()}
    unmatched_train = [(d.day, d.unmatched_training) for d in results if d.unmatched_training]
    unmatched_serv = [(d.day, d.unmatched_serving) for d in results if d.unmatched_serving]
    dup = [(d.day, d.duplicate_keys) for d in results if d.duplicate_keys]
    orphans = [(d.day, d.orphan_predictions) for d in results if d.orphan_predictions]
    # thin-day 0==0: a day without a cache must yield no surviving touch
    thin_violations = [d.day for d in thin_days if d.serving_survivors > 0]
    max_inst = max((d.max_instant_diff_ns for d in results), default=0)
    max_feat = max((d.max_feature_abs_diff for d in results), default=0.0)
    max_proba = max((d.max_proba_abs_diff for d in results), default=0.0)

    green = (
        all(d.green for d in results)
        and not unmatched_train
        and not unmatched_serv
        and not dup
        and not orphans
        and not thin_violations
    )

    lines.append("=" * 78)
    lines.append(f"W3b BATCH<->SERVING PARITY GATE — {len(results)} days "
                 f"({len(cache_days)} cache, {len(thin_days)} thin)")
    lines.append("=" * 78)
    lines.append(f"touches reconciled (matched): {total_matched}  (training rows: {total_train})")
    lines.append(f"serving drops: {total_drops}")
    lines.append(f"per-axis mismatches: {axis_counts}")
    lines.append(f"max instant diff (ns): {max_inst}  max feature |diff|: {max_feat:g}  "
                 f"max proba |diff|: {max_proba:g}")
    lines.append(f"thin-day 0==0 violations: {thin_violations or 'none'}")
    if unmatched_train:
        lines.append(f"UNMATCHED TRAINING (cache row, no serving survivor): {unmatched_train}")
    if unmatched_serv:
        lines.append(f"UNMATCHED SERVING (survivor, no cache row): {unmatched_serv}")
    if dup:
        lines.append(f"DUPLICATE KEYS: {dup}")
    if orphans:
        lines.append(f"ORPHAN PREDICTIONS: {orphans}")
    # Per-day red detail
    reds = [d for d in results if not d.green]
    if reds:
        lines.append("-" * 78)
        lines.append("RED DAYS:")
        for d in reds:
            lines.append("  " + d.summary())
            for attr in axes.values():
                for m in getattr(d, attr):
                    lines.append(f"      {m.key} {m.axis}: {m.detail}")
    lines.append("=" * 78)
    lines.append(f"VERDICT: {'HARD-GREEN' if green else 'RED'}")
    lines.append("=" * 78)
    return "\n".join(lines), green


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", help="comma-separated in-window day subset")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--journal-base", type=Path, default=_DEFAULT_JOURNAL_BASE)
    parser.add_argument("--no-score", action="store_true", help="skip P2 offline scoring")
    parser.add_argument("--report-out", type=Path, default=None)
    args = parser.parse_args()

    window = resolve_window()
    if args.days:
        days = [d.strip() for d in args.days.split(",") if d.strip()]
        unknown = [d for d in days if d not in window.window_dates]
        if unknown:
            raise SystemExit(f"--days not in D-036 window: {unknown}")
    else:
        days = list(window.window_dates)

    t0 = time.perf_counter()
    results = run_window(
        days, journal_base=args.journal_base, workers=args.workers, score=not args.no_score
    )
    text, green = report(results, window)
    text += f"\nwall: {(time.perf_counter()-t0)/60:.1f} min  (pid {os.getpid()})"
    print(text, flush=True)
    if args.report_out:
        args.report_out.write_text(text + "\n", encoding="utf-8")
    # Persist per-day diffs as JSON for inspection.
    if args.report_out:
        import json

        json_out = args.report_out.with_suffix(".json")
        json_out.write_text(
            json.dumps([asdict(d) for d in results], indent=2, default=str), encoding="utf-8"
        )
    return 0 if green else 1


if __name__ == "__main__":
    raise SystemExit(_main())
