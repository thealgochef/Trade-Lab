"""W3b — batch<->serving parity gate + falsification (validation tooling).

This package is NOT shipped serving code; it drives the real Trade-Lab serving
stack headlessly to prove it reproduces, per touch, the QL training-cache rows
(``ml_utility_7850272e.parquet``) that trained bundle ``NQ_W3_20260613T055600Z``.

Modules:
  * ``window``         — the QL cache-build provenance reused verbatim (the 73-day
                         D-036 window, the rolling PDH/PDL ``prev_full_hl`` seed,
                         the contract feature order). The shared bootstrap inputs
                         the gate CONTROLS so it can ISOLATE the engine transform.
  * ``headless_replay``— P0: replay one trading day through the activated bundle
                         and the real runtime/resolver/journal, no FastAPI/ws.
  * ``parity``         — P1/P2: per-touch join + DayDiff + offline model scoring.
"""
