"""GATE B (D1a) — characterization, NOT pass/fail: old tracker vs dark honest resolver.

Replays the 9 real store days 2025-07-10 -> 2025-07-22 through the FULL
``ApplicationRuntime`` with the real bundle ``NQ_20260604_015413`` activated through
the registry (contract -> checksum -> CatBoost -> feature validation), live-like
(no per-day reset; the SC engine rolls trading days itself). Every prediction the
runtime produces is tracked by BOTH paths exactly as wired in production: the legacy
level-anchored ``OutcomeTracker`` (the served stream) and the DARK SC streaming
honest resolver (the D1a seat). Emits ``D1_CHARACTERIZATION.md`` beside ``backend/``.

Scope notes (honest): the replay feeds TRADE prints only (the b3 store-window
convention: front-month, action 't', price>0, WIRE order, prev-18:00 -> 18:00 ET);
quote-dependent approach features are NaN under the contract's model_native NaN
policy, so prediction COUNTS and resolution semantics are characterized, not
quote-fidelity feature values. bars_to_resolution deltas are reported with the
old path's 1-based count normalized to the new path's 0-based index (old-1 vs new).

Run: python validation/d1_characterization_harness.py   (from backend/, PYTHONPATH=src)
"""

from __future__ import annotations

import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

from strategy_core import StreamDrop, StreamResolution  # noqa: E402

from trade_lab.domain.events import TradeEvent, TradeSide  # noqa: E402
from trade_lab.services.inference.inference_engine import InferenceEngine  # noqa: E402
from trade_lab.services.model_registry import ModelRegistry  # noqa: E402
from trade_lab.services.runtime import ApplicationRuntime  # noqa: E402

DATA_DIR = Path(r"C:/Users/gonza/Documents/Trade-Dashboard/data/databento")
MODELS_ROOT = Path(r"C:/Users/gonza/Documents/Claude-Quant-Lab/models")
MODEL_ID = "NQ_20260604_015413"
SYMBOL = "NQ"
TICK_SIZE = 0.25
DAYS = [
    "2025-07-10", "2025-07-11", "2025-07-14", "2025-07-15", "2025-07-16",
    "2025-07-17", "2025-07-18", "2025-07-21", "2025-07-22",
]
_SIDE = {"B": TradeSide.BUY, "A": TradeSide.SELL}
OUT_MD = BACKEND / "D1_CHARACTERIZATION.md"


def _window(day: str):
    d = date.fromisoformat(day)
    prev = d - timedelta(days=1)
    start = pd.Timestamp(f"{prev} 18:00:00", tz="America/New_York").tz_convert("UTC")
    end = pd.Timestamp(f"{d} 18:00:00", tz="America/New_York").tz_convert("UTC")
    return prev, start, end


def _read_trade_events(day: str) -> list[TradeEvent]:
    """The b3 store-window trade extraction, emitted as TL TradeEvents."""
    prev, start, end = _window(day)
    files = [DATA_DIR / SYMBOL / prev.isoformat() / "mbp10.parquet",
             DATA_DIR / SYMBOL / day / "mbp10.parquet"]
    frames = [pd.read_parquet(f, columns=["ts_event", "action", "price", "size", "side", "symbol"])
              for f in files if f.exists()]
    if not frames:
        return []
    raw = pd.concat(frames, ignore_index=True)
    ts = pd.to_datetime(raw["ts_event"], utc=True)
    inwin = (ts >= start) & (ts < end) & (~raw["symbol"].astype(str).str.contains("-"))
    if not inwin.any():
        return []
    fm = raw.loc[inwin, "symbol"].value_counts().idxmax()
    mask = inwin & (raw["symbol"] == fm) & (raw["action"].astype(str).str.lower() == "t") \
        & raw["price"].notna() & (raw["price"] > 0)
    sub = raw[mask].copy()
    sub["ts_event"] = pd.to_datetime(sub["ts_event"], utc=True)
    sub = sub.sort_values("ts_event", kind="stable").reset_index(drop=True)
    ts_arr = sub["ts_event"].to_numpy()
    px = sub["price"].to_numpy("float64")
    sz = sub["size"].to_numpy("int64")
    side = sub["side"].astype(str).to_numpy()
    return [
        TradeEvent(
            event_ts_utc=pd.Timestamp(ts_arr[i]).to_pydatetime(),
            receive_ts_utc=None,
            instrument_id=1,
            requested_symbol="NQ.c.0",
            raw_symbol=str(fm),
            price_ticks=round(px[i] / TICK_SIZE),
            size=int(sz[i]),
            side=_SIDE.get(side[i], TradeSide.UNKNOWN),
            source_schema="mbp-10",
        )
        for i in range(len(sub))
    ]


def main() -> None:
    registry = ModelRegistry(MODELS_ROOT)
    active = registry.activate(MODEL_ID)
    engine = InferenceEngine(registry)
    runtime = ApplicationRuntime(
        requested_symbol="NQ.c.0",
        tick_timeframes=(147, 987, 2000),  # production defaults (config.py)
        observation_duration_seconds=300,
    )
    runtime.set_inference_engine(engine)
    policy = active.contract.label_policy
    lines: list[str] = []
    lines.append("# D1 CHARACTERIZATION — old tracker vs dark honest resolver (GATE B)\n")
    lines.append(f"Bundle: `{MODEL_ID}` activated through the registry "
                 f"(engine_version `{active.contract.engine_version}`); label_policy: "
                 f"tp={policy.tp_points} sl={policy.sl_points} trap={policy.trap_mfe_min} "
                 f"forward={policy.forward_bar_type} offset={policy.decision_offset_minutes}m "
                 f"cutoff=`{policy.forward_cutoff}`.")
    lines.append(f"Days: {DAYS[0]} -> {DAYS[-1]} (9 store days, trades-only, live-like "
                 "continuous replay — no per-day reset; see harness docstring for scope).\n")

    predictions: dict[str, object] = {}
    old_by_pred: dict[str, object] = {}
    per_day_rows: list[tuple[str, int, int, Counter, int, Counter]] = []
    dark_seen = 0

    for day in DAYS:
        events = _read_trade_events(day)
        if not events:
            per_day_rows.append((day, 0, 0, Counter(), 0, Counter()))
            continue
        day_preds = 0
        day_old = Counter()
        n_trades = 0
        for ev in events:
            update = runtime.process_market_event(ev)
            n_trades += 1
            for p in update.predictions:
                predictions[p.prediction_id] = p
                day_preds += 1
            for o in update.outcomes:
                old_by_pred[o.prediction_id] = o
                day_old[o.resolution_type.value] += 1
        dark_now = runtime.dark_outcomes
        day_dark = Counter()
        for item in dark_now[dark_seen:]:
            day_dark[_dark_kind(item)] += 1
        dark_delta = len(dark_now) - dark_seen
        dark_seen = len(dark_now)
        per_day_rows.append((day, n_trades, day_preds, day_old, dark_delta, day_dark))

    # Harness end: flush the dark resolver past the last day's window so still-open
    # setups finalize exactly as the streaming cutoff rule dictates.
    _, _, last_end = _window(DAYS[-1])
    resolver = runtime._honest_resolver  # gate-B harness owns the dark seat
    flushed = resolver.flush(last_end.to_pydatetime()) if resolver is not None else ()
    if flushed:
        runtime._append_dark(flushed)
    old_still_open = (
        runtime._outcome_tracker.open_count if runtime._outcome_tracker is not None else 0
    )
    dark = {item.key: item for item in runtime.dark_outcomes}

    lines.append("## Per-day\n")
    lines.append(
        "| day | trades | predictions | old outcomes (by type) | dark events | dark (by kind) |"
    )
    lines.append("|---|---|---|---|---|---|")
    for day, n_trades, day_preds, day_old, dark_delta, day_dark in per_day_rows:
        lines.append(f"| {day} | {n_trades} | {day_preds} | {dict(day_old) or '-'} "
                     f"| {dark_delta} | {dict(day_dark) or '-'} |")
    if flushed:
        lines.append(f"| (harness flush) | - | - | - | {len(flushed)} | "
                     f"{dict(Counter(_dark_kind(i) for i in flushed))} |")

    total_preds = len(predictions)
    old_types = Counter(o.resolution_type.value for o in old_by_pred.values())
    new_kinds = Counter(_dark_kind(i) for i in dark.values())
    lines.append("\n## Totals\n")
    lines.append(f"- predictions registered (both paths, one-for-one): **{total_preds}**")
    lines.append(f"- OLD path resolutions: **{sum(old_types.values())}** by type "
                 f"{dict(old_types)}; still open at harness end: {old_still_open}")
    lines.append(f"- NEW path events: **{len(dark)}** by kind {dict(new_kinds)}; "
                 f"still open after flush: {resolver.open_count if resolver else 0}")

    # Pairwise characterization.
    entry_deltas: list[float] = []
    flips = 0
    both_resolved = 0
    bars_deltas = Counter()
    session_end_map = Counter()
    old_to_new = Counter()
    for pid, old in old_by_pred.items():
        new = dark.get(pid)
        if new is None:
            old_to_new[(old.resolution_type.value, "STILL-OPEN/MISSING")] += 1
            continue
        new_kind = _dark_kind(new)
        old_to_new[(old.resolution_type.value, new_kind)] += 1
        if old.resolution_type.value == "session_end":
            session_end_map[new_kind] += 1
        pred = predictions.get(pid)
        old_entry = pred.level_price_ticks * TICK_SIZE if pred is not None else None
        new_entry = getattr(new, "entry_price", None)
        if old_entry is not None and new_entry is not None:
            entry_deltas.append((new_entry - old_entry) / TICK_SIZE)
        if isinstance(new, StreamResolution) and pred is not None:
            both_resolved += 1
            old_correct = old.correct
            new_correct = new.result.label == pred.predicted_class
            if old_correct != new_correct:
                flips += 1
            bars_deltas[(old.bars_to_resolution - 1) - new.result.bars_to_resolution] += 1

    lines.append("\n## Old -> new mapping (per prediction with an OLD outcome)\n")
    lines.append("| old resolution | new kind | count |")
    lines.append("|---|---|---|")
    for (o, n), c in sorted(old_to_new.items()):
        lines.append(f"| {o} | {n} | {c} |")
    lines.append(f"\n- SESSION_END -> new-kind mapping: {dict(session_end_map) or 'none'}")
    lines.append(f"- correctness flips among both-resolved pairs: **{flips}** of {both_resolved}")
    if entry_deltas:
        s = pd.Series(entry_deltas)
        lines.append(
            f"- entry-price delta (new trade print - old level price), ticks: count={len(s)} "
            f"mean={s.mean():.2f} mean|.|={s.abs().mean():.2f} max|.|={s.abs().max():.0f} "
            f"min={s.min():.0f} max={s.max():.0f}"
        )
    else:
        lines.append("- entry-price delta: no comparable pairs")
    lines.append(f"- bars_to_resolution deltas (old-1 minus new, both-resolved): "
                 f"{dict(bars_deltas) or 'none'}")

    new_only = {k: _dark_kind(v) for k, v in dark.items() if k not in old_by_pred}
    lines.append(f"\n- predictions with a NEW event but NO old outcome (tracker still open "
                 f"or never resolved): {dict(Counter(new_only.values())) or 'none'}")

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nwritten: {OUT_MD}")


def _dark_kind(item) -> str:
    if isinstance(item, StreamResolution):
        return f"resolved:{item.result.label}"
    if isinstance(item, StreamDrop):
        return f"drop:{item.reason}"
    return type(item).__name__


if __name__ == "__main__":
    import warnings

    warnings.simplefilter("ignore")
    main()
