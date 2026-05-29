# Sessions, Levels, Touches, and Observations — Phase 1

This document defines Trade-Lab v1 session, level, touch, and observation semantics.

## Timezone and Trading Day

All session logic uses `America/Chicago`.

Trading day:

```text
6:00 PM CT -> 4:00 PM CT next day
```

Trading-day labels should be deterministic and based on event timestamps, not wall-clock processing time. Internal event timestamps remain UTC.

Trading-day label convention: use the calendar date of the 4:00 PM `America/Chicago` close. For example, the trading day that starts Sunday at 6:00 PM CT and closes Monday at 4:00 PM CT is labeled Monday.

## Sessions

Sessions are defined in Chicago time:

| Session | Start | End |
| --- | ---: | ---: |
| Asia | 6:00 PM | 2:00 AM |
| London | 2:00 AM | 8:00 AM |
| NY | 8:00 AM | 4:00 PM |

Session boundaries are end-exclusive except where a trading-day close event is explicitly finalized.

## Levels

Trade-Lab tracks these levels:

- PDH: prior full trading day high;
- PDL: prior full trading day low;
- Asia High;
- Asia Low;
- London High;
- London Low;
- NY High;
- NY Low.

All level prices are integer ticks.

## PDH and PDL

PDH/PDL are the high and low of the prior full trading day:

```text
prior 6:00 PM CT -> 4:00 PM CT trading day
```

They are not prior NY/RTH-only levels.

PDH/PDL become available only after a complete prior trading day has been explicitly finalized by the runtime or loaded from replay/session summary data. Incomplete observed snippets, such as starting live processing mid-day, must not be promoted automatically to next-day PDH/PDL. Until an exchange holiday calendar is added, loaded/finalized summaries define the available prior trading days; the engine uses the latest complete summary before the current trading-day label, so a Friday summary can become Monday's PDH/PDL after a weekend gap.

## Developing Session Levels

Asia, London, and NY highs/lows are developing levels during their own sessions.

Rules:

- A session high updates when a trade prints above the current high.
- A session low updates when a trade prints below the current low.
- Developing levels are display/context levels while their own session is forming.
- Same-session developing levels are not signal/touch eligible.

Example: Asia cannot sweep or touch its own high/low while Asia is forming.

## Display Levels vs Eligible Signal Levels

The system must explicitly distinguish:

- display levels: visible for context;
- eligible signal levels: valid for touch detection and observation creation.

Frontend overlays and intelligence panels should render this distinction clearly.

Eligibility is computed from event timestamp, current session, level origin, and previous touch state.

## Touch Eligibility

All sessions allow touches, subject to level eligibility.

Rules:

- Touch source is last traded price only.
- Top-of-book bid/ask does not trigger touches.
- Touch zone is exact price only: `0` points.
- No cutoff distance.
- Use integer tick equality: `trade.price_ticks == level.price_ticks`.
- One touch per level per session.
- Same-session developing levels are not eligible while forming.
- Other sessions can revisit prior/current eligible levels.

Examples:

- London can touch completed Asia High/Low from the same trading day.
- NY can touch completed Asia or London levels from the same trading day.
- NY cannot use NY High/Low as signal levels while NY is forming.
- PDH/PDL may be eligible in any session if not already touched for that level in that session.

## Touch Event

A valid touch should produce a semantic event containing:

- UTC event timestamp;
- trading day;
- current session;
- level id/type;
- level price ticks;
- trade price ticks;
- requested symbol/raw contract/instrument id, when available;
- whether this created a new observation;
- touch sequence metadata for that session.

## Observation Lifecycle

An observation starts on a valid touch.

Initial proposed observation window:

```text
5 minutes, configurable
```

There is no ML prediction in Phase 1-4. Observations are used to prepare future model integration and to show post-touch market context.

Observation state should include:

- observation id;
- originating touch id;
- start timestamp;
- scheduled end timestamp;
- current status: active, completed, cancelled, or expired;
- associated level and session metadata.

## Session Transitions

At session start:

- initialize new developing high/low from first trade in the session;
- reset per-session touch state;
- publish a session transition event.

At new trading-day start:

- reinitialize developing Asia, London, and NY levels for the new trading day;
- reset trading-day-scoped level/touch state;
- prior trading-day session levels are not signal-eligible unless explicitly retained as historical display context;
- PDH/PDL become the prior full trading-day high/low.

At session end:

- freeze that session's high/low for later sessions;
- publish final session level values;
- keep levels available for display and future eligibility where allowed.

At trading-day end:

- finalize any in-progress tick bar at last trade;
- finalize NY levels;
- compute completed trading-day high/low for the next day's PDH/PDL;
- publish trading-day summary state.

## Open Questions

- Whether touch state should reset only per session or also across replay seeks.
- Exact handling for days with incomplete historical coverage.
- Observation cancellation rules if replay seeks backwards or a data gap is detected.
