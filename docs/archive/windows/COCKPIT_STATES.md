# COCKPIT — states description of the strip and cards

## Trader strip (P2, `TraderStrip`)
- **Last price**: `—` until any forming/closed bar exists; then the newest print
  (close of the most recent bar across all timeframes; the forming bar wins
  wall-clock ties). On every price change the number remounts with a green
  (`flash-up`) or red (`flash-down`) background flash and keeps an `up`/`down`
  color until the next change.
- **Session net change**: `—` until a bar with `bar_index 0` for the CURRENT
  trading day is retained (exact-or-nothing: a midday join whose bar 0 was
  truncated out shows `—`, never an approximation). Otherwise signed points vs
  the day's first bar open, green/red by sign.
- **Day H/L**: `—` with no bars for the current trading day; otherwise max
  high / min low across all retained bars of that day (all timeframes).
- **Countdowns** (America/New_York wall clock, Intl-derived, DST-safe):
  - `NY open 09:30` — `in h:mm:ss` before 09:30 ET, `since h:mm:ss` after
    (blue → amber). Day-scoped: flips back to `in` at midnight ET.
  - `Flatten 16:40` — same semantics; `since` renders red.
  Both tick once per second on the wall clock (they are ops countdowns, not
  event-clock values).
- **Ops pills**: Mode/Session/Trading Day/Feed/API/WS/Heartbeat/Timeframe,
  compacted right; same placeholder semantics as before (`unavailable`, `—`).

## Collapsed ops panels (P2)
- Safe Replay / Databento render fully in every non-RUNNING state.
- On `running`: one status line — eyebrow, state chip, `N events · last HH:MM:SS`,
  `Expand` button. Expanding restores the full panel with a `Collapse` button;
  leaving RUNNING always restores the full panel and re-arms the collapse for
  the next run (local state defaults collapsed).

## Active-setup card (P3c, IntelligencePanel top)
- Hidden when no observation has `status === 'active'`.
- Visible: newest active observation — level kind + `@ price`
  (from `level_price_ticks`), authoritative `direction` badge (green long / red
  short; `direction —` for legacy null), `Touch <price>` from the originating
  touch joined by `originating_touch_id` (`—` when the touch ring missed it,
  e.g. right after a reconnect snapshot, which carries observations but no
  touches), session, and `ends in m:ss` on the EVENT clock (latest print ts;
  frozen while the feed is paused, `0:00` once the end has passed but the
  expiring trade hasn't arrived, `—` with no event clock yet).
- Multiple active observations: title shows `(N open)`, card shows the newest.

## Prediction card gate math (P3b, per prediction row)
- Renders only when a model is loaded with gate parameters AND the row's
  `modelId` equals the active model's (hot-swap safe: rows from an older model
  show no gate math rather than being judged against the wrong gate).
- Shows `<eligible-class> <prob> / <gate>` with a filled bar (green when the
  backend verdict is eligible, amber otherwise) and a threshold tick at the
  gate.
- When ineligible, a `why:` line derived client-side in priority order:
  `class — predicted X, eligible Y` → `session — s not in [..]` →
  `gate — p below g`. The backend `is_eligible` verdict is always displayed
  as-is; the WHY never overrides it.

## Levels table (P3a)
- With a last price: rows sorted by |distance|, nearest highlighted
  (`.nearest`), each row shows signed `±pts (±ticks)` (green above / red
  below), absolute price demoted to small secondary text, eligibility badge
  unchanged. Without a last price: the old fixed kind order, distances `—`.
- Runtime "Level origin" reads the NEAREST level; PDH/PDL with null origin
  render `prior day` (kind-derived), other null origins remain `unknown`.

## Chart (P4)
- Open paper position: TP (green) and SL (red) large-dashed 1px price lines,
  labeled `TP/SL <direction>`, removed the moment the position closes or a
  reset clears it.
- Session shading: translucent full-height bands (asia blue, london amber,
  ny green) behind the candles, derived from bar opens in ET; unsessioned gaps
  unshaded.
- Outcome markers sit on the RESOLUTION bar; touch/prediction markers stay on
  the touch bar.
- Timeframe tabs: the decision timeframe reads `147t · decision`.

## Trade tape (P5, EventBlotter)
- Filter chips All / Predictions / Executions / Drops (predictions =
  prediction+outcome rows; executions = position open/close; drops = drop
  rows; untyped runtime events are All-only). Empty filtered view says
  `No <filter> rows in the retained tape.`
- Typed rows: prediction (`pred` chip, class, `p 0.72`, eligible/ineligible
  badge, direction · session), outcome (`outcome` chip, correct/miss badge,
  resolution, actual class, `+15.00 / +14.75 pts` joined from the executions
  store when the tracker closed that setup — absent for untracked/ineligible
  predictions), drop (`drop` chip + reason), open (`open` chip, both entry
  columns, tp · sl), close (`close` chip, reason, both point columns, exit).
- Newest row flashes on arrival; store stays bounded at 200 events, render at
  80 rows (after filtering).
