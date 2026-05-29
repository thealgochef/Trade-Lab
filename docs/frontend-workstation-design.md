# Frontend Workstation Design — Phase 1

Trade-Lab should present as a hedge-fund analytics workstation, not a copy of the old dashboard layout. The UI must be professional, dark, chart-centered, high signal-to-noise, and explicit about data quality.

Frontend target port: `5174`.

## Design Principles

- Chart-first layout for rapid market context.
- Dark institutional visual language with restrained color.
- Clear separation between market state, level intelligence, event log, and future predictions.
- Data-quality and contract state always visible.
- No raw tick spam in the UI.
- Typed realtime client and domain-specific stores instead of a monolithic global store.

## Primary Layout

```text
┌──────────────────────────────────────────────────────────────┐
│ Top Status / Command Bar                                      │
├──────────────────────────────────────────────┬───────────────┤
│ Main Chart                                   │ Intelligence  │
│                                              │ Panel         │
├──────────────────────────────────────────────┴───────────────┤
│ Bottom Event Blotter                                          │
└──────────────────────────────────────────────────────────────┘
```

## Top Status / Command Bar

The top bar should show operational state at a glance:

- requested symbol and resolved front-month contract;
- live vs replay mode;
- current session;
- trading day;
- feed status;
- data/schema contract version;
- latency;
- last event time;
- selected tick timeframe: `147t`, `987t`, or `2000t`;
- warnings and degraded-state badges.

Controls may include timeframe selection, replay controls, and connection actions when implemented.

## Main Chart

Use `lightweight-charts` for the primary chart.

Chart requirements:

- tick bars only;
- live in-progress bar;
- completed bars emitted after configured trade count;
- level overlays for PDH/PDL and Asia/London/NY highs/lows;
- visible distinction between eligible signal levels and display-only levels;
- touch markers at exact traded-price touches;
- session separators;
- trading-day boundary markers;
- data-quality overlays for stale feed, replay gaps, or unresolved instrument state.

The chart should consume generic overlay models, not hard-coded implementation details from backend domain objects.

## Right Intelligence Panel

The intelligence panel should summarize current market structure:

- current session;
- trading day;
- developing highs/lows;
- PDH/PDL;
- Asia High/Low;
- London High/Low;
- NY High/Low;
- distance from last traded price to each level;
- display vs eligible state;
- per-session touch status;
- active/completed observation state;
- future model prediction area, disabled until ML phases.

Distances should be computed from integer ticks and displayed in ticks and points where useful.

## Bottom Event Blotter

The event blotter should be compact and filterable.

Event categories:

- session transitions;
- trading-day transitions;
- level updates;
- valid touches;
- observation start/end;
- feed status changes;
- replay status changes;
- data-quality warnings;
- future model predictions and diagnostics.

Each event should include timestamp, severity/category, short message, and relevant metadata.

## Frontend State Architecture

Prefer split domain stores over a monolithic store.

Suggested state areas:

- connection/feed state;
- instrument and contract metadata;
- bars by timeframe;
- levels and eligibility;
- touches and observations;
- replay controls;
- event blotter;
- warnings/data-quality state;
- future prediction state.

The realtime client should be typed and should translate backend messages into frontend domain models before updating stores.

## Realtime Client Expectations

The WebSocket client should support:

- typed message discrimination;
- snapshot/bootstrap handling;
- delta updates;
- reconnect with resubscription;
- stale-feed detection from heartbeat or last event time;
- schema/version mismatch warnings;
- replay state messages.

The UI should not assume every message is a raw market event. It should consume semantic updates such as bar updates, level changes, touch events, observation changes, and warnings.

## Visual Semantics

Suggested visual distinctions:

- eligible levels: stronger line weight or accent color;
- display-only levels: muted/dashed line;
- touched levels: marker plus changed line state;
- active observation: highlighted region or marker state;
- stale/degraded feed: top-bar badge and chart overlay;
- replay mode: persistent mode badge to prevent confusion with live.

## Open Questions

- Final color palette and typography.
- Exact layout behavior on laptop vs large monitor.
- Whether multiple tick timeframes can be shown simultaneously or only selected one.
- Event blotter retention and filtering defaults.
