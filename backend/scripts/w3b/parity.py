"""W3b P1/P2 — per-touch batch<->serving parity + offline model scoring.

For one trading day the gate:
  * replays the day through the real serving stack -> a JSONL journal (P0), then
  * loads the QL training cache row set (``ml_utility_7850272e.parquet``), and
  * joins training <-> serving PER SURVIVING TOUCH on
    (level_type == level_kind, direction, touch instant) requiring 1:1, then
    asserts, with tol=0 on the features (any nonzero diff is a real W3b finding):

    (a) surviving-touch set identical + drop set reconciles,
    (b) the 5 contract features bit-exact,
    (c) training.label == serving outcome.actual_class,
    (d) representative_price == level_price_ticks * tick_size (nearest tick),
    (e) max_mfe / max_mae == outcome.max_mfe_pts / max_mae_pts,
    (P2) offline CatBoost predict_proba on the cache features (contractual order)
         == serving probabilities (tol 1e-6), and the offline gate
         (p[eligible] >= gate AND session==ny) == serving is_eligible.

``int_time_beyond_level`` (cached, unpinned, not served) and
``entry_price`` / ``decision_time`` (serving-only / training-only) are NOT compared.

Reconciliation note (the <5-interaction-trade asymmetry): QL drops a touch with
fewer than 5 interaction trades, with NO record left in the cache; the serving
engine has no such gate and DOES emit a prediction+outcome for it. So a serving
survivor absent from the cache is reconciled iff re-deriving QL's drop rules over
the same stream explains it (``classify_serving_only``). A serving survivor the
cache kept must match bit-for-bit; a cache row with no serving survivor is always
a hard mismatch.
"""

from __future__ import annotations

import bisect
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from w3b.window import (
    BUNDLE_ID,
    CLASS_MAP,
    CONTRACT_FEATURES,
    TICK_SIZE,
    W3Window,
    resolve_window,
)

#: Decision offset (minutes): serving prediction.event_ts_utc == touch + this.
_DECISION_OFFSET = timedelta(minutes=5)
#: Model-scoring probability tolerance (P2). Same features through the same
#: CatBoost binary -> the only slack is float-repr of the JSON-roundtripped proba.
_PROBA_TOL = 1e-6
#: Eligibility gate (strategy.json inference).
_ELIGIBLE_CLASS = "tradeable_reversal"
_ELIGIBLE_SESSION = "ny"
_CONFIDENCE_GATE = 0.70
#: Touch-instant join tolerance (ns). The serving instant rides a pandas
#: Timestamp through the journal (ns-preserving); set 0 and report the residual.
_INSTANT_TOL_NS = 0
#: QL's interaction-trade drop threshold (engine_decision.py:764 — `< 5` prints in
#: the interaction window => the row is dropped, leaving NO cache record). A
#: serving-only survivor below this is the expected batch↔serving asymmetry, not a bug.
_INTERACTION_MIN_TRADES = 5


# ── Serving journal ──────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ServingTouch:
    """A serving survivor: one prediction joined to its resolved outcome."""

    prediction_id: str
    touch_id: str
    level_kind: str
    direction: str
    session: str
    touch_instant: pd.Timestamp  # event_ts_utc - decision offset (= touch bar close)
    level_price_ticks: int
    feature_values: dict[str, float]
    predicted_class: str
    probabilities: dict[str, float]
    is_eligible: bool
    nan_count: int
    actual_class: str
    max_mfe_pts: float
    max_mae_pts: float
    resolution_type: str


@dataclass(frozen=True)
class ServingDrop:
    prediction_id: str
    touch_id: str
    level_kind: str
    direction: str
    touch_instant: pd.Timestamp
    reason: str


@dataclass
class ServingDay:
    survivors: list[ServingTouch]
    drops: list[ServingDrop]
    n_predictions: int
    n_orphan_predictions: int  # prediction with neither outcome nor drop (bug guard)


def _parse_ts(value: str) -> pd.Timestamp:
    """Parse a journal ISO timestamp to a UTC pandas Timestamp (ns-precise)."""

    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def parse_journal(path: Path) -> ServingDay:
    """Parse a ``{date}.jsonl`` serving journal into survivors + drops.

    A prediction joins to exactly one terminal event: an OUTCOME (resolved
    survivor) or a DROP (registration or terminal). Predictions are keyed by
    ``prediction_id``; the touch instant is reconstructed as
    ``event_ts_utc - decision_offset`` so it aligns with training ``event_ts``.
    """

    predictions: dict[str, dict] = {}
    outcomes: dict[str, dict] = {}
    drops: dict[str, dict] = {}
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                kind = rec.get("type")
                if kind == "prediction":
                    predictions[rec["prediction_id"]] = rec
                elif kind == "outcome":
                    outcomes[rec["prediction_id"]] = rec
                elif kind == "drop":
                    drops[rec["prediction_id"]] = rec

    survivors: list[ServingTouch] = []
    drop_list: list[ServingDrop] = []
    orphans = 0
    for pid, pred in predictions.items():
        touch_instant = _parse_ts(pred["ts_utc"]) - _DECISION_OFFSET
        if pid in outcomes:
            oc = outcomes[pid]
            survivors.append(
                ServingTouch(
                    prediction_id=pid,
                    touch_id=pred["touch_id"],
                    level_kind=str(pred["level_kind"]).lower(),
                    direction=str(pred["direction"]).lower(),
                    session=str(pred["session"]).lower(),
                    touch_instant=touch_instant,
                    level_price_ticks=int(pred["level_price_ticks"]),
                    feature_values={k: float(v) for k, v in pred["feature_values"].items()},
                    predicted_class=pred["predicted_class"],
                    probabilities={k: float(v) for k, v in pred["probabilities"].items()},
                    is_eligible=bool(pred["is_eligible"]),
                    nan_count=int(pred["nan_count"]),
                    actual_class=oc["actual_class"],
                    max_mfe_pts=float(oc["max_mfe_pts"]),
                    max_mae_pts=float(oc["max_mae_pts"]),
                    resolution_type=str(oc["resolution_type"]),
                )
            )
        elif pid in drops:
            dr = drops[pid]
            drop_list.append(
                ServingDrop(
                    prediction_id=pid,
                    touch_id=pred["touch_id"],
                    level_kind=str(pred["level_kind"]).lower(),
                    direction=str(pred["direction"]).lower(),
                    touch_instant=touch_instant,
                    reason=str(dr["reason"]),
                )
            )
        else:
            orphans += 1
    return ServingDay(
        survivors=survivors,
        drops=drop_list,
        n_predictions=len(predictions),
        n_orphan_predictions=orphans,
    )


# ── Training cache ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TrainingTouch:
    level_type: str
    direction: str
    session: str
    touch_instant: pd.Timestamp  # event_ts (ET) -> UTC == touch bar close
    representative_price: float
    label: str
    label_encoded: int
    max_mfe: float
    max_mae: float
    features: dict[str, float]


def load_training_touches(window: W3Window, date_str: str) -> list[TrainingTouch]:
    path = window.cache_path(date_str)
    if not path.is_file():
        return []
    df = pd.read_parquet(path)
    touches: list[TrainingTouch] = []
    for row in df.itertuples(index=False):
        d = row._asdict()
        touches.append(
            TrainingTouch(
                level_type=str(d["level_type"]).lower(),
                direction=str(d["direction"]).lower(),
                session=str(d["session"]).lower(),
                touch_instant=pd.Timestamp(d["event_ts"]).tz_convert("UTC"),
                representative_price=float(d["representative_price"]),
                label=str(d["label"]),
                label_encoded=int(d["label_encoded"]),
                max_mfe=float(d["max_mfe"]),
                max_mae=float(d["max_mae"]),
                features={f: float(d[f]) for f in CONTRACT_FEATURES},
            )
        )
    return touches


# ── Per-touch diff ───────────────────────────────────────────────────────────
@dataclass
class TouchMismatch:
    key: str
    axis: str  # feature:<name> | label | mfe | mae | price | proba:<label> | eligible
    detail: str


@dataclass
class DayDiff:
    day: str
    cache_present: bool = False
    # counts
    training_touches: int = 0
    serving_survivors: int = 0
    serving_drops: int = 0
    matched: int = 0
    # set reconciliation
    unmatched_training: list[str] = field(default_factory=list)  # cache row, no serving (RED)
    unmatched_serving: list[str] = field(default_factory=list)  # serving, no cache, >=5 (RED)
    # serving-only survivor QL would have dropped for <5 interaction trades — the
    # expected asymmetry (Finding-1). Recorded, does NOT make the day red.
    reconciled_serving_only: list[str] = field(default_factory=list)
    duplicate_keys: list[str] = field(default_factory=list)
    orphan_predictions: int = 0
    # per-axis mismatches (each must be empty for green)
    feature_mismatches: list[TouchMismatch] = field(default_factory=list)
    label_mismatches: list[TouchMismatch] = field(default_factory=list)
    excursion_mismatches: list[TouchMismatch] = field(default_factory=list)
    price_mismatches: list[TouchMismatch] = field(default_factory=list)
    instant_mismatches: list[TouchMismatch] = field(default_factory=list)
    proba_mismatches: list[TouchMismatch] = field(default_factory=list)
    eligible_mismatches: list[TouchMismatch] = field(default_factory=list)
    # diagnostics
    max_instant_diff_ns: int = 0
    max_feature_abs_diff: float = 0.0
    max_proba_abs_diff: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def green(self) -> bool:
        return (
            not self.unmatched_training
            and not self.unmatched_serving
            and not self.duplicate_keys
            and self.orphan_predictions == 0
            and not self.feature_mismatches
            and not self.label_mismatches
            and not self.excursion_mismatches
            and not self.price_mismatches
            and not self.instant_mismatches
            and not self.proba_mismatches
            and not self.eligible_mismatches
        )

    def summary(self) -> str:
        verdict = "GREEN" if self.green else "RED"
        nmis = (
            len(self.feature_mismatches)
            + len(self.label_mismatches)
            + len(self.excursion_mismatches)
            + len(self.price_mismatches)
            + len(self.instant_mismatches)
            + len(self.proba_mismatches)
            + len(self.eligible_mismatches)
        )
        return (
            f"[{verdict}] {self.day}: cache={self.cache_present} "
            f"train={self.training_touches} serv_surv={self.serving_survivors} "
            f"matched={self.matched} drops={self.serving_drops} "
            f"unmatched_train={len(self.unmatched_training)} "
            f"unmatched_serv={len(self.unmatched_serving)} "
            f"recon_serv_only={len(self.reconciled_serving_only)} "
            f"mismatches={nmis} max_inst_diff_ns={self.max_instant_diff_ns} "
            f"max_feat_diff={self.max_feature_abs_diff:g} "
            f"max_proba_diff={self.max_proba_abs_diff:g}"
        )


def _key(level_type: str, direction: str) -> str:
    return f"{level_type}|{direction}"


def diff_day(
    date_str: str,
    journal_path: Path,
    *,
    window: W3Window | None = None,
    scorer: OfflineScorer | None = None,
    serving_only_counter: Callable[[ServingTouch], int] | None = None,
) -> DayDiff:
    """Load the day's training cache + serving journal, then diff all axes.

    Supplies the REAL QL-backed serving-only interaction-trade counter by default
    (Finding-1): a serving-only survivor is reconciled iff QL's own <5-trade rule
    would have dropped it (see :class:`QLInteractionTradeCounter`).
    """

    window = window or resolve_window()
    training = load_training_touches(window, date_str)
    serving = parse_journal(journal_path)
    if serving_only_counter is None:
        serving_only_counter = QLInteractionTradeCounter(window, date_str)
    return diff_touch_sets(
        date_str,
        training,
        serving,
        cache_present=window.has_cache(date_str),
        scorer=scorer,
        serving_only_counter=serving_only_counter,
    )


def diff_touch_sets(
    date_str: str,
    training: list[TrainingTouch],
    serving: ServingDay,
    *,
    cache_present: bool = False,
    scorer: OfflineScorer | None = None,
    serving_only_counter: Callable[[ServingTouch], int] | None = None,
) -> DayDiff:
    """Join a training touch set <-> a serving day and assert all parity axes.

    Pure over its inputs (no I/O): the falsification suite drives this directly
    with synthetic touch sets to prove each axis can go RED. ``serving_only_counter``
    is the injected interaction-trade count provider for serving-only survivors —
    the real run passes the QL-backed counter; tests pass a stub returning a chosen
    count; ``None`` (no provider) keeps every serving-only survivor RED.
    """

    diff = DayDiff(day=date_str)
    diff.cache_present = cache_present
    diff.training_touches = len(training)
    diff.serving_survivors = len(serving.survivors)
    diff.serving_drops = len(serving.drops)
    diff.orphan_predictions = serving.n_orphan_predictions

    # Index by (level_type, direction); first_touch_per_zone_per_day makes this
    # unique within a day. Flag any collision as a duplicate key (would break 1:1).
    train_by_key: dict[str, TrainingTouch] = {}
    for t in training:
        k = _key(t.level_type, t.direction)
        if k in train_by_key:
            diff.duplicate_keys.append(f"train:{k}")
        train_by_key[k] = t
    serv_by_key: dict[str, ServingTouch] = {}
    for s in serving.survivors:
        k = _key(s.level_kind, s.direction)
        if k in serv_by_key:
            diff.duplicate_keys.append(f"serv:{k}")
        serv_by_key[k] = s

    for k in sorted(set(train_by_key) - set(serv_by_key)):
        diff.unmatched_training.append(k)
    # Finding-1: a serving-only survivor (no cache row) is the EXPECTED asymmetry iff
    # QL would have dropped it for <5 interaction trades; otherwise it is a real bug
    # (serving kept a touch QL would have kept, yet the cache has no row).
    for k in sorted(set(serv_by_key) - set(train_by_key)):
        s = serv_by_key[k]
        count = serving_only_counter(s) if serving_only_counter is not None else None
        if classify_serving_only(count):
            diff.reconciled_serving_only.append(f"{k} (interaction_trades={count})")
        else:
            diff.unmatched_serving.append(k)

    for k in sorted(set(train_by_key) & set(serv_by_key)):
        t = train_by_key[k]
        s = serv_by_key[k]
        diff.matched += 1

        inst_diff = abs(int(t.touch_instant.value) - int(s.touch_instant.value))
        diff.max_instant_diff_ns = max(diff.max_instant_diff_ns, inst_diff)
        if inst_diff > _INSTANT_TOL_NS:
            diff.instant_mismatches.append(
                TouchMismatch(
                    k,
                    "instant",
                    f"train={t.touch_instant.isoformat()} "
                    f"serv(-offset)={s.touch_instant.isoformat()} diff={inst_diff} ns",
                )
            )

        # (b) features, tol=0
        for name in CONTRACT_FEATURES:
            tv = t.features[name]
            sv = s.feature_values.get(name)
            if sv is None:
                diff.feature_mismatches.append(
                    TouchMismatch(k, f"feature:{name}", "missing in serving")
                )
                continue
            d = abs(tv - sv)
            diff.max_feature_abs_diff = max(diff.max_feature_abs_diff, d)
            if not _exact(tv, sv):
                diff.feature_mismatches.append(
                    TouchMismatch(k, f"feature:{name}", f"train={tv!r} serv={sv!r} diff={d:g}")
                )

        # (c) label == actual_class
        if t.label != s.actual_class:
            diff.label_mismatches.append(
                TouchMismatch(k, "label", f"train={t.label!r} serv={s.actual_class!r}")
            )

        # (e) excursions
        if not _exact(t.max_mfe, s.max_mfe_pts):
            diff.excursion_mismatches.append(
                TouchMismatch(k, "mfe", f"train={t.max_mfe!r} serv={s.max_mfe_pts!r}")
            )
        if not _exact(t.max_mae, s.max_mae_pts):
            diff.excursion_mismatches.append(
                TouchMismatch(k, "mae", f"train={t.max_mae!r} serv={s.max_mae_pts!r}")
            )

        # (d) representative_price <-> level_price_ticks (nearest tick)
        expected_ticks = round(t.representative_price / TICK_SIZE)
        if expected_ticks != s.level_price_ticks:
            diff.price_mismatches.append(
                TouchMismatch(
                    k,
                    "price",
                    f"rep_px={t.representative_price} -> {expected_ticks}t "
                    f"!= serv {s.level_price_ticks}t",
                )
            )

        # (P2) offline scoring on the cache features
        if scorer is not None:
            offline = scorer.score(t.features)
            for label, sv in s.probabilities.items():
                ov = offline.get(label)
                if ov is None:
                    diff.proba_mismatches.append(
                        TouchMismatch(k, f"proba:{label}", "label absent offline")
                    )
                    continue
                d = abs(ov - sv)
                diff.max_proba_abs_diff = max(diff.max_proba_abs_diff, d)
                if d > _PROBA_TOL:
                    diff.proba_mismatches.append(
                        TouchMismatch(k, f"proba:{label}", f"offline={ov!r} serv={sv!r} diff={d:g}")
                    )
            offline_eligible = _offline_eligible(offline, s.session)
            if offline_eligible != s.is_eligible:
                diff.eligible_mismatches.append(
                    TouchMismatch(
                        k, "eligible", f"offline={offline_eligible} serv={s.is_eligible}"
                    )
                )
    return diff


def _exact(a: float, b: float) -> bool:
    if math.isnan(a) and math.isnan(b):
        return True
    return a == b


def _offline_eligible(probabilities: dict[str, float], session: str) -> bool:
    predicted = max(probabilities, key=lambda label: probabilities[label])
    return (
        predicted == _ELIGIBLE_CLASS
        and session.split("_", 1)[0] == _ELIGIBLE_SESSION
        and probabilities.get(_ELIGIBLE_CLASS, 0.0) >= _CONFIDENCE_GATE
    )


# ── Finding-1: serving-only reconciliation (<5-interaction-trade asymmetry) ───
def classify_serving_only(count: int | None) -> bool:
    """Is a serving-only survivor RECONCILED (the expected asymmetry) or a RED bug?

    Pure: returns ``True`` (reconcile — QL would have dropped this touch, leaving no
    cache row) iff the interaction-trade ``count`` is known and below QL's threshold
    (engine_decision.py:764, ``< 5``). ``count is None`` (no provider) or
    ``count >= 5`` -> ``False`` (RED: QL would have kept it, so a missing cache row is
    a real divergence).
    """

    return count is not None and count < _INTERACTION_MIN_TRADES


class QLInteractionTradeCounter:
    """Count a serving-only touch's interaction trades the way QL's labeler does.

    Reproduces QL's drop rule VERBATIM in definition, not by guess:
    ``engine_decision.py:761-766`` computes
    ``interaction_trades = _trades_in(touch.bar_ts_utc, touch.bar_ts_utc +
    interaction_window)`` then drops the row on ``len(interaction_trades) < 5``;
    ``_trades_in`` (``:694-695``) is
    ``trades[bisect_left(trade_ts, start):bisect_left(trade_ts, end)]`` over the
    front-month ``Trade`` prints harvested from the SAME canonical
    ``DatabentoParquetSource.for_trading_day`` stream (``:654-668``) — i.e. a
    half-open ``[touch_bar_close, touch_bar_close + interaction_window)`` count.

    This counter reads that exact stream (same source, same default front-month
    selection, same ``requested_symbol``) and counts identically (``bisect_left`` on
    both bounds, ns-precise), with ``interaction_window`` taken from the resolved
    D-036 config (``window.util_kwargs['interaction_window_minutes']``). The day's
    trade timestamps are read once and memoized; only invoked when a serving-only
    survivor actually exists (none on the 51 evaluated days).
    """

    def __init__(self, window: W3Window, date_str: str) -> None:
        self._window = window
        self._date_str = date_str
        self._interaction_window = timedelta(
            minutes=int(window.util_kwargs["interaction_window_minutes"])
        )
        self._trade_ns: list[int] | None = None

    def _trade_ns_sorted(self) -> list[int]:
        if self._trade_ns is None:
            from strategy_core.data.databento_parquet import DatabentoParquetSource
            from strategy_core.types import Trade as ScTrade

            source = DatabentoParquetSource.for_trading_day(
                self._window.symbol_dir,
                date.fromisoformat(self._date_str),
                requested_symbol=self._window.symbol,
            )
            ns = [
                pd.Timestamp(ev.event_ts_utc).value
                for ev in source.events()
                if isinstance(ev, ScTrade)
            ]
            ns.sort()  # canonical SC order is already ts-sorted; explicit for bisect safety
            self._trade_ns = ns
        return self._trade_ns

    def __call__(self, serv_touch: ServingTouch) -> int:
        trade_ns = self._trade_ns_sorted()
        start = int(serv_touch.touch_instant.value)
        end = int((serv_touch.touch_instant + self._interaction_window).value)
        return bisect.bisect_left(trade_ns, end) - bisect.bisect_left(trade_ns, start)


# ── P2 offline scorer ────────────────────────────────────────────────────────
class OfflineScorer:
    """Score cache feature rows through the bundle's CatBoost binary offline.

    Mirrors the serving probability mapping exactly: ``predict_proba`` on the
    contract-ordered vector, then map ``model.classes_`` through ``class_map``.
    """

    def __init__(self, model_path: Path) -> None:
        from catboost import CatBoostClassifier

        self._model = CatBoostClassifier()
        self._model.load_model(str(model_path))

    @classmethod
    def for_bundle(
        cls, window: W3Window | None = None, bundle_id: str = BUNDLE_ID
    ) -> OfflineScorer:
        window = window or resolve_window()
        return cls(window.models_path / bundle_id / "model.cbm")

    def score(self, features: dict[str, float]) -> dict[str, float]:
        vector = [features[name] for name in CONTRACT_FEATURES]
        proba = self._model.predict_proba([vector])[0]
        out: dict[str, float] = {}
        for cls, value in zip(self._model.classes_, proba, strict=True):
            out[CLASS_MAP[int(cls)]] = float(value)
        return out
