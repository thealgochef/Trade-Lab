# Sessions, Levels, Zones, Touches, and Observations — Strategy-Core Runtime

Trade-Lab no longer owns independent session/level/touch strategy semantics. The
runtime delegates authoritative bars, sessions, levels, merged zones, and touches
to `strategy_core.runtime.StrategyRuntime` through
`trade_lab.services.strategy_core_service.StrategyCoreService`. Trade-Lab maps the
Strategy-Core output to API/WebSocket DTOs and starts observations from mapped
`TouchEvent`s.

## Timezone and Trading Day

Authoritative runtime session logic uses Strategy-Core
`RESEARCH_SESSION_SCHEME`:

```text
Timezone: US/Eastern
Trading-day boundary: 6:00 PM ET
Trading-day label: local calendar date after applying the 6:00 PM ET boundary
```

Internal event timestamps remain UTC. Session/trading-day labels must be derived
from event timestamps, never wall-clock processing time.

## Sessions

Strategy-Core v3 named session windows are ET-native and DST-aware:

| Session | Start | End | Notes |
| --- | ---: | ---: | --- |
| Asia | 7:00 PM ET | 2:45 AM ET | Crosses midnight. |
| London | 3:00 AM ET | 8:00 AM ET | End-exclusive. |
| NY | 9:00 AM ET | 5:00 PM ET | End-exclusive; aligns with the 17:00 ET forward-label cutoff. |

The ET gaps `18:00–19:00`, `02:45–03:00`, `08:00–09:00`, and `17:00–18:00`
are intentionally unsessioned (`none`). They are still inside the trading-day
calendar where applicable, but they are not named Asia/London/NY sessions.

## Levels

Strategy-Core tracks the canonical level names that Trade-Lab maps to display
DTOs today:

- `pdh`: prior trading-day high;
- `pdl`: prior trading-day low;
- `asia_high` / `asia_low`;
- `london_high` / `london_low`.

NY is a Strategy-Core session label and forward-cutoff window, but current
`StrategyLevelState` does not emit separate `ny_high` / `ny_low` levels. Add those
in Strategy-Core before documenting or rendering them as active runtime levels.

Trade-Lab DTOs expose prices as integer NQ ticks. Strategy-Core stores level and
zone calculations in points internally and converts through the adapter boundary.

## PDH and PDL

PDH/PDL come from the complete prior Strategy-Core trading day. They are not a
separate Trade-Lab Chicago/RTH-only calculation. Incomplete snippets, such as
starting live processing mid-day, must not be promoted automatically into the
next day’s PDH/PDL. Loaded/finalized summaries define which prior trading days
are available until an exchange holiday/calendar source is added.

## Level Availability

Strategy-Core enforces `available_from` lookahead guards:

- PDH/PDL are available from the Strategy-Core trading-day start.
- Asia high/low become signal-eligible only after Asia closes at 02:45 ET.
- London high/low become signal-eligible only after London closes at 08:00 ET.
- A merged zone uses the max `available_from` across its constituent levels.

Developing levels may be displayed for context, but they do not create touches
before Strategy-Core marks them available.

## Zones and Touch Eligibility

Touch detection is zone-based, not exact Trade-Lab level equality:

- Levels within `3.0` points merge into one zone.
- The zone representative is the mean price of its constituent levels.
- A touch fires when a closed Strategy-Core decision bar’s `[low, high]` range
  intersects the zone representative.
- First-touch state is tracked per zone per Strategy-Core trading day.
- Quote/top-of-book events do not create bars or touches; only trade prints
  advance Strategy-Core bars and touch detection.

This replaces the old Trade-Lab-only exact-tick rule
`trade.price_ticks == level.price_ticks`. Legacy exact-touch/domain modules may
remain for compatibility tests, but `ApplicationRuntime` must not use them as the
authoritative runtime path.

## Touch Event Mapping

A Strategy-Core touch is mapped to a Trade-Lab `TouchEvent` containing:

- UTC event timestamp from the touched decision bar;
- Strategy-Core trading day and session label;
- level/zone representative mapped to integer ticks;
- latest trade price ticks for context;
- requested symbol/raw contract/instrument id when available;
- whether this created an observation;
- sequence metadata for the mapped Trade-Lab session.

## Observation Lifecycle

An observation starts from a valid Strategy-Core-derived touch.

Default observation window:

```text
5 minutes, configurable
```

Observations are Trade-Lab runtime objects. They do not recompute touch
eligibility. Inference, when active, runs only after observations expire and must
use the active model contract.

Observation state includes:

- observation id;
- originating touch id;
- start timestamp;
- scheduled end timestamp;
- current status: active, expired, cancelled, or completed/resolved where a
  downstream workflow defines that transition;
- associated level/zone and session metadata.

## Session Transitions

Strategy-Core owns session/trading-day transitions, developing levels, level
freezing, zone construction, and end-of-day bar finalization. Trade-Lab should
publish/surface Strategy-Core-derived snapshots and deltas only; it should not
run a second session/level/touch state machine for live or replay.

## Remaining Operational Questions

- Manual live validation still needs to confirm the Strategy-Core-derived
  session/touch stream during active market hours.
- Replay seeks/backwards time travel remain controller concerns; current runtime
  processing is forward-only and records timestamp regressions as data-quality
  warnings.
- Future model promotion must verify that each model’s `strategy.json` session,
  level, touch, and bar settings match the Strategy-Core runtime contract.
