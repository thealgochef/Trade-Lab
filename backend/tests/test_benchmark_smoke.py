from datetime import UTC, datetime, timedelta
from time import perf_counter

import pytest

from trade_lab.domain.candles import CandleEngine
from trade_lab.domain.events import TradeEvent
from trade_lab.domain.levels import SessionLevelEngine


def test_synthetic_benchmark_path_is_deterministic_at_small_scale() -> None:
    start = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    candles = CandleEngine((10,))
    levels = SessionLevelEngine()
    completed = 0
    touches = 0

    for i in range(25):
        event = TradeEvent(
            start + timedelta(seconds=i),
            None,
            1,
            "NQ.c.0",
            "NQM6",
            68_000 + (i % 4),
            1,
        )
        completed += len(candles.process_trade(event).completed)
        touches += len(levels.process_trade(event).touches)

    assert completed == 2
    assert touches == 0


@pytest.mark.benchmark
def test_synthetic_domain_throughput_smoke() -> None:
    count = 100_000
    warmup_count = 10_000
    runs = 5
    gate_events_per_second = 100_000
    start = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    events = tuple(
        TradeEvent(
            start + timedelta(milliseconds=i),
            None,
            1,
            "NQ.c.0",
            "NQM6",
            68_000 + (i % 40),
            1,
        )
        for i in range(count)
    )

    def run_once(sample: tuple[TradeEvent, ...]) -> float:
        candles = CandleEngine()
        levels = SessionLevelEngine()
        process_candle = candles.process_trade
        process_level = levels.process_trade

        t0 = perf_counter()
        for event in sample:
            process_candle(event)
            process_level(event)
        return len(sample) / (perf_counter() - t0)

    # This opt-in smoke benchmark gates the CandleEngine + SessionLevelEngine domain
    # hot path only. Setup, API, websocket, replay controls, and synthetic event
    # construction stay outside the timed region. A short untimed warmup and best-of-N
    # policy reduce scheduler/CPU-frequency noise while still requiring one full,
    # meaningful candle+level processing pass to clear the gate.
    run_once(events[:warmup_count])
    throughputs = [run_once(events) for _ in range(runs)]
    best = max(throughputs)
    median = sorted(throughputs)[runs // 2]
    formatted_runs = ", ".join(f"{throughput:,.0f}" for throughput in throughputs)
    print(
        "synthetic candle+level throughput: "
        f"best {best:,.0f} events/sec, median {median:,.0f} events/sec "
        f"over {count:,} events x {runs} runs (runs: {formatted_runs})"
    )
    assert best >= gate_events_per_second
