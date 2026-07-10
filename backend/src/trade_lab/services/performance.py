"""Read-only performance aggregation over the prediction journal (REPORT P2).

Pure module: stdlib + ``trade_lab.domain.trading_day`` (the writer's own 18:00 ET
clock) + a lazy pyarrow import for the optional OOS parquet. No engine, runtime,
registry, or strategy_core imports — this file must stay importable and correct
with nothing running.

Dual consumer: the trader's Performance page and the D-P-12 soak adjudication
artifact (one implementation, two readers).

Honesty rules (REPORT_RECON.md):

- Only ``tp_hit``/``sl_hit`` outcomes exist (D1b retired force-labels) and only
  they are priced: ``tp_hit -> +tp_points``, ``sl_hit -> -sl_points`` from the
  row's own ``tp_points``/``sl_points`` if ever present, else the row's
  ``bundle_id`` contract under ``models_root``, else the explicitly supplied
  fallback bundle contract. Trades priced no other way land in an ``unpriced``
  bucket — never silently dropped, never guessed.
- Drops (flatten/cutoff/no_fill/no_forward/no_resolution) carry no exit price;
  they are excluded from net and counted by reason.
- Outcome rows carry no session/direction/eligibility — those come from the
  prediction join (``prediction_id``) across the WHOLE directory, because a
  prediction and its outcome can land in different trading-day files.
- Every rate carries its numerator/denominator; every mean carries its n.
- Trades are dated by the OUTCOME's trading day (P&L realization); predictions
  and drops by their own timestamp. The funnel is prediction-cohort based: it
  follows in-scope predictions to their eventual fate even outside the window.
- Journal-quality anomalies (malformed lines, duplicate ids, orphans, conflicts)
  are counted over the whole directory read, before filtering.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import median
from typing import Any

from trade_lab.domain.trading_day import trading_day_for

logger = logging.getLogger(__name__)

RESOLUTION_TP = "tp_hit"
RESOLUTION_SL = "sl_hit"
KNOWN_RESOLUTIONS = frozenset({RESOLUTION_TP, RESOLUTION_SL})

MODES = frozenset({"replay", "live", "all"})
ELIGIBILITY_FILTERS = frozenset({"all", "eligible", "ineligible"})

_STRATEGY_FILE = "strategy.json"
_EVALUATION_FILE = "evaluation.json"
_OOS_PREDICTIONS_FILE = "oos_predictions.parquet"


class JournalDirectoryNotFound(FileNotFoundError):
    """The journal directory does not exist (endpoint maps this to 404)."""


class InvalidPerformanceFilter(ValueError):
    """A filter value is outside its allowed domain (endpoint maps this to 400)."""


@dataclass(frozen=True, slots=True)
class PerformanceFilters:
    """Row filters. ``from_day``/``to_day`` are inclusive TRADING days (18:00 ET roll)."""

    mode: str = "all"
    from_day: date | None = None
    to_day: date | None = None
    bundle_id: str | None = None
    session: str | None = None
    eligibility: str = "all"

    def validate(self) -> None:
        if self.mode not in MODES:
            raise InvalidPerformanceFilter(f"mode must be one of {sorted(MODES)}")
        if self.eligibility not in ELIGIBILITY_FILTERS:
            raise InvalidPerformanceFilter(
                f"eligibility must be one of {sorted(ELIGIBILITY_FILTERS)}"
            )
        if (
            self.from_day is not None
            and self.to_day is not None
            and self.from_day > self.to_day
        ):
            raise InvalidPerformanceFilter("from_day is after to_day")


@dataclass(frozen=True, slots=True)
class PerformanceReport:
    """The aggregate. Sections are JSON-safe dicts; shape is pinned by tests."""

    applied_filters: dict[str, Any]
    headline: dict[str, Any]
    series: list[dict[str, Any]]
    breakdowns: dict[str, Any]
    funnel: dict[str, Any]
    anomalies: dict[str, Any]
    pricing: dict[str, Any]
    oos_comparison: dict[str, Any] | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "applied_filters": self.applied_filters,
            "headline": self.headline,
            "series": self.series,
            "breakdowns": self.breakdowns,
            "funnel": self.funnel,
            "anomalies": self.anomalies,
            "pricing": self.pricing,
            "oos_comparison": self.oos_comparison,
        }


# ---------------------------------------------------------------------------
# Denominator-carrying shapes
# ---------------------------------------------------------------------------


def _ratio(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "value": None if denominator == 0 else numerator / denominator,
        "numerator": numerator,
        "denominator": denominator,
    }


def _mean_stat(values: list[float]) -> dict[str, Any]:
    return {"value": None if not values else sum(values) / len(values), "n": len(values)}


def _dist_stat(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"mean": None, "median": None, "max": None, "n": 0}
    return {
        "mean": sum(values) / len(values),
        "median": median(values),
        "max": max(values),
        "n": len(values),
    }


# ---------------------------------------------------------------------------
# Journal reading
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _RawJournal:
    predictions: dict[str, dict[str, Any]] = field(default_factory=dict)
    outcomes: dict[str, dict[str, Any]] = field(default_factory=dict)
    drops: dict[str, dict[str, Any]] = field(default_factory=dict)
    files_scanned: int = 0
    lines_total: int = 0
    malformed_lines: int = 0
    unknown_type_rows: int = 0
    rows_missing_ids: int = 0
    undated_rows: int = 0
    duplicate_prediction_rows: int = 0
    duplicate_outcome_rows: int = 0
    duplicate_drop_rows: int = 0


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _read_journal(journal_dir: Path) -> _RawJournal:
    """Read every ``*.jsonl`` in the directory (the writer's flat layout).

    First occurrence wins on duplicate ids; repeats are counted, never merged.
    Malformed lines are skipped and counted (the writer is line-buffered without
    fsync, so a torn tail line is a legal state, not an error).
    """

    raw = _RawJournal()
    for file in sorted(journal_dir.glob("*.jsonl"), key=lambda p: p.name):
        raw.files_scanned += 1
        try:
            text = file.read_text(encoding="utf-8")
        except OSError:
            logger.warning("performance: journal file is unreadable: %s", file.name)
            continue
        for line in text.splitlines():
            if not line.strip():
                continue
            raw.lines_total += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                raw.malformed_lines += 1
                continue
            if not isinstance(row, dict):
                raw.malformed_lines += 1
                continue
            row_type = row.get("type")
            if row_type == "prediction":
                _ingest(raw, raw.predictions, row, "duplicate_prediction_rows")
            elif row_type == "outcome":
                _ingest(raw, raw.outcomes, row, "duplicate_outcome_rows")
            elif row_type == "drop":
                _ingest(raw, raw.drops, row, "duplicate_drop_rows")
            else:
                raw.unknown_type_rows += 1
    return raw


def _ingest(
    raw: _RawJournal,
    bucket: dict[str, dict[str, Any]],
    row: dict[str, Any],
    duplicate_counter: str,
) -> None:
    prediction_id = row.get("prediction_id")
    if not isinstance(prediction_id, str) or not prediction_id:
        raw.rows_missing_ids += 1
        return
    ts = _parse_ts(row.get("ts_utc"))
    if ts is None:
        raw.undated_rows += 1
        return
    row["_ts"] = ts
    row["_day"] = trading_day_for(ts)
    if prediction_id in bucket:
        setattr(raw, duplicate_counter, getattr(raw, duplicate_counter) + 1)
        return
    bucket[prediction_id] = row


# ---------------------------------------------------------------------------
# Contract pricing (tp/sl proxy sources)
# ---------------------------------------------------------------------------


def _is_safe_bundle_id(bundle_id: str) -> bool:
    # Mirrors model_registry.is_safe_model_id without importing it (that module
    # imports strategy_core at module scope; this one must stay pure).
    if not bundle_id or len(bundle_id) > 128:
        return False
    if "/" in bundle_id or "\\" in bundle_id or ".." in bundle_id:
        return False
    return all(ch.isalnum() or ch in "_.:-" for ch in bundle_id)


@dataclass(frozen=True, slots=True)
class _ContractPricing:
    tp_points: float
    sl_points: float
    point_value: float | None
    eligible_class: str | None


def _load_contract_pricing(bundle_dir: Path) -> _ContractPricing | None:
    try:
        payload = json.loads((bundle_dir / _STRATEGY_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    policy = payload.get("label_policy")
    if not isinstance(policy, dict):
        return None
    tp = policy.get("tp_points")
    sl = policy.get("sl_points")
    if not isinstance(tp, int | float) or not isinstance(sl, int | float):
        return None
    point_value = payload.get("point_value")
    inference = payload.get("inference")
    eligible_class = inference.get("eligible_class") if isinstance(inference, dict) else None
    return _ContractPricing(
        tp_points=float(tp),
        sl_points=float(sl),
        point_value=float(point_value) if isinstance(point_value, int | float) else None,
        eligible_class=eligible_class if isinstance(eligible_class, str) else None,
    )


class _PricingResolver:
    """Resolve (tp, sl) for one trade row: row fields > row bundle contract > fallback."""

    def __init__(self, models_root: Path | None, fallback: _ContractPricing | None) -> None:
        self._models_root = models_root
        self._fallback = fallback
        self._cache: dict[str, _ContractPricing | None] = {}
        self.source_counts = {"row": 0, "bundle_contract": 0, "fallback_contract": 0}

    def resolve(self, row: dict[str, Any], *, count: bool = True) -> tuple[float, float] | None:
        # ``count=False`` for fate lookups (the cohort funnel) so ``source_counts``
        # stays a per-trade tally of the headline trade set, not doubled.
        tp = row.get("tp_points")
        sl = row.get("sl_points")
        if isinstance(tp, int | float) and isinstance(sl, int | float):
            if count:
                self.source_counts["row"] += 1
            return float(tp), float(sl)
        bundle_id = row.get("bundle_id")
        if isinstance(bundle_id, str):
            pricing = self._bundle_pricing(bundle_id)
            if pricing is not None:
                if count:
                    self.source_counts["bundle_contract"] += 1
                return pricing.tp_points, pricing.sl_points
        if self._fallback is not None:
            if count:
                self.source_counts["fallback_contract"] += 1
            return self._fallback.tp_points, self._fallback.sl_points
        return None

    def _bundle_pricing(self, bundle_id: str) -> _ContractPricing | None:
        if bundle_id in self._cache:
            return self._cache[bundle_id]
        pricing: _ContractPricing | None = None
        if self._models_root is not None and _is_safe_bundle_id(bundle_id):
            candidate = self._models_root / bundle_id
            try:
                if candidate.is_dir():
                    pricing = _load_contract_pricing(candidate)
            except OSError:
                pricing = None
        self._cache[bundle_id] = pricing
        return pricing


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Trade:
    """One outcome row joined (best effort) to its prediction row."""

    outcome: dict[str, Any]
    prediction: dict[str, Any] | None
    day: date
    resolution: str
    points: float | None  # None => unpriced


def aggregate_performance(
    journal_dir: Path,
    *,
    filters: PerformanceFilters | None = None,
    models_root: Path | None = None,
    bundle_dir: Path | None = None,
) -> PerformanceReport:
    """Aggregate the journal directory into a :class:`PerformanceReport`.

    ``models_root`` enables per-row pricing from each row's own ``bundle_id``
    contract; ``bundle_dir`` supplies the fallback ("active") contract AND
    enables the OOS comparison section. Raises :class:`JournalDirectoryNotFound`
    when the directory is absent and :class:`InvalidPerformanceFilter` on
    out-of-domain filter values.
    """

    filters = filters or PerformanceFilters()
    filters.validate()
    if not journal_dir.is_dir():
        raise JournalDirectoryNotFound(str(journal_dir))

    raw = _read_journal(journal_dir)
    fallback_pricing = _load_contract_pricing(bundle_dir) if bundle_dir is not None else None
    resolver = _PricingResolver(models_root, fallback_pricing)

    # --- journal-global anomalies (pre-filter data quality) -----------------
    orphan_outcomes = sum(1 for pid in raw.outcomes if pid not in raw.predictions)
    orphan_drops = sum(1 for pid in raw.drops if pid not in raw.predictions)
    outcome_drop_conflicts = sum(1 for pid in raw.drops if pid in raw.outcomes)

    def _in_window(day: date) -> bool:
        if filters.from_day is not None and day < filters.from_day:
            return False
        return not (filters.to_day is not None and day > filters.to_day)

    def _mode_ok(row: dict[str, Any]) -> bool:
        return filters.mode == "all" or row.get("mode") == filters.mode

    def _bundle_ok(row: dict[str, Any]) -> bool:
        return filters.bundle_id is None or row.get("bundle_id") == filters.bundle_id

    def _prediction_session_eligibility_ok(pred: dict[str, Any] | None) -> tuple[bool, str]:
        """(passes, reason) — reason distinguishes a real mismatch from unknowable."""

        if filters.session is not None:
            if pred is None:
                return False, "unknown_session"
            if pred.get("session") != filters.session:
                return False, "mismatch"
        if filters.eligibility != "all":
            if pred is None or not isinstance(pred.get("is_eligible"), bool):
                return False, "unknown_eligibility"
            wanted = filters.eligibility == "eligible"
            if pred["is_eligible"] is not wanted:
                return False, "mismatch"
        return True, ""

    # --- filtered predictions (prediction-dated) ----------------------------
    scoped_predictions: list[dict[str, Any]] = []
    for pred in raw.predictions.values():
        if not (_mode_ok(pred) and _bundle_ok(pred) and _in_window(pred["_day"])):
            continue
        passes, _ = _prediction_session_eligibility_ok(pred)
        if passes:
            scoped_predictions.append(pred)

    # --- filtered trades (outcome-dated; session/eligibility via the join) --
    trades: list[_Trade] = []
    unknown_resolution_trades = 0
    excluded_unknown_session = 0
    excluded_unknown_eligibility = 0
    for pid, outcome in raw.outcomes.items():
        if not (_mode_ok(outcome) and _bundle_ok(outcome) and _in_window(outcome["_day"])):
            continue
        pred = raw.predictions.get(pid)
        passes, reason = _prediction_session_eligibility_ok(pred)
        if not passes:
            if reason == "unknown_session":
                excluded_unknown_session += 1
            elif reason == "unknown_eligibility":
                excluded_unknown_eligibility += 1
            continue
        resolution = outcome.get("resolution_type")
        if resolution not in KNOWN_RESOLUTIONS:
            unknown_resolution_trades += 1
            continue
        pricing = resolver.resolve(outcome)
        points: float | None = None
        if pricing is not None:
            tp_points, sl_points = pricing
            points = tp_points if resolution == RESOLUTION_TP else -sl_points
        trades.append(
            _Trade(
                outcome=outcome,
                prediction=pred,
                day=outcome["_day"],
                resolution=resolution,
                points=points,
            )
        )

    # --- filtered drops (drop-dated; enrichment via the join) ---------------
    scoped_drops: list[dict[str, Any]] = []
    for pid, drop in raw.drops.items():
        if pid in raw.outcomes:
            continue  # conflict rows counted globally, excluded from drop stats
        if not (_mode_ok(drop) and _bundle_ok(drop) and _in_window(drop["_day"])):
            continue
        passes, _ = _prediction_session_eligibility_ok(raw.predictions.get(pid))
        if passes:
            scoped_drops.append(drop)

    # --- headline ------------------------------------------------------------
    priced = [t for t in trades if t.points is not None]
    unpriced = len(trades) - len(priced)
    wins = [t for t in trades if t.resolution == RESOLUTION_TP]
    priced_wins = [t for t in priced if t.points is not None and t.points > 0]
    priced_losses = [t for t in priced if t.points is not None and t.points <= 0]
    correct_flags = [
        bool(t.outcome["correct"])
        for t in trades
        if isinstance(t.outcome.get("correct"), bool)
    ]
    eligible_trades = [
        t
        for t in trades
        if t.prediction is not None and t.prediction.get("is_eligible") is True
    ]

    gross_win = sum(t.points for t in priced_wins if t.points is not None)
    gross_loss = -sum(t.points for t in priced_losses if t.points is not None)
    net_points = gross_win - gross_loss

    resolution_seconds = [
        (t.outcome["_ts"] - t.prediction["_ts"]).total_seconds()
        for t in trades
        if t.prediction is not None
    ]
    bars_to_resolution = [
        float(t.outcome["bars_to_resolution"])
        for t in trades
        if isinstance(t.outcome.get("bars_to_resolution"), int | float)
    ]

    per_day: dict[date, dict[str, Any]] = {}

    def _day_bucket(day: date) -> dict[str, Any]:
        return per_day.setdefault(
            day,
            {
                "net_points": 0.0,
                "resolved": 0,
                "priced": 0,
                "wins": 0,
                "losses": 0,
                "unpriced": 0,
                "predictions": 0,
                "eligible_predictions": 0,
                "drops": 0,
            },
        )

    for t in trades:
        bucket = _day_bucket(t.day)
        bucket["resolved"] += 1
        if t.points is None:
            bucket["unpriced"] += 1
            continue
        bucket["priced"] += 1
        bucket["net_points"] += t.points
        if t.points > 0:
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1
    for pred in scoped_predictions:
        bucket = _day_bucket(pred["_day"])
        bucket["predictions"] += 1
        if pred.get("is_eligible") is True:
            bucket["eligible_predictions"] += 1
    for drop in scoped_drops:
        _day_bucket(drop["_day"])["drops"] += 1

    series: list[dict[str, Any]] = []
    cumulative = 0.0
    for day in sorted(per_day):
        bucket = per_day[day]
        cumulative += bucket["net_points"]
        series.append(
            {"trading_day": day.isoformat(), **bucket, "cumulative_net_points": cumulative}
        )

    days_with_priced = [d for d, b in per_day.items() if b["priced"] > 0]
    winning_days = sum(1 for d in days_with_priced if per_day[d]["net_points"] > 0)

    direction_split: dict[str, dict[str, Any]] = {}
    for t in trades:
        direction = "unknown"
        if t.prediction is not None and isinstance(t.prediction.get("direction"), str):
            direction = t.prediction["direction"]
        bucket = direction_split.setdefault(
            direction,
            {"resolved": 0, "wins": 0, "priced": 0, "net_points": 0.0},
        )
        bucket["resolved"] += 1
        if t.resolution == RESOLUTION_TP:
            bucket["wins"] += 1
        if t.points is not None:
            bucket["priced"] += 1
            bucket["net_points"] += t.points
    for bucket in direction_split.values():
        bucket["win_rate"] = _ratio(bucket["wins"], bucket["resolved"])
        bucket["expectancy_pts"] = {
            "value": None if bucket["priced"] == 0 else bucket["net_points"] / bucket["priced"],
            "n": bucket["priced"],
        }

    mfe_values = [
        float(t.outcome["max_mfe_pts"])
        for t in trades
        if isinstance(t.outcome.get("max_mfe_pts"), int | float)
    ]
    mae_values = [
        float(t.outcome["max_mae_pts"])
        for t in trades
        if isinstance(t.outcome.get("max_mae_pts"), int | float)
    ]

    headline = {
        "predictions": len(scoped_predictions),
        "eligible_predictions": sum(
            1 for p in scoped_predictions if p.get("is_eligible") is True
        ),
        "resolved_trades": len(trades),
        "eligible_trades": len(eligible_trades),
        "priced_trades": len(priced),
        "unpriced_trades": unpriced,
        "win_rate": _ratio(len(wins), len(trades)),
        "class_accuracy": _ratio(sum(correct_flags), len(correct_flags)),
        "net_points": {"value": net_points, "n_priced": len(priced)},
        "gross_win_points": gross_win,
        "gross_loss_points": gross_loss,
        "profit_factor": {
            "value": None if gross_loss == 0 else gross_win / gross_loss,
            "gross_win": gross_win,
            "gross_loss": gross_loss,
        },
        "avg_win_pts": _mean_stat([t.points for t in priced_wins if t.points is not None]),
        "avg_loss_pts": _mean_stat(
            [-t.points for t in priced_losses if t.points is not None]
        ),
        "avg_time_to_resolution_seconds": _mean_stat(resolution_seconds),
        "avg_bars_to_resolution": _mean_stat(bars_to_resolution),
        "direction": direction_split,
        "day_win_pct": _ratio(winning_days, len(days_with_priced)),
        "mfe_pts": _dist_stat(mfe_values),
        "mae_pts": _dist_stat(mae_values),
        "point_value": fallback_pricing.point_value if fallback_pricing else None,
    }

    # --- breakdowns ----------------------------------------------------------
    per_class: dict[str, dict[str, Any]] = {}
    class_names = {
        str(t.outcome.get("predicted_class")) for t in trades
    } | {str(t.outcome.get("actual_class")) for t in trades}
    for name in sorted(class_names - {"None"}):
        predicted = [t for t in trades if t.outcome.get("predicted_class") == name]
        actual = [t for t in trades if t.outcome.get("actual_class") == name]
        true_positive = sum(1 for t in predicted if t.outcome.get("actual_class") == name)
        predicted_priced = [t for t in predicted if t.points is not None]
        per_class[name] = {
            "predicted": len(predicted),
            "actual": len(actual),
            "precision": _ratio(true_positive, len(predicted)),
            "recall": _ratio(true_positive, len(actual)),
            "win_rate": _ratio(
                sum(1 for t in predicted if t.resolution == RESOLUTION_TP), len(predicted)
            ),
            "net_points": {
                "value": sum(t.points for t in predicted_priced if t.points is not None),
                "n_priced": len(predicted_priced),
            },
        }

    def _keyed_breakdown(key: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for t in trades:
            value = "unknown"
            if t.prediction is not None and isinstance(t.prediction.get(key), str):
                value = t.prediction[key]
            bucket = result.setdefault(
                value, {"resolved": 0, "wins": 0, "priced": 0, "net_points": 0.0}
            )
            bucket["resolved"] += 1
            if t.resolution == RESOLUTION_TP:
                bucket["wins"] += 1
            if t.points is not None:
                bucket["priced"] += 1
                bucket["net_points"] += t.points
        for pred in scoped_predictions:
            value = pred.get(key) if isinstance(pred.get(key), str) else "unknown"
            bucket = result.setdefault(
                value, {"resolved": 0, "wins": 0, "priced": 0, "net_points": 0.0}
            )
            bucket["predictions"] = bucket.get("predictions", 0) + 1
        for bucket in result.values():
            bucket.setdefault("predictions", 0)
            bucket["win_rate"] = _ratio(bucket["wins"], bucket["resolved"])
        return result

    drop_reasons: dict[str, int] = {}
    for drop in scoped_drops:
        reason = drop.get("reason") if isinstance(drop.get("reason"), str) else "unknown"
        drop_reasons[reason] = drop_reasons.get(reason, 0) + 1

    breakdowns = {
        "per_class": per_class,
        "per_level_kind": _keyed_breakdown("level_kind"),
        "per_session": _keyed_breakdown("session"),
        "drop_reasons": drop_reasons,
    }

    # --- funnel (prediction cohort: in-scope predictions -> eventual fate) ---
    cohort_resolved = 0
    cohort_priced = 0
    cohort_dropped = 0
    cohort_pending = 0
    for pred in scoped_predictions:
        pid = pred["prediction_id"]
        outcome = raw.outcomes.get(pid)
        if outcome is not None:
            cohort_resolved += 1
            if (
                outcome.get("resolution_type") in KNOWN_RESOLUTIONS
                and resolver.resolve(outcome, count=False) is not None
            ):
                cohort_priced += 1
        elif pid in raw.drops:
            cohort_dropped += 1
        else:
            cohort_pending += 1
    funnel = {
        "note": "prediction cohort in the filter window, followed to its eventual fate",
        "predictions": len(scoped_predictions),
        "eligible": headline["eligible_predictions"],
        "resolved": cohort_resolved,
        "dropped": cohort_dropped,
        "pending": cohort_pending,
        "priced": cohort_priced,
    }

    anomalies = {
        "note": "whole-directory data quality, counted before filtering",
        "files_scanned": raw.files_scanned,
        "lines_total": raw.lines_total,
        "malformed_lines": raw.malformed_lines,
        "unknown_type_rows": raw.unknown_type_rows,
        "rows_missing_ids": raw.rows_missing_ids,
        "undated_rows": raw.undated_rows,
        "duplicate_prediction_rows": raw.duplicate_prediction_rows,
        "duplicate_outcome_rows": raw.duplicate_outcome_rows,
        "duplicate_drop_rows": raw.duplicate_drop_rows,
        "orphan_outcomes": orphan_outcomes,
        "orphan_drops": orphan_drops,
        "outcome_drop_conflicts": outcome_drop_conflicts,
        "scoped": {
            "unknown_resolution_trades": unknown_resolution_trades,
            "unpriced_trades": unpriced,
            "excluded_unknown_session": excluded_unknown_session,
            "excluded_unknown_eligibility": excluded_unknown_eligibility,
        },
    }

    pricing_section = {
        "rule": (
            "tp_hit -> +tp_points, sl_hit -> -sl_points; drops and unknown resolutions "
            "are never priced"
        ),
        "sources": resolver.source_counts,
        "fallback_contract": None
        if fallback_pricing is None
        else {
            "tp_points": fallback_pricing.tp_points,
            "sl_points": fallback_pricing.sl_points,
            "point_value": fallback_pricing.point_value,
        },
    }

    oos_comparison = (
        _oos_comparison(bundle_dir, fallback_pricing, trades, eligible_trades, priced)
        if bundle_dir is not None
        else None
    )

    applied_filters = {
        "mode": filters.mode,
        "from": None if filters.from_day is None else filters.from_day.isoformat(),
        "to": None if filters.to_day is None else filters.to_day.isoformat(),
        "bundle_id": filters.bundle_id,
        "session": filters.session,
        "eligibility": filters.eligibility,
    }

    return PerformanceReport(
        applied_filters=applied_filters,
        headline=headline,
        series=series,
        breakdowns=breakdowns,
        funnel=funnel,
        anomalies=anomalies,
        pricing=pricing_section,
        oos_comparison=oos_comparison,
    )


# ---------------------------------------------------------------------------
# OOS comparison (bundle artifacts vs the filtered journal)
# ---------------------------------------------------------------------------


def _oos_comparison(
    bundle_dir: Path,
    pricing: _ContractPricing | None,
    trades: list[_Trade],
    eligible_trades: list[_Trade],
    priced: list[_Trade],
) -> dict[str, Any]:
    """Side-by-side OOS artifacts vs the same stats over the filtered journal.

    REPORT_RECON.md (c): the OOS artifacts carry NO MFE/MAE — that side is
    explicitly null. Pricing rules differ (OOS gated expectancy is a tp15/sl30
    simulation; the journal proxy prices the contract's own tp/sl) and each side
    is labeled with its rule rather than pretending comparability.
    """

    oos: dict[str, Any] = {
        "available": False,
        "gated": None,
        "quality_gates": None,
        "class_distribution": None,
        "mfe_mae": None,
        "mfe_mae_note": "not recorded in the OOS artifacts",
    }
    evaluation = _load_json(bundle_dir / _EVALUATION_FILE)
    if evaluation is not None:
        oos["available"] = True
        gated = evaluation.get("gated_oos")
        if isinstance(gated, dict):
            oos["gated"] = {
                "trade_count": gated.get("trade_count"),
                "precision": gated.get("precision"),
                "expectancy_pts": gated.get("expectancy_15_30_pts"),
                "profit_factor": gated.get("profit_factor_15_30"),
                "coverage": gated.get("coverage"),
                "confidence_gate": gated.get("confidence_gate"),
                "eligible_sessions": gated.get("eligible_sessions"),
                "n_samples": gated.get("n_samples"),
                "pricing_rule": "OOS simulation at tp15/sl30 (expectancy_15_30_pts)",
            }
        gates = evaluation.get("quality_gates")
        if isinstance(gates, dict):
            oos["quality_gates"] = {
                "all_passed": gates.get("all_passed"),
                "gates": {
                    name: {
                        "passed": detail.get("passed"),
                        "value": detail.get("value"),
                        "threshold": detail.get("threshold"),
                    }
                    for name, detail in gates.get("gates", {}).items()
                    if isinstance(detail, dict)
                },
            }
        three_class = evaluation.get("oos_three_class_balance")
        if isinstance(three_class, dict):
            oos["class_distribution"] = {"actual": three_class, "predicted": None}

    parquet_distribution = _oos_parquet_distribution(bundle_dir / _OOS_PREDICTIONS_FILE)
    if parquet_distribution is not None:
        oos["available"] = True
        oos["class_distribution"] = parquet_distribution

    eligible_class = pricing.eligible_class if pricing is not None else None
    eligible_hits = sum(
        1 for t in eligible_trades if t.outcome.get("actual_class") == eligible_class
    )
    eligible_priced = [t for t in eligible_trades if t.points is not None]
    eligible_net = sum(t.points for t in eligible_priced if t.points is not None)
    journal = {
        "resolved_trades": len(trades),
        "eligible_trades": len(eligible_trades),
        "gated_hit_rate": {
            **_ratio(eligible_hits, len(eligible_trades)),
            "definition": f"actual_class == {eligible_class!r} among eligible resolved trades",
        },
        "gated_win_rate": _ratio(
            sum(1 for t in eligible_trades if t.resolution == RESOLUTION_TP),
            len(eligible_trades),
        ),
        "gated_expectancy_pts": {
            "value": None if not eligible_priced else eligible_net / len(eligible_priced),
            "n": len(eligible_priced),
            "pricing_rule": "journal proxy at the contract's own tp/sl",
        },
        "class_distribution": {
            "predicted": _count_by(trades, "predicted_class"),
            "actual": _count_by(trades, "actual_class"),
            "n": len(trades),
        },
        "mfe_mae": {
            "mfe_pts": _dist_stat(
                [
                    float(t.outcome["max_mfe_pts"])
                    for t in trades
                    if isinstance(t.outcome.get("max_mfe_pts"), int | float)
                ]
            ),
            "mae_pts": _dist_stat(
                [
                    float(t.outcome["max_mae_pts"])
                    for t in trades
                    if isinstance(t.outcome.get("max_mae_pts"), int | float)
                ]
            ),
        },
        "priced_trades": len(priced),
    }
    return {"bundle_id": bundle_dir.name, "oos": oos, "journal": journal}


def _count_by(trades: list[_Trade], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in trades:
        value = t.outcome.get(key)
        name = value if isinstance(value, str) else "unknown"
        counts[name] = counts.get(name, 0) + 1
    return counts


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _oos_parquet_distribution(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file():
            return None
    except OSError:
        return None
    try:
        import pyarrow.parquet as pq  # lazy: only the OOS section needs it

        table = pq.read_table(path, columns=["label", "pred_label"])
    except Exception:
        logger.warning("performance: OOS parquet is unreadable: %s", path.name)
        return None
    actual: dict[str, int] = {}
    predicted: dict[str, int] = {}
    for value in table.column("label").to_pylist():
        name = value if isinstance(value, str) else "unknown"
        actual[name] = actual.get(name, 0) + 1
    for value in table.column("pred_label").to_pylist():
        name = value if isinstance(value, str) else "unknown"
        predicted[name] = predicted.get(name, 0) + 1
    return {"actual": actual, "predicted": predicted, "n": table.num_rows}
