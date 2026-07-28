"""SEED-PARITY probe (read-only): QL training seed vs TL replay seed vs ground truth.

For each probe day D:
  (a) QL seed = the rolling ``prev_full_hl`` carry entering D, replicated via QL's OWN
      ``_get_session_hl_for_date`` with the cache-warmer backward-walk (provably equal
      to the serial carry: first non-None walking back from D's predecessor), under two
      window conventions: ALL store days, and the D-036 training window
      (2025-11-21..2026-02-13, cold-start None on its first day).
  (b) TL dashboard replay seed = UNSEEDED (recon SEED_PARITY_RECON.md section 1: no code
      path on POST /api/v1/replay/start calls load_prior_day_summary; runtime.reset
      rebuilds the plugin with empty summaries). Nothing to compute; marked.
  (c) Ground truth = full [18:00 ET -> 18:00 ET) extremes of the most recent prior store
      day with >=1 front-month trade, computed from the canonical SC reader
      (DatabentoParquetSource.for_trading_day, front_month_only=True), max/min of
      Trade.price_ticks. Every walked candidate day is recorded.
  Plus: an INDEPENDENT pyarrow re-computation of the reader extremes (trade rows of the
  dominant-by-trade-count non-spread instrument inside the same UTC window) as a
  cross-check, and TL live's key day (prior_trading_day) for attribution.

Comparisons are in ticks (QL points -> ticks via QL's own _round_to_ticks, tick 0.25).
Run:  python scratch_seed_parity.py   (from the TL root; ~10-20 min, reader-bound)
"""
from __future__ import annotations

import json
import sys
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DATA_DIR = Path(r"C:\Users\gonza\Documents\Claude-Quant-Lab\data\databento")
SYMBOL = "NQ"
SYMBOL_DIR = DATA_DIR / SYMBOL
PROBE_DAYS = [
    "2026-02-18",
    "2026-02-17",
    "2026-01-20",
    "2026-01-12",
    "2025-12-26",
    "2025-11-21",
    "2025-07-15",
]
D036_START, D036_END = "2025-11-21", "2026-02-13"
OUT_JSON = Path(__file__).with_name("scratch_seed_parity_results.json")

# ── QL's own functions (editable-installed alpha_lab) ─────────────────────────
from alpha_lab.agents.data_infra.ml.config import MLPipelineConfig  # noqa: E402
from alpha_lab.agents.data_infra.ml.dashboard_utility_builder import (  # noqa: E402
    _get_session_hl_for_date,
)
from alpha_lab.agents.data_infra.ml.engine_decision import (  # noqa: E402
    TRADE_TICK,
    _round_to_ticks,
)

# ── SC canonical reader (editable-installed strategy_core) ────────────────────
from strategy_core.data.databento_parquet import DatabentoParquetSource  # noqa: E402
from strategy_core.types import Trade  # noqa: E402

# ── TL live key convention (definition only, no API) ──────────────────────────
try:
    from trade_lab.domain.trading_day import prior_trading_day
except Exception as exc:  # pragma: no cover
    prior_trading_day = None
    print(f"NOTE: trade_lab import failed ({exc}); live key column = N/A", flush=True)

UTIL_CFG = MLPipelineConfig().dashboard_utility  # bar_type default "147t" == D-036
_TICK_FILENAMES = ["mbp10.parquet", "mbp1.parquet", "trades.parquet"]  # ml_training_tab.py:49


def get_available_dates() -> list[str]:
    """Mirror of QL ml_training_tab.get_available_dates (dirs holding a tick file)."""
    out = []
    for p in sorted(SYMBOL_DIR.iterdir()):
        if p.is_dir() and any((p / f).exists() for f in _TICK_FILENAMES):
            out.append(p.name)
    return out


def ql_seed_walk(day: str, dates: list[str]) -> dict:
    """QL serial-carry seed entering ``day``: backward-walk, first non-None
    _get_session_hl_for_date(prev, seed=None) — the w3_cache_warmer._seed_for_day
    replication (proven equal to the serial carry incl. empty-day carry-through)."""
    prevs = [d for d in dates if d < day]
    walked = []
    for p in reversed(prevs):
        t0 = _time.time()
        hl = _get_session_hl_for_date(DATA_DIR, SYMBOL, p, UTIL_CFG, None)
        walked.append({"day": p, "result": hl, "secs": round(_time.time() - t0, 1)})
        if hl is not None:
            return {
                "seed_day": p,
                "high_pts": hl[0],
                "low_pts": hl[1],
                "high_ticks": _round_to_ticks(hl[0], TRADE_TICK),
                "low_ticks": _round_to_ticks(hl[1], TRADE_TICK),
                "walked": walked,
            }
    return {"seed_day": None, "walked": walked}  # cold start


def reader_extremes(day_str: str) -> dict:
    """Canonical-reader extremes for trading day ``day_str``: max/min Trade.price_ticks
    over DatabentoParquetSource.for_trading_day(front_month_only=True).events()."""
    td = date.fromisoformat(day_str)
    t0 = _time.time()
    source = DatabentoParquetSource.for_trading_day(SYMBOL_DIR, td, requested_symbol=SYMBOL)
    hi = lo = None
    n_trades = 0
    warnings = []
    for ev in source.events():
        if isinstance(ev, Trade):
            n_trades += 1
            p = ev.price_ticks
            if hi is None or p > hi:
                hi = p
            if lo is None or p < lo:
                lo = p
        elif not isinstance(ev, Trade) and type(ev).__name__ == "DataQualityWarning":
            warnings.append(getattr(ev, "code", None) and str(ev.code))
    return {
        "day": day_str,
        "n_trades": n_trades,
        "high_ticks": hi,
        "low_ticks": lo,
        "warnings": [w for w in warnings if w][:5],
        "n_warnings": len(warnings),
        "secs": round(_time.time() - t0, 1),
    }


def pyarrow_extremes(day_str: str) -> dict:
    """INDEPENDENT cross-check of the reader (different code path): trade rows of the
    dominant-by-trade-count non-spread instrument, within [prev 18:00 ET, D 18:00 ET),
    straight off the parquet files with pyarrow."""
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    import pyarrow as pa

    td = date.fromisoformat(day_str)
    prev = td - timedelta(days=1)
    tz = ZoneInfo("US/Eastern")
    start = datetime.combine(prev, datetime.min.time().replace(hour=18), tzinfo=tz)
    end = datetime.combine(td, datetime.min.time().replace(hour=18), tzinfo=tz)
    split = datetime(td.year, td.month, td.day, tzinfo=ZoneInfo("UTC"))

    def _ns(dt: datetime) -> int:
        return int(dt.timestamp()) * 1_000_000_000  # whole-second boundaries -> exact

    frames = []
    for d, w0, w1 in ((prev, start, split), (td, split, end)):
        f = SYMBOL_DIR / d.isoformat() / "mbp10.parquet"
        if not f.exists():
            continue
        t = pq.read_table(f, columns=["ts_event", "action", "price", "symbol", "instrument_id"])
        ts_ns = pc.cast(t["ts_event"], pa.int64())  # column is timestamp[ns, UTC]
        m = (
            pc.and_(
                pc.and_(pc.greater_equal(ts_ns, _ns(w0)), pc.less(ts_ns, _ns(w1))),
                pc.equal(pc.utf8_upper(t["action"]), "T"),
            )
        )
        frames.append(t.filter(m))
    if not frames:
        return {"day": day_str, "n_trades": 0}
    import pyarrow as pa

    t = pa.concat_tables(frames)
    # non-spread: symbol without '-'; dominant instrument by TRADE-row count, tie -> larger id
    nonspread = t.filter(pc.invert(pc.match_substring(t["symbol"], "-")))
    if nonspread.num_rows == 0:
        return {"day": day_str, "n_trades": 0}
    counts = {}
    for iid in nonspread["instrument_id"].to_pylist():
        counts[iid] = counts.get(iid, 0) + 1
    front = max(counts, key=lambda k: (counts[k], k))
    ft = nonspread.filter(pc.equal(nonspread["instrument_id"], front))
    prices = ft["price"].to_pylist()
    sym = ft["symbol"][0].as_py() if ft.num_rows else None
    return {
        "day": day_str,
        "n_trades": ft.num_rows,
        "front_symbol": sym,
        "front_instrument_id": front,
        "instrument_trade_counts": {str(k): v for k, v in sorted(counts.items())},
        "high_ticks": int(round(max(prices) / 0.25)),
        "low_ticks": int(round(min(prices) / 0.25)),
    }


def gt_walk(day: str, dates: list[str]) -> dict:
    """Ground truth: most recent prior store day with >=1 front-month trade (reader)."""
    prevs = [d for d in dates if d < day]
    candidates = []
    for p in reversed(prevs):
        r = reader_extremes(p)
        candidates.append(r)
        if r["n_trades"] > 0:
            return {"gt_day": p, **{k: r[k] for k in ("high_ticks", "low_ticks", "n_trades")},
                    "candidates": candidates}
    return {"gt_day": None, "candidates": candidates}


def classify(ql: dict, gt: dict) -> str:
    if ql.get("seed_day") is None:
        return "ABSENCE (cold start: no prior day in window)"
    if gt.get("gt_day") is None:
        return "ABSENCE (no ground-truth prior day)"
    if ql["seed_day"] != gt["gt_day"]:
        return f"KEY (QL seed day {ql['seed_day']} != GT day {gt['gt_day']})"
    dh = ql["high_ticks"] - gt["high_ticks"]
    dl = ql["low_ticks"] - gt["low_ticks"]
    if dh == 0 and dl == 0:
        return "GREEN (same day, tick-exact)"
    return f"COMPUTATION (same day {ql['seed_day']}, dH={dh} dL={dl} ticks)"


def main() -> None:
    dates = get_available_dates()
    print(f"store: {len(dates)} available dates {dates[0]}..{dates[-1]}", flush=True)
    d036 = [d for d in dates if D036_START <= d <= D036_END]
    results = []
    for day in PROBE_DAYS:
        print(f"\n=== probe {day} ===", flush=True)
        present = day in dates
        ql_all = ql_seed_walk(day, dates)
        print(f"  QL(all-store) seed day={ql_all.get('seed_day')} "
              f"H/L pts={ql_all.get('high_pts')}/{ql_all.get('low_pts')}", flush=True)
        if D036_START <= day <= D036_END:
            ql_win = ql_seed_walk(day, d036)
        else:
            ql_win = {"seed_day": "N/A (outside D-036 window)"}
        gt = gt_walk(day, dates)
        print(f"  GT day={gt.get('gt_day')} H/L ticks={gt.get('high_ticks')}/{gt.get('low_ticks')} "
              f"n_trades={gt.get('n_trades')}", flush=True)
        xcheck = pyarrow_extremes(gt["gt_day"]) if gt.get("gt_day") else None
        if xcheck:
            agree = (xcheck.get("high_ticks") == gt.get("high_ticks")
                     and xcheck.get("low_ticks") == gt.get("low_ticks"))
            print(f"  xcheck(pyarrow) {'AGREES' if agree else 'DISAGREES'}: "
                  f"{xcheck.get('high_ticks')}/{xcheck.get('low_ticks')} "
                  f"front={xcheck.get('front_symbol')}", flush=True)
        live_key = (prior_trading_day(date.fromisoformat(day)).isoformat()
                    if prior_trading_day else "N/A")
        row = {
            "probe_day": day,
            "in_store": present,
            "ql_all_store": ql_all,
            "ql_d036_window": ql_win,
            "tl_dashboard_replay": "UNSEEDED (recon section 1: no seed call on the path)",
            "ground_truth": gt,
            "reader_xcheck_pyarrow": xcheck,
            "tl_live_key_day": live_key,
            "ql_vs_gt": classify(ql_all, gt),
            "ql_d036_vs_gt": (classify(ql_win, gt)
                              if ql_win.get("seed_day") != "N/A (outside D-036 window)"
                              else "N/A"),
        }
        results.append(row)
        OUT_JSON.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        print(f"  ql_vs_gt: {row['ql_vs_gt']}   ql_d036_vs_gt: {row['ql_d036_vs_gt']}", flush=True)
    print(f"\nwrote {OUT_JSON}", flush=True)


if __name__ == "__main__":
    main()
