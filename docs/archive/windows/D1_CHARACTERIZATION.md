# D1 CHARACTERIZATION — old tracker vs dark honest resolver (GATE B)

Bundle: `NQ_20260604_015413` activated through the registry (engine_version `strategy_core_engine_v3`); label_policy: tp=15.0 sl=15.0 trap=5.0 forward=147t offset=5m cutoff=`17:00_US/Eastern_ny_close`.
Days: 2025-07-10 -> 2025-07-22 (9 store days, trades-only, live-like continuous replay — no per-day reset; see harness docstring for scope).

## Per-day

| day | trades | predictions | old outcomes (by type) | dark events | dark (by kind) |
|---|---|---|---|---|---|
| 2025-07-10 | 247828 | 4 | {'sl_hit': 3, 'tp_hit': 1} | 4 | {'resolved:tradeable_reversal': 2, 'resolved:aggressive_blowthrough': 2} |
| 2025-07-11 | 292586 | 1 | {'tp_hit': 1} | 1 | {'resolved:trap_reversal': 1} |
| 2025-07-14 | 263663 | 3 | {'tp_hit': 2, 'sl_hit': 1} | 3 | {'resolved:tradeable_reversal': 3} |
| 2025-07-15 | 339997 | 3 | {'tp_hit': 3} | 3 | {'resolved:tradeable_reversal': 3} |
| 2025-07-16 | 362141 | 4 | {'sl_hit': 3, 'tp_hit': 1} | 4 | {'resolved:aggressive_blowthrough': 2, 'resolved:trap_reversal': 1, 'resolved:tradeable_reversal': 1} |
| 2025-07-17 | 279919 | 3 | {'tp_hit': 2, 'sl_hit': 1} | 3 | {'resolved:tradeable_reversal': 3} |
| 2025-07-18 | 273353 | 4 | {'tp_hit': 2, 'sl_hit': 2} | 4 | {'resolved:tradeable_reversal': 3, 'resolved:aggressive_blowthrough': 1} |
| 2025-07-21 | 242286 | 4 | {'tp_hit': 2, 'sl_hit': 2} | 4 | {'resolved:tradeable_reversal': 2, 'resolved:aggressive_blowthrough': 2} |
| 2025-07-22 | 336902 | 3 | {'sl_hit': 2, 'tp_hit': 1} | 3 | {'resolved:tradeable_reversal': 1, 'resolved:aggressive_blowthrough': 1, 'resolved:trap_reversal': 1} |

## Totals

- predictions registered (both paths, one-for-one): **29**
- OLD path resolutions: **29** by type {'sl_hit': 14, 'tp_hit': 15}; still open at harness end: 0
- NEW path events: **29** by kind {'resolved:tradeable_reversal': 18, 'resolved:aggressive_blowthrough': 8, 'resolved:trap_reversal': 3}; still open after flush: 0

## Old -> new mapping (per prediction with an OLD outcome)

| old resolution | new kind | count |
|---|---|---|
| sl_hit | resolved:aggressive_blowthrough | 7 |
| sl_hit | resolved:tradeable_reversal | 5 |
| sl_hit | resolved:trap_reversal | 2 |
| tp_hit | resolved:aggressive_blowthrough | 1 |
| tp_hit | resolved:tradeable_reversal | 13 |
| tp_hit | resolved:trap_reversal | 1 |

- SESSION_END -> new-kind mapping: none
- correctness flips among both-resolved pairs: **5** of 29
- entry-price delta (new trade print - old level price), ticks: count=29 mean=-20.48 mean|.|=55.59 max|.|=226 min=-226 max=134
- bars_to_resolution deltas (old-1 minus new, both-resolved): {-21: 1, -72: 1, -6: 3, -9: 1, -3: 4, 1: 1, -12: 1, 0: 2, -1: 2, -7: 2, 2: 1, -19: 1, -58: 1, 12: 1, -5: 1, 8: 2, 6: 1, -17: 1, -4: 1, -11: 1}

- predictions with a NEW event but NO old outcome (tracker still open or never resolved): none
