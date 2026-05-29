# Market Data Contract — Phase 1

This document defines the Trade-Lab v1 market-data contract for live and replay. The contract is designed so historical/replay interpretation matches live interpretation.

## Instrument

- Product: NQ futures.
- Tick size: `0.25` index points.
- Point value: `$20` per point.
- Minimum tick value: `$5`.

Prices must be stored and compared as integer ticks:

```text
price_ticks = exact_decimal_ticks(price, tick_size=0.25)
price = price_ticks * 0.25
```

Price normalization must use fixed-point/decimal arithmetic, not binary floating-point rounding. Validate that every price-like field is exactly divisible by the instrument tick size. Invalid, missing, or non-aligned prices must produce data-quality warnings and must not be silently rounded to the nearest tick. Internal price equality and range checks use integer ticks only.

## Symbol and Symbology

Trade-Lab should request the Databento front-month symbol/symbology. The Databento API is responsible for front-month resolution.

Persist and display, when available:

- requested symbol;
- resolved raw contract;
- instrument id;
- dataset/schema metadata;
- data schema version.

The frontend must make the requested symbol and resolved contract visible in the top status/command bar.

## Databento Subscription Scope

The user subscription allows:

- MBP-1 / CMBP-1;
- BBO / CBBO;
- TBBO / TCBBO;
- Trades;
- OHLCV;
- Definition;
- Statistics;
- Status.

### v1 Feed Recommendation

Primary v1 feed:

- Trades, as the canonical source for `TradeEvent` and tick-bar counting;
- MBP-1 / CMBP-1;
- Definition;
- Status;
- Statistics.

Use trades, top-of-book quote state, definitions, status, and daily statistics from these feeds. MBP-1 / CMBP-1 provide top-of-book quote state. They may be used for trade extraction only when an adapter documents deterministic conversion rules and proves equivalence to the canonical `TradeEvent` contract.

TBBO / TCBBO may be added later for trade-aligned top-of-book context, for example ML features. They are not a tick-count source unless records are converted into canonical `TradeEvent` instances by documented adapter rules.

OHLCV must not be used for primary candles. Trade-Lab primary candles are tick bars built from canonical `TradeEvent` records.

## Historical Data Boundary

Historical data location is configurable with `TRADE_LAB_DATA_PATH`. When set, it should point to a `<local historical data directory>` that Trade-Lab can read for replay/historical workflows.

The configured directory may contain richer data, including local Databento-export
Parquet MBP-10 files. Replay may discover supported MBP-10 filenames/schema under
that root, including filename variants `mbp10`, `mbp-10`, `mbp_10`, and
`cmbp-10`. The catalog exposes opaque source ids only; API/WS payloads must not
expose full paths or filenames, and replay start must not accept arbitrary caller
paths. Unsupported deeper schemas such as MBO and MBP-2 through MBP-9/depth-only
remain hidden.

Trade-Lab must not build production semantics from fields unavailable in the live
v1 feed. Historical/replay and live modes must interpret events through the same
canonical event stream. MBP-10 replay therefore projects only live-compatible
fields: trade action rows (`T`/`Trade` variants) may produce `TradeEvent`, level 0
bid/ask may produce optional `TopOfBookEvent` context, and deeper book levels are
ignored as runtime features.

## Canonical Event Stream

All live and replay inputs must be normalized into canonical event types before domain processing.

### `TradeEvent`

Represents one trade message.

Canonical source: Databento Trades records are the primary source for `TradeEvent`. Adapter-derived trades from MBP-1 / CMBP-1, TBBO / TCBBO, or other schemas are valid only when documented conversion rules make them semantically equivalent to a Trades-derived `TradeEvent`.

Required fields:

- `event_ts_utc`;
- `receive_ts_utc`, if available;
- `instrument_id`, if available;
- `requested_symbol`;
- `raw_symbol`, if resolved;
- `price_ticks`;
- `size`;
- `side` or aggressor indicator, if available;
- source schema metadata.

For tick bars, one canonical `TradeEvent` counts as one tick regardless of size.

### `TopOfBookEvent`

Represents best bid/ask state.

Required fields:

- `event_ts_utc`;
- `instrument_id`, if available;
- `bid_price_ticks`, if available;
- `bid_size`, if available;
- `ask_price_ticks`, if available;
- `ask_size`, if available;
- source schema metadata.

### `InstrumentDefinitionEvent`

Represents instrument metadata and contract resolution.

Required fields:

- `event_ts_utc`;
- `instrument_id`;
- `requested_symbol`;
- `raw_symbol`;
- tick size;
- point value, if available;
- expiration/roll metadata, if available.

### `MarketStatusEvent`

Represents exchange/feed status.

Required fields:

- `event_ts_utc`;
- `instrument_id`, if available;
- status code/name;
- reason, if available.

### `DailyStatisticEvent`

Represents daily statistics supplied by the feed.

Required fields:

- `event_ts_utc`;
- `instrument_id`, if available;
- statistic type;
- price/value fields as integer ticks where price-like;
- source metadata.

## Time Semantics

- Internal event timestamps: UTC.
- Session logic timezone: `America/Chicago`.
- Frontend may display local/session time, but API payloads should carry UTC timestamps plus session/trading-day labels where relevant.

## Tick Bars

Primary candles are tick bars only.

Supported bar sizes:

- `147t`;
- `987t`;
- `2000t`.

Rules:

- One canonical `TradeEvent` equals one tick.
- Only canonical `TradeEvent` records increment tick-bar counts.
- Quote, book, status, definition, and statistic messages never increment tick bars.
- Bar OHLC is derived from trade prices only.
- Volume is the sum of trade sizes.
- A live in-progress bar must be displayed.
- A completed bar is emitted after exactly `N` canonical `TradeEvent` records for the selected tick size.
- At trading-day end, the current bar closes at the last trade even if incomplete.
- No time-based primary candles in v1.

## Data Quality and Warnings

The pipeline should surface warnings for:

- missing instrument definition;
- unresolved front-month contract;
- stale feed or no recent events;
- event timestamp regression;
- missing top-of-book fields;
- invalid or non-tick-aligned price fields;
- replay data using fields outside the live contract;
- bar finalization at trading-day end with fewer than `N` trades.

## Phase 2B API and Adapter Foundation

The WebSocket stream uses a versioned envelope:

```json
{
  "version": "ws.v1",
  "type": "system.snapshot",
  "sequence": 1,
  "server_time_utc": "2026-01-01T00:00:00Z",
  "payload": {}
}
```

Envelope versioning lets future clients reject or adapt to contract changes without
coupling to internal engine dataclasses. API DTOs remain separate from domain
objects; engines emit domain events, and the API maps those events into snapshots
or deltas.

Historical Parquet scans must select only live-compatible columns. Synthetic tests
may use Parquet fixtures, but adapters must not hardcode local raw-data paths or
promote historical-only MBP-10 depth fields into runtime features. Root/child
symlink or reparse-point traversal must be blocked, and path-like or Windows
drive-like source ids must be rejected. Unsupported schemas, missing required
columns, invalid timestamps, and non-tick-aligned prices emit sanitized
`DataQualityWarning` records without raw values, full paths, filenames,
secret-like source labels, sampled historical-only columns, or schema/column
names. Replay
`max_events` limits replay items/updates including warnings, not only
successfully processed market events.

Broadcasters must use bounded queues. Slow consumers should create explicit
backpressure/drop warnings instead of unbounded memory growth.

## Open Questions

- Exact Databento dataset names and symbol request format for production.
- Whether to retain all top-of-book updates or only latest state between trade events.
- Exact stale-feed latency thresholds for live mode.
