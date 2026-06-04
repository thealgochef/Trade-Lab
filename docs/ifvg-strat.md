# AlgoChef SMC HTF FVG → Parent FVG → 1m iFVG Strategy

## Surgical Technical Specification — Strategy-Only v4.1

**Generated:** 2026-06-02 18:14 UTC  
**Audience:** AlgoChef, strategy reviewers, and trading-model validators who need the exact rule set, assumptions, caveats, validation plan, and do-not-break rules.  
**Purpose:** Define the complete strategy logic, defaults, assumptions, caveats, open questions, validation plan, and do-not-break rules for the SMC HTF FVG → Parent FVG → 1m iFVG strategy. This revision incorporates delegated reviews of strategy logic, risk/backtest mechanics, known gaps, and technical-writing clarity.

---

## 1. Executive Summary

This is a deterministic 1-minute execution strategy built around a multi-timeframe SMC sequence:

1. Price retests a valid unfilled 1H or 4H Fair Value Gap.
2. That higher-timeframe retest creates directional context only; it is not an entry by itself.
3. A same-direction parent Fair Value Gap must confirm on 3m, 5m, 10m, 15m, or 30m inside the reaction window.
4. Before parent retest, the highest valid parent timeframe wins: 30m > 15m > 10m > 5m > 3m.
5. Once 1m price retests the selected parent Fair Value Gap, the parent locks and cannot be replaced.
6. After parent lock, the strategy waits for an opposing 1m Fair Value Gap near or inside the parent zone.
7. That opposing 1m Fair Value Gap must invert by a body close through its boundary.
8. A new same-direction 1m Fair Value Gap after inversion confirms the final entry.
9. Final entry is allowed only during enabled New-York-time sessions.
10. Default stop-loss is the manipulation swing from parent retest through entry.
11. Default break-even trigger is a candle-body close through the parent internal high or low.
12. Default take-profit is fixed 1R.

Only one setup or trade is active at a time.

The compressed strategy thesis is:

> Fresh higher-timeframe imbalance retest → same-direction parent displacement → parent retest/manipulation → failed opposing 1m displacement → same-direction continuation entry.

This specification defines the trading model. It does not prove profitability. The current strategy should be treated as signal research until validated with realistic costs, robust sample testing, manual trade audit, and out-of-sample review.

---

## 2. Scope and Non-Scope

### 2.1 In scope

This document covers:

- The exact strategy sequence.
- Bullish and bearish Fair Value Gap definitions.
- Higher-timeframe, parent-timeframe, and 1m execution relationships.
- Default parameters and current tuned settings.
- State-machine lifecycle.
- Entry, invalidation, expiry, reset, stop-loss, take-profit, and break-even logic.
- Trade audit requirements.
- Known gaps and unresolved design questions.
- Caveats around backtests and execution realism.
- Validation scenarios and acceptance criteria.

### 2.2 Out of scope

This document does not cover:

- Broker integration.
- Live execution infrastructure.
- Guaranteed profitability.
- Discretionary manual overrides.
- Portfolio-level sizing.
- Production risk allocation.
- Platform-specific syntax or execution-infrastructure mechanics.

---

## 3. Strategy Thesis: Why This Model Could Have Edge

The model is not “trade every FVG.” The intended edge comes from requiring several market-structure events to occur in order.

### 3.1 Higher-timeframe imbalance provides context

A 1H or 4H Fair Value Gap is treated as evidence of prior higher-timeframe displacement. When price retests that imbalance, the zone may become a reaction area where larger participants defend, rebalance, or resume the prior displacement.

The higher-timeframe Fair Value Gap is context only. By itself, it is too broad and too early to justify entry.

### 3.2 Parent Fair Value Gap confirms lower-timeframe displacement

After the higher-timeframe retest, the strategy requires a same-direction parent Fair Value Gap on 3m, 5m, 10m, 15m, or 30m.

The parent gap is intended to show that price did not merely touch a higher-timeframe zone; it actually displaced in the intended direction after the reaction context began.

### 3.3 Parent retest prevents chasing

The strategy does not enter immediately after the parent Fair Value Gap forms. It waits for 1m price to retest the selected parent zone.

This attempts to force execution near the displacement zone rather than chasing away from it.

### 3.4 Opposing 1m FVG models the manipulation/counter-move

After the parent retest, the strategy waits for a 1m Fair Value Gap opposite the intended trade direction.

For a bullish setup, the strategy waits for a bearish 1m Fair Value Gap.  
For a bearish setup, the strategy waits for a bullish 1m Fair Value Gap.

This opposing gap is interpreted as a counter-displacement, sweep, manipulation attempt, or temporary failed move against the intended direction.

### 3.5 Inversion confirms the opposing move failed

The opposing 1m Fair Value Gap must invert by a candle-body close through its boundary.

For a bullish setup, price must close above the top of the bearish opposing gap.  
For a bearish setup, price must close below the bottom of the bullish opposing gap.

This inversion is the core iFVG concept: the opposing imbalance fails and becomes evidence for continuation in the original direction.

### 3.6 Final same-direction 1m FVG confirms renewed displacement

The strategy still does not enter immediately on inversion. It waits for a new 1m Fair Value Gap in the original direction after the inversion bar.

This final same-direction gap is the entry confirmation. It is meant to prove that price has resumed displacement after the opposing gap failed.

### 3.7 Manipulation-swing stop aligns risk with the thesis

The default stop-loss is not simply behind the entry gap. It is placed beyond the lowest low or highest high from parent retest through entry.

For a long, the stop is below the manipulation swing low.  
For a short, the stop is above the manipulation swing high.

This ties the stop to the failure point of the counter-move. If price breaches that manipulation swing, the failed-countermove thesis is likely invalid.

---

## 4. Operating Assumptions

### 4.1 Execution chart

- The strategy is designed for a 1-minute chart.
- Entries should be disabled or considered invalid on non-1-minute charts.
- Standard candles should be used for validation.
- Synthetic charts can distort Fair Value Gaps, fills, stop behavior, and backtest results.

### 4.2 Timeframes

Higher-timeframe key levels:

- 1H
- 4H

Parent timeframes:

- 3m
- 5m
- 10m
- 15m
- 30m

Execution timeframe:

- 1m

### 4.3 Session timezone

- All sessions use America/New_York.
- Sessions gate final entry only.
- Setup formation can occur outside session.
- Trade management continues after session ends.

### 4.4 Trade concurrency

- One active setup at a time.
- One open trade at a time.
- No pyramiding.
- No reversal while a position is open.

### 4.5 Data confirmation principle

Higher-timeframe and parent-timeframe structures must use confirmed candles only. The strategy should not make historical decisions using developing higher-timeframe candles.

This is essential because the model depends on ordered structural confirmation. If unconfirmed higher-timeframe candles are allowed, the historical record can show structures that were not truly known at the time.

---

## 5. Deterministic Definitions

### 5.1 Fair Value Gap

A Fair Value Gap is a three-candle imbalance.

Let:

- Candle A = two candles before the confirmation candle.
- Candle B = the middle candle.
- Candle C = the confirmation candle.

A bullish Fair Value Gap exists when Candle C’s low is above Candle A’s high by at least the minimum gap size.

A bearish Fair Value Gap exists when Candle C’s high is below Candle A’s low by at least the minimum gap size.

The middle candle helps define the three-candle structure but is not itself used as the zone boundary.

### 5.2 Bullish Fair Value Gap zone

Condition:

- Candle C low > Candle A high.
- Gap size = Candle C low minus Candle A high.
- Gap size must be at least the minimum Fair Value Gap size.

Zone:

- Top = Candle C low.
- Bottom = Candle A high.

Full fill:

- Price reaches or crosses the bottom of the zone.

### 5.3 Bearish Fair Value Gap zone

Condition:

- Candle C high < Candle A low.
- Gap size = Candle A low minus Candle C high.
- Gap size must be at least the minimum Fair Value Gap size.

Zone:

- Top = Candle A low.
- Bottom = Candle C high.

Full fill:

- Price reaches or crosses the top of the zone.

### 5.4 Retest / touch

A retest occurs when the 1m candle’s wick range overlaps a zone.

For any zone:

- The candle high must be at or above the zone bottom.
- The candle low must be at or below the zone top.

This is a wick-overlap rule. It does not require a body close, rejection candle, or minimum penetration unless added later.

### 5.5 Full fill

A full fill means price has reached the far side of the Fair Value Gap zone.

For bullish zones:

- Full fill occurs when price trades to or below the zone bottom.

For bearish zones:

- Full fill occurs when price trades to or above the zone top.

### 5.6 Overlap and distance

Two zones overlap when their vertical price intervals intersect.

If zones overlap, their distance is zero.

If they do not overlap, distance is the nearest vertical gap between the two intervals.

This distance logic is used for:

- Parent Fair Value Gap proximity to the selected higher-timeframe zone.
- Opposing 1m Fair Value Gap proximity to the parent zone.
- Optional final entry Fair Value Gap proximity to the parent zone.

### 5.7 Inversion

A Fair Value Gap inversion occurs when price closes through the boundary of the selected opposing gap.

Bullish setup:

- The selected opposing gap is bearish.
- Inversion requires a body close above the opposing gap top.

Bearish setup:

- The selected opposing gap is bullish.
- Inversion requires a body close below the opposing gap bottom.

The strategy uses body close for inversion, not wick touch.

### 5.8 Parent internal high / low

The parent internal high and low are measured across the three parent-timeframe candles that formed the parent Fair Value Gap.

For long break-even logic:

- Parent internal high = highest high of the three parent candles.

For short break-even logic:

- Parent internal low = lowest low of the three parent candles.

### 5.9 Manipulation swing

The manipulation swing is the highest high or lowest low from parent retest through final entry.

For a long:

- Manipulation swing low = lowest 1m low from parent retest through entry.

For a short:

- Manipulation swing high = highest 1m high from parent retest through entry.

Default stop-loss is placed beyond this swing with a buffer.

---

## 6. Current Tuned Defaults

These are the current v4.1 tuned defaults. They should be preserved unless AlgoChef explicitly retunes them.

### 6.1 Direction and timeframes

| Parameter | Current default | Strategic meaning |
|---|---:|---|
| Enable longs | Yes | Long setups are allowed. |
| Enable shorts | Yes | Short setups are allowed. |
| Use 1H higher-timeframe FVGs | Yes | 1H zones can create context. |
| Use 4H higher-timeframe FVGs | Yes | 4H zones can create context and outrank 1H on same-candle taps. |
| Use 3m parent FVGs | Yes | 3m parent structures can qualify. |
| Use 5m parent FVGs | Yes | 5m parent structures can qualify. |
| Use 10m parent FVGs | Yes | 10m parent structures can qualify. |
| Use 15m parent FVGs | Yes | 15m parent structures can qualify. |
| Use 30m parent FVGs | Yes | 30m parent structures can qualify and have highest parent priority. |

### 6.2 Core structure settings

| Parameter | Current default | Strategic meaning |
|---|---:|---|
| Minimum Fair Value Gap size | 8 ticks | Filters out tiny gaps/noise. |
| Parent reaction window | 24 parent bars | Parent FVG must confirm within this window after HTF tap. |
| Parent priority | 30m > 15m > 10m > 5m > 3m | Higher parent timeframe wins before lock. |
| Parent must be near selected HTF zone | Yes | Parent must be vertically tied to HTF context. |
| Parent ↔ HTF max distance | 80 ticks | Max allowed distance if parent/HTF do not overlap. |
| Invalidate parent on 1m full fill before entry | Yes | Parent cannot be fully mitigated before entry. |
| Opposing 1m FVG proximity to parent | 80 ticks | Opposing execution gap must be near/inside parent. |
| Entry FVG must be near parent | No | Final entry gap can occur away from parent under current default. |
| Max bars after inversion to find entry FVG | 30 bars | Post-inversion entry search expires after 30 1m bars. |
| Max active HTF FVG records | 1 | Strategy behaves like a freshest-retained-HTF filter, with caveats. |

### 6.3 Risk settings

| Parameter | Current default | Strategic meaning |
|---|---:|---|
| Stop-loss model | Manipulation swing | Stop is beyond parent-retest-through-entry sweep area. |
| Stop-loss buffer | 1 tick | Stop is placed slightly beyond the raw swing/zone. |
| Take-profit | 1R | Fixed one-risk-unit target. |
| Break-even trigger mode | Body close | BE requires candle-body confirmation, not just wick touch. |
| Break-even level for longs | Parent internal high | Long BE after price reclaims parent formation high. |
| Break-even level for shorts | Parent internal low | Short BE after price reclaims parent formation low. |

### 6.4 Sessions

Timezone: America/New_York

| Session | Current default | Entry allowed? |
|---|---:|---:|
| Asia | 16:00–01:45 | Yes |
| London | 02:00–07:00 | Yes |
| New York | 08:00–14:00 | Yes |

Session rule:

- Sessions gate final entry only.
- Higher-timeframe scanning, parent formation, parent retest, opposing gap selection, inversion, and trade management can occur outside these windows.
- Open trades remain managed after session end.

---

## 7. End-to-End Setup Lifecycle

### 7.1 Phase 1 — Scan higher-timeframe Fair Value Gaps

The strategy continuously tracks confirmed 1H and 4H Fair Value Gaps.

A higher-timeframe gap can activate a setup only if:

1. It is confirmed.
2. It has not fully filled before activation.
3. It is touched/retested by the current 1m wick.
4. Its direction is allowed by the long/short toggles.

Priority rules:

- If 1H and 4H are touched on the same 1m candle, 4H wins.
- If bullish and bearish zones of the winning higher-timeframe rank are touched on the same candle, activation is skipped as conflicted.
- If multiple same-direction candidates exist within the same rank, selection uses the most relevant/tie-broken candidate according to the retained-record logic.

Important current behavior:

- Max active HTF records defaults to 1.
- This means the model is not currently trying to trade every unfilled 1H/4H imbalance.
- It is closer to trading the freshest retained higher-timeframe imbalance, but active setup retention can create edge cases where an older selected zone remains while newer zones are ignored.

Transition:

- Valid HTF retest → parent search begins.

### 7.2 Phase 2 — Search for same-direction parent Fair Value Gap

After higher-timeframe activation, the strategy waits for a same-direction parent Fair Value Gap on one of these timeframes:

- 3m
- 5m
- 10m
- 15m
- 30m

Parent requirements:

1. Parent direction must match the selected higher-timeframe direction.
2. Parent Fair Value Gap must confirm after the higher-timeframe tap context begins.
3. Parent confirmation must occur inside the parent reaction window.
4. By default, parent zone must be within 80 ticks of the selected higher-timeframe zone if it does not overlap.
5. Parent must not be fully filled before entry.
6. Parent must not structurally invalidate before entry.

Priority before parent lock:

- Highest enabled parent timeframe wins.
- 30m outranks 15m.
- 15m outranks 10m.
- 10m outranks 5m.
- 5m outranks 3m.
- Within the same parent timeframe, newest confirmed parent candidate wins.

Current caveat:

- Parent confirmation after HTF tap does not necessarily mean all three candles forming that parent Fair Value Gap began after the HTF tap. If AlgoChef wants strict causal formation, this must be tightened.

Transition:

- Valid parent candidate selected → wait for 1m parent retest.

### 7.3 Phase 3 — Parent retest and lock

Parent lock occurs when 1m price retests the selected parent Fair Value Gap after the parent has become available.

Parent lock requirements:

1. Selected parent exists.
2. 1m wick overlaps the parent zone.
3. Retest occurs after the parent candidate has been selected.
4. Parent has not fully filled or invalidated before lock.

Once parent locks:

- The parent cannot be replaced by a higher timeframe parent.
- The parent becomes the execution zone for the rest of the setup.
- Manipulation swing tracking begins.
- Opposing 1m Fair Value Gap search begins after the retest bar.

Parent invalidation before entry:

- Bullish parent invalidates if parent-timeframe candle closes below the parent bottom.
- Bearish parent invalidates if parent-timeframe candle closes above the parent top.
- Bullish parent fully fills if 1m price trades to or below the parent bottom.
- Bearish parent fully fills if 1m price trades to or above the parent top.

If parent invalidates or fully fills before entry:

- Provisional parent is removed if not locked.
- Locked parent resets the setup.

After entry:

- Parent structural invalidation is ignored.
- Trade management is governed only by stop-loss, take-profit, and break-even.

### 7.4 Phase 4 — Opposing 1m Fair Value Gap selection

After parent lock, the strategy waits for an opposing 1m Fair Value Gap near or inside the locked parent.

Bullish setup:

- Wait for bearish 1m Fair Value Gap.

Bearish setup:

- Wait for bullish 1m Fair Value Gap.

Eligibility:

- Opposing gap must form after parent retest context begins.
- Opposing gap must overlap the parent zone or be within 80 ticks by default.
- The strategy must check whether the currently selected opposing gap inverted before replacing it with a newer opposing gap.

Current caveat:

- The selected opposing 1m Fair Value Gap can be confirmed after parent retest while part of its three-candle structure may have started before the parent retest. If strict post-retest formation is required, this must be tightened.

Transition:

- Eligible opposing 1m Fair Value Gap selected → wait for inversion.

### 7.5 Phase 5 — Inversion

The selected opposing 1m Fair Value Gap must invert by body close.

Bullish setup:

- Opposing gap is bearish.
- Inversion requires close above opposing gap top.

Bearish setup:

- Opposing gap is bullish.
- Inversion requires close below opposing gap bottom.

Rules:

- Wick through the boundary is not enough.
- Body close is required.
- Inversion does not immediately trigger entry.
- Final entry cannot occur on the inversion candle.

Current caveat:

- There is no hard timeout from opposing gap selection to inversion. A stale opposing gap can remain active for an extended period unless a newer eligible opposing gap replaces it.

Transition:

- Body-close inversion → wait for final same-direction 1m Fair Value Gap.

### 7.6 Phase 6 — Final entry Fair Value Gap

After inversion, the strategy waits for a new 1m Fair Value Gap in the original setup direction.

Bullish setup:

- New bullish 1m Fair Value Gap confirms the long entry.

Bearish setup:

- New bearish 1m Fair Value Gap confirms the short entry.

Entry requirements:

1. Final entry gap occurs after the inversion bar.
2. Entry occurs during an enabled New-York-time session.
3. Chart is 1m.
4. No position is open.
5. Risk is at least one tick.
6. Entry does not violate active setup/trade constraints.
7. Post-inversion max bars has not expired.

Current default:

- Final entry gap does not need to be near the parent zone.

This is a major strategic choice. It gives the strategy more freedom to enter after confirmation but can also allow entries far away from the parent structure.

Outside-session behavior:

- A final entry gap outside the enabled session does not trigger a trade.
- It should not be queued for a later session.
- Current behavior should be trade-audited to ensure outside-session final entry events do not confuse setup identity.

Transition:

- Valid final entry → trade management.

### 7.7 Phase 7 — Trade management and reset

Once in trade:

- Structural HTF and parent invalidation are ignored.
- Stop-loss, take-profit, and break-even control the trade.
- Stop-loss and take-profit remain active after session ends.
- Break-even logic remains active after session ends.
- After the position closes, the strategy resets to scanning mode.

HTF reuse:

- Higher-timeframe Fair Value Gaps are not automatically consumed after a completed trade.
- If still retained and unfilled, they may become eligible again.
- This is an important design choice and should be reviewed.

---

## 8. Bullish vs Bearish Rule Table

| Component | Bullish setup | Bearish setup |
|---|---|---|
| Higher-timeframe context | Bullish 1H/4H FVG retested | Bearish 1H/4H FVG retested |
| Parent FVG | Bullish parent FVG | Bearish parent FVG |
| Parent priority | Highest valid parent TF wins before lock | Highest valid parent TF wins before lock |
| Parent lock | 1m wick retests bullish parent zone | 1m wick retests bearish parent zone |
| Opposing 1m FVG | Bearish 1m FVG near/inside parent | Bullish 1m FVG near/inside parent |
| Inversion | Body close above opposing FVG top | Body close below opposing FVG bottom |
| Final entry confirmation | New bullish 1m FVG after inversion | New bearish 1m FVG after inversion |
| Default SL | Lowest 1m low from parent retest through entry minus buffer | Highest 1m high from parent retest through entry plus buffer |
| Default TP | 1R | 1R |
| BE trigger | Body close above parent internal high | Body close below parent internal low |
| Parent full-fill invalidation | 1m low reaches/crosses parent bottom before entry | 1m high reaches/crosses parent top before entry |
| Parent structural invalidation | Parent close below parent bottom before entry | Parent close above parent top before entry |

---

## 9. State-Machine Summary

| Phase | Objective | Key transition | Reset / failure |
|---|---|---|---|
| Scan HTF | Find unfilled 1H/4H FVG retest | Valid HTF retest activates setup | HTF full fill, conflict, no valid tap |
| Parent search | Find same-direction parent FVG | Valid parent selected | Parent windows expire, selected HTF fills |
| Parent retest / lock | Wait for 1m retest of parent | Parent locks | Parent invalidates/fills before entry |
| Opposing 1m FVG | Find counter-direction gap near parent | Opposing gap selected | No hard timeout currently |
| Inversion | Confirm opposing gap failure | Body close through opposing boundary | No hard timeout currently |
| Final entry FVG | Confirm same-direction continuation | Valid entry during session | 30-bar post-inversion expiry, invalid risk, outside session |
| In trade | Manage SL/TP/BE | Position closes | SL/TP/BE outcome |

---

## 10. Invalidation, Expiry, and Reset Rules

| Stage | Condition | Current behavior |
|---|---|---|
| HTF scan | Higher-timeframe FVG fully filled before activation | Remove/ignore; no setup activation |
| HTF scan | Same-rank bullish and bearish HTF zones touched on same candle | Skip activation as conflicted |
| HTF active setup | Selected HTF fully fills before entry | Reset setup |
| Parent search | All enabled parent reaction windows expire without valid parent | Reset setup |
| Parent candidate | Parent fully filled by 1m price before lock | Candidate removed |
| Locked parent | Parent fully filled by 1m price before entry | Reset setup |
| Parent candidate or locked parent | Parent-timeframe close beyond zone before entry | Remove/reset depending on lock state |
| Opposing 1m FVG selection | No opposing gap appears | No hard timeout currently |
| Inversion wait | Opposing gap does not invert | No hard timeout currently |
| Final entry wait | No same-direction entry gap within 30 bars after inversion | Reset setup |
| Entry validation | Risk less than one tick | Reset / no trade |
| Entry validation | Final entry gap outside enabled session | No entry; should not queue signal |
| Trade management | Stop-loss, take-profit, or break-even-adjusted stop reached | Close trade and reset |

---

## 11. Risk Management

### 11.1 Entry price assumption

The strategy models entry at the final 1m Fair Value Gap confirmation close.

Caveat:

- Live execution may not fill exactly at the confirmation close.
- Slippage, spread, queueing, and broker execution can materially affect results.
- Backtest results should not be interpreted as live fills unless cost and fill assumptions are modeled.

### 11.2 Default stop-loss: manipulation swing

Long setup:

- Stop-loss = lowest 1m low from parent retest through entry minus 1 tick buffer.

Short setup:

- Stop-loss = highest 1m high from parent retest through entry plus 1 tick buffer.

This is the preferred default because it anchors invalidation to the manipulation/countermove structure rather than a narrow final-entry gap.

### 11.3 Alternate stop-loss anchors

The model has conceptual room for alternate stop anchors:

- Parent Fair Value Gap boundary.
- Higher-timeframe Fair Value Gap boundary.
- Final entry Fair Value Gap boundary.

These should be treated as separate strategy variants, not minor cosmetic settings, because each changes win rate, R distribution, trade frequency, and invalidation meaning.

### 11.4 Stop-loss buffer

Current default buffer:

- 1 tick.

Purpose:

- Place stop just beyond the structural level instead of exactly on it.

Open question:

- Whether buffer should be static ticks, instrument-specific, or volatility-adjusted.

### 11.5 Take-profit

Current default:

- Fixed 1R.

Implication:

- After costs, the system needs a materially high win rate or strong break-even management to maintain edge.
- Fixed 1R may cap large winners.
- If the signal quality is noisy, fixed 1R can make the strategy look structurally unattractive.

Variants worth testing:

- 1.5R or 2R fixed targets.
- Partial at 1R and runner.
- Liquidity target.
- Parent/high-low target.
- Session high/low target.
- Time stop.
- Trail after break-even.

### 11.6 Break-even

Current default:

- Break-even triggers by candle-body close through the parent internal high or low.

Long:

- Move stop to entry after a later body close above parent internal high.

Short:

- Move stop to entry after a later body close below parent internal low.

Rules:

- Break-even should not activate on the entry candle.
- Break-even should not be session-gated.
- Once triggered, stop moves to entry.

Open questions:

- Should BE use strict close beyond the level or inclusive close at/through the level?
- Should wick-touch BE be allowed as a separate variant?
- Should BE be optional/off for testing expectancy?
- Should BE move to true entry, entry plus costs, or entry plus tick buffer?

### 11.7 Position sizing caveat

Current results should not be judged only by raw net profit unless position sizing is explicitly normalized.

The more useful performance unit is R-multiple expectancy:

- Average R per trade.
- Median R.
- Win rate.
- Loss rate.
- Break-even frequency.
- Average win / average loss.
- Drawdown in R.
- Performance by session, direction, HTF, and parent timeframe.

Without risk-normalized analysis, wide-stop trades can dominate results differently than tight-stop trades.

---

## 12. Trade Audit Requirements

Trade audit is not cosmetic. For a discretionary SMC model converted into deterministic rules, the trader must be able to verify that the deterministic model is identifying the same structures the discretionary model expects.

The strategy should expose enough information to answer these questions on any completed trade:

1. Which higher-timeframe Fair Value Gap was tapped?
2. Was it 1H or 4H?
3. Was it bullish or bearish?
4. Which parent Fair Value Gap was selected?
5. Which parent timeframe won?
6. Did a higher parent timeframe replace a lower one before lock?
7. Where did parent lock occur?
8. Which opposing 1m Fair Value Gap was selected?
9. Where did inversion occur?
10. Which final same-direction 1m Fair Value Gap triggered entry?
11. What stop-loss model was active?
12. What was the exact stop-loss level before entry?
13. What was the take-profit level?
14. What was the break-even trigger level?
15. Did BE trigger, and if so, where?
16. Why did the setup reset if no trade occurred?

### 12.1 Required audit evidence

The audit display should make the following obvious:

- Active higher-timeframe zone.
- Selected parent zone.
- Parent locked state.
- Opposing 1m Fair Value Gap.
- Inverted opposing gap.
- Final entry gap.
- Entry price marker.
- Stop-loss marker.
- Take-profit marker.
- Break-even trigger marker.
- Setup ID.
- Last important event.
- Current phase.
- Direction.
- Session state.

### 12.2 Readability requirement

The status display and event markers must be readable on a white chart by default.

This matters because AlgoChef usually uses a white chart background. Low-contrast tables or transparent cells can make the strategy impossible to audit even if the trade logic is correct.

Recommended status-display fields:

- Setup ID.
- Phase.
- Direction.
- Higher-timeframe source.
- Higher-timeframe zone ID/time.
- Parent timeframe.
- Parent state.
- Opposing 1m gap state.
- Inversion state.
- Session state.
- Stop-loss model.
- Pending stop-loss.
- Active stop-loss.
- Entry price.
- Take-profit.
- Break-even trigger.
- Last event.
- Chart/timeframe validity.

### 12.3 Historical audit requirement

Completed trades should remain auditable during debugging.

If historical setup evidence disappears immediately on reset, it becomes impossible to diagnose whether a losing trade was caused by:

- Incorrect HTF selection.
- Parent chosen too far from HTF zone.
- Bad parent tie-break.
- Opposing gap replacement issue.
- Same-bar sequence bug.
- Entry outside intended context.
- Stop-loss anchor mismatch.
- Session gating mismatch.

A completed-trade audit history, capped to avoid clutter, is required for serious debugging.

---

## 13. What Makes the Strategy Work

The strategy only makes sense if the following are true:

### 13.1 The higher-timeframe zone matters

The selected 1H/4H Fair Value Gap must represent a meaningful imbalance, not random noise. If the HTF gap is stale, irrelevant, or already functionally mitigated, the rest of the chain can be structurally clean but low quality.

### 13.2 The parent gap must be causally tied to the HTF reaction

The parent Fair Value Gap should be evidence of reaction after HTF retest. If it is merely nearby in price but not causally connected, the setup can become a loose coincidence chain.

### 13.3 Parent retest must be meaningful

A wick overlap is easy to satisfy. The model assumes that a wick retest of the parent zone is enough to represent a return to the displacement area. If many bad trades occur, this may need tightening with depth, rejection, body close, or time constraints.

### 13.4 The opposing gap must represent true failed counter-displacement

The opposing 1m Fair Value Gap is the execution trigger’s core. It should represent a real counter-move, not just a tiny mechanical imbalance.

The 8-tick minimum FVG filter helps, but it may not be enough by itself.

### 13.5 Inversion must happen in the right context

A body-close inversion of the opposing gap should happen while the setup context remains fresh. If inversion occurs too late, the original parent/HTF thesis may no longer matter.

### 13.6 Final entry should not chase too far

Current default allows the final entry Fair Value Gap to be away from the parent. This improved results in current tuning, but it can also detach the entry from the original displacement zone.

This should be treated as a major strategic choice, not a minor setting.

### 13.7 Stop-loss must reflect true invalidation

The manipulation-swing stop is logical because it invalidates the failed-countermove thesis. If the stop is too tight, good setups may stop out before continuation. If too wide, the fixed 1R target may become unattractive.

### 13.8 Break-even must not suffocate expectancy

Break-even can reduce losses but also flatten winners into zero outcomes. Since break-even trades are not truly free after costs, BE rules need separate analysis.

---

## 14. Known Gaps, Risks, and Failure Modes

### 14.1 Causality gap: confirmed after vs fully formed after

This is one of the most important unresolved issues.

Current behavior generally requires structure confirmation after the prior trigger. But a three-candle Fair Value Gap confirmed after a trigger can still include candles that began before the trigger.

This matters in three places:

1. Parent Fair Value Gap after HTF tap.
2. Opposing 1m Fair Value Gap after parent retest.
3. Final entry Fair Value Gap after inversion.

Example issue:

- Parent retest occurs on bar N.
- Opposing 1m FVG confirms on bar N+1.
- That opposing FVG may use bar N-1 as Candle A, meaning part of the structure existed before the parent retest.

Decision required:

- Is confirmation after the trigger enough?
- Or must the entire three-candle structure form after the trigger?

This question can materially alter trade count and performance quality.

### 14.2 HTF record cap changes the thesis

Current default keeps max active higher-timeframe records at 1.

This is not just a storage/performance setting. It changes the strategy universe.

Potential benefits:

- Filters stale zones.
- Reduces clutter.
- Forces focus on the freshest retained HTF imbalance.

Potential problems:

- Older unfilled HTF zones are ignored even if still relevant.
- Same-rank bull/bear conflicts may be hidden if only one record is retained.
- During an active setup/trade, newer HTF zones may appear but be discarded while the selected older zone is preserved.
- After reset, the model may reuse the older selected zone while fresher zones formed during the active trade were never retained.

Decision required:

- Should the strategy intentionally trade only the freshest retained HTF Fair Value Gap?
- Or should it track more HTF zones and apply a separate ranking rule?

### 14.2A HTF maintenance order and reset-bar event risk

Delegated logic review identified two related maintenance risks that should be treated as validation items, not cosmetic issues.

First, if a trade closes and the strategy resets on the same candle that a new higher-timeframe Fair Value Gap confirms, the new event can be skipped if timestamp bookkeeping advances before the post-reset scan consumes it. The same class of issue can allow a filled retained higher-timeframe zone to remain available if fill maintenance is skipped during a reset bar.

Second, if the higher-timeframe record cap is applied before full-fill filtering, then with `max active HTF records = 1` a newly confirmed but immediately filled HTF gap can evict an older valid unfilled gap, after which the new filled gap is removed. In that case, the strategy unintentionally loses the older valid context.

Decision required:

- Should the retained HTF universe mean the freshest raw HTF gap, or the freshest unfilled/active HTF gap?
- Should full-fill filtering occur before record capping?
- Should reset bars still perform HTF registration and fill maintenance before the setup state is cleared?
- Should a regression test cover trade-close bars where new HTF gaps confirm or retained HTF gaps fill?

### 14.3 Setup staleness

Current hard expiry exists after inversion only:

- If no final entry Fair Value Gap appears within 30 bars after inversion, setup resets.

But there is no hard timeout for:

- Parent candidate waiting for 1m retest.
- Parent lock waiting for opposing 1m Fair Value Gap.
- Opposing 1m Fair Value Gap waiting for inversion.

This can create stale setups that block newer, cleaner opportunities.

Decision required:

- Should every phase have a max age?
- Should timeouts be measured in 1m bars, session time, parent bars, or volatility-adjusted time?

### 14.4 Wick-overlap retest may be too loose

HTF tap and parent retest currently use wick overlap.

Potential issue:

- A single tick into a zone qualifies as a retest.
- There is no required rejection close.
- There is no required minimum penetration.
- There is no required reaction magnitude.

This may allow many low-quality mechanical setups.

Decision required:

- Should retest require any wick overlap, minimum penetration, midpoint touch, body close behavior, rejection candle, or displacement after touch?

### 14.5 Parent proximity is vertical, not fully causal

The parent ↔ HTF relationship currently uses vertical price distance/overlap.

This ensures the parent is near the HTF zone but does not fully prove causal reaction.

Potential issue:

- A parent FVG can be close in price but not structurally caused by the HTF retest.

Decision required:

- Should parent require overlap with the HTF zone?
- Should parent formation require price to first touch HTF and then displace from that same reaction leg?
- Should parent require its entire three-candle structure after HTF tap?

### 14.6 Entry locality disabled by default

Current default:

- Final entry Fair Value Gap does not need to be near the parent.

Potential benefit:

- Allows continuation entries after price leaves the parent area.
- May improve trade count and current tuned performance.

Potential problem:

- Entry can become disconnected from the original parent zone.
- Stop may be wide relative to entry.
- Fixed 1R may become less attractive if entry chases.

Decision required:

- Should final entry gap have its own proximity rule?
- Should it be allowed away from parent only if risk/reward remains acceptable?

### 14.7 Opposing FVG replacement may alter intent

Before inversion, newer eligible opposing gaps can replace older ones.

Potential benefit:

- Uses the freshest counter-displacement.

Potential problem:

- The first opposing move may be the true manipulation.
- Replacing it can skip an inversion that would have mattered.
- If replacement happens before inversion check, signals can be missed; the current invariant is to check inversion first.

Decision required:

- Should the first eligible opposing gap lock until inversion/expiry?
- Or should the newest eligible opposing gap keep replacing until inversion?

### 14.8 Outside-session final entry behavior

Sessions gate final entry only. Setup formation and inversion can happen outside session.

If a final entry gap appears outside session:

- It does not trigger a trade.
- It should not be queued for later.
- The setup may continue waiting until the 30-bar post-inversion expiry.

Potential issue:

- The audit display may show an outside-session entry event even though no trade was taken.
- The trader may think the strategy missed or delayed an entry.

Decision required:

- Should outside-session final entry gaps reset the setup?
- Should they be ignored while setup remains alive?
- Should they be visible but clearly marked “outside session / ignored”?

### 14.9 Same-bar event ambiguity

Historical 1m candles cannot reveal true intrabar order.

Ambiguous cases:

- HTF tap and HTF full fill on same candle.
- Parent retest and parent full fill on same candle.
- BE trigger and stop-loss on same candle.
- Stop-loss and take-profit on same candle.
- Inversion and final entry gap on same candle.

Current conservative rules include:

- Same-candle HTF tap + full fill should not activate.
- Entry cannot occur on the inversion candle.
- BE should not trigger on the entry candle.

But full intrabar ordering remains unknowable from standard 1m historical bars.

### 14.10 Backtest realism gaps

Current backtests should be treated carefully because they may not include:

- Realistic commission.
- Slippage.
- Spread.
- Liquidity/queue effects.
- Live fill drift from confirmation close.
- Risk-normalized sizing.
- News/event filters.
- True intrabar event ordering.

A strategy can look acceptable before costs and become poor after costs, especially with 1R targets and break-even exits.

### 14.11 Fixed 1R target may be structurally limiting

A fixed 1R take-profit requires solid win rate after costs. If break-even trades become small live losers and slippage worsens entries/exits, the required win rate rises.

Potential issue:

- If the strategy catches real displacement, fixed 1R may cut off the best part of the edge.
- If the strategy is noisy, fixed 1R may not compensate for full losses.

Decision required:

- Should fixed 1R remain the base case?
- Should the strategy test larger R targets or liquidity-based exits?

### 14.11A Break-even trigger may already be crossed at entry

The default break-even trigger is the parent internal high for longs and parent internal low for shorts. In some valid sequences, price may already be beyond that level by the time the final entry Fair Value Gap confirms.

Current risk discipline should prevent BE from activating on the entry candle. But if price remains beyond the trigger on the next candle, the stop can move to entry almost immediately. That may be desired protection, or it may suffocate trades before the intended displacement has enough room to continue.

Decision required:

- Should BE require a fresh post-entry cross through the trigger?
- Or is it acceptable for BE to activate on the first post-entry candle if price is already beyond the trigger?
- Should BE have an off mode for expectancy comparison?

### 14.12 No discretionary quality filters

The deterministic model currently does not include many discretionary filters that an SMC trader might use:

- Higher-timeframe trend context.
- Market structure break or change of character.
- Liquidity sweep validation.
- Premium/discount location.
- News avoidance.
- Volume confirmation.
- Displacement candle quality.
- Body-to-wick ratio.
- Time-of-day quality beyond broad sessions.
- HTF age/freshness beyond retained-record behavior.

This may explain why the strategy can be mechanically correct but unprofitable.

---

## 15. Important Questions To Resolve

The questions below are split by theme. The highest-priority questions before further tuning are: causality, stale-state timeouts, HTF record retention, entry locality, cost-aware risk model, and whether BE should require a fresh post-entry cross.

### 15.1 Causality

1. Should the entire parent Fair Value Gap structure form after HTF tap, or is post-tap confirmation sufficient?
2. Should the entire opposing 1m Fair Value Gap structure form after parent retest, or is post-retest confirmation sufficient?
3. Should the entire final entry Fair Value Gap structure form after inversion, or is post-inversion confirmation sufficient?

### 15.2 Higher-timeframe selection

4. Should max active HTF records remain 1?
5. Should the strategy track multiple unfilled HTF zones and rank them separately?
6. Should older unfilled HTF zones remain tradable?
7. Should HTF zones be consumed after a completed trade?
8. Should 4H always outrank 1H, or should nearest/ freshest zone sometimes win?

### 15.3 Parent logic

9. Should parent FVG require overlap with the selected HTF zone instead of just max distance?
10. Should parent retest require wick touch only, or body/rejection confirmation?
11. Should parent retest expire after a max number of 1m bars?
12. Should parent full fill invalidate every time, or can deep mitigation still be valid?
13. Should same-timeframe parent tie-break remain newest, or should closest-to-HTF win?

### 15.4 Execution logic

14. Should first eligible opposing 1m FVG lock, or should newest eligible continue replacing?
15. Should opposing FVG selection expire if inversion takes too long?
16. Should entry FVG require proximity to parent?
17. Should outside-session final entry FVG reset the setup, be ignored, or be marked but not queued?
18. Should entry require additional displacement quality beyond the FVG itself?

### 15.5 Risk logic

19. Should manipulation-swing SL remain default across all instruments?
20. Should SL buffer be instrument-specific or volatility-adjusted?
21. Should TP remain fixed 1R?
22. Should BE be body close, wick touch, disabled, or adaptive?
23. Should BE move to entry, entry plus costs, or entry plus buffer?
24. Should position sizing be fixed contracts or fixed account risk?
25. Should BE require a fresh post-entry trigger rather than being allowed when the trigger was already crossed by entry?

### 15.6 Validation

26. What symbol and session are primary: NQ, ES, other futures, FX, crypto?
27. What sample period is considered sufficient?
28. What performance metric matters most: profit factor, max drawdown, R expectancy, win rate, or stability?
29. How much degradation under costs/slippage is acceptable?
30. Does performance survive walk-forward and parameter perturbation?
31. Does each segment contribute, or is the entire edge concentrated in one fragile subgroup?

---

## 16. Validation Plan

The strategy should not be judged only by one aggregate backtest. It needs staged validation.

### 16.1 Manual trade audit

Audit at least:

- 50 completed trades.
- 50 failed or abandoned setups.
- A mix of longs and shorts.
- A mix of Asia, London, and New York sessions.
- A mix of 1H and 4H contexts.
- A mix of parent timeframes.

For each trade, confirm:

1. HTF FVG existed and was unfilled before tap.
2. HTF selection matched priority/conflict rules.
3. Parent FVG direction matched HTF direction.
4. Parent formed within reaction window.
5. Parent was near the HTF zone under current proximity rules.
6. Parent lock occurred after parent became available.
7. Parent was not fully filled before entry.
8. Opposing 1m FVG was near/inside parent.
9. Opposing FVG inversion used body close.
10. Final entry FVG occurred after inversion.
11. Entry occurred during enabled session.
12. Stop-loss matched selected SL model.
13. TP and BE levels were correct.
14. Reset reason was clear for abandoned setups.

### 16.2 Logic regression scenarios

Create test cases for:

- 1H and 4H tap on same candle: 4H wins.
- Bullish and bearish same-rank HTF conflict: skip activation.
- HTF tap and full fill on same candle: no activation.
- Trade-close/reset candle where a new HTF gap confirms: new HTF event is not silently lost.
- Trade-close/reset candle where retained HTF gap fully fills: filled HTF zone is not reused.
- New immediately filled HTF gap with max active HTF records = 1: confirm whether older valid unfilled gap should be preserved or intentionally evicted.
- Parent candidate replaced by higher parent TF before lock.
- Parent candidate cannot be replaced after lock.
- Same-timeframe parent tie: newest wins.
- Parent full fill before entry resets/removes.
- Parent close beyond zone invalidates before entry.
- Opposing FVG inversion checked before replacement.
- Inversion cannot create same-candle entry.
- Entry after post-inversion expiry: no trade.
- Outside-session final entry gap: no trade.
- BE cannot trigger on entry candle.
- BE trigger already crossed by entry: confirm whether BE activates on the next candle or requires a fresh post-entry cross.
- SL/TP/BE remain active after session end.

### 16.3 Causality tests

Explicitly compare current behavior vs strict full-after rules:

1. Parent FVG confirms after HTF tap but first candle began before HTF tap.
2. Opposing 1m FVG confirms after parent retest but uses a pre-retest candle.
3. Entry FVG confirms after inversion but uses a pre-inversion candle.

For each, record:

- Trade count difference.
- Win rate difference.
- Profit factor difference.
- R expectancy difference.
- Audit quality difference.

### 16.4 Parameter robustness tests

Run controlled sweeps around current defaults:

| Parameter | Suggested values |
|---|---|
| Minimum FVG size | 4, 6, 8, 10, 12 ticks |
| Parent ↔ HTF distance | 40, 80, 120, 160 ticks |
| Opposing FVG proximity | 40, 80, 120, 160 ticks |
| Entry FVG near parent | On / Off |
| Max bars after inversion | 10, 20, 30, 45, 60 |
| Max HTF records | 1, 2, 5, 20 |
| BE mode | Body close, wick touch, off |
| TP multiple | 1R, 1.5R, 2R |
| SL model | Manipulation swing, parent boundary, HTF boundary, entry FVG boundary |

A robust model should not depend on one razor-thin parameter combination unless the edge is intentionally narrow and well understood.

### 16.5 Segment analysis

Report performance by:

- Long vs short.
- Asia vs London vs New York.
- 1H vs 4H context.
- Parent timeframe.
- HTF age.
- Parent wait time.
- Parent lock to opposing FVG wait time.
- Opposing FVG to inversion wait time.
- Inversion to entry wait time.
- Entry distance from parent.
- BE triggered vs not triggered.
- Day of week.
- High-volatility vs low-volatility regimes.

This is necessary to identify whether the edge is broad or concentrated in a fragile subset.

### 16.6 Cost and execution testing

Test with realistic assumptions:

- Commission.
- Slippage.
- Spread.
- Worse entry fill than confirmation close.
- Worse exit fill than ideal stop/target.
- Break-even trades becoming small net losers.

If a 1R strategy only works with perfect fills and no costs, it is not live-ready.

### 16.7 Walk-forward validation

Suggested process:

1. Tune on one period.
2. Freeze settings.
3. Test on a later unseen period.
4. Repeat across different volatility regimes.
5. Check whether results degrade gradually or collapse.

Acceptance:

- Stable R expectancy.
- Manageable drawdown.
- No single session or parameter doing all the work unless intentionally selected.
- Results remain acceptable after costs.

---

## 17. Acceptance Criteria

Before considering the strategy decision-useful, it should satisfy these criteria.

### 17.1 Logic acceptance

- No entries on non-1m charts.
- No entries without valid HTF tap.
- No entries without same-direction parent FVG.
- No entries before parent retest/lock.
- No entries before opposing 1m FVG inversion.
- No same-candle inversion and entry.
- No entries outside enabled sessions.
- No stale post-inversion entries beyond max bars.
- Parent priority behaves as specified.
- Parent invalidation/full-fill behaves as specified.
- Stop-loss, take-profit, and break-even levels match the strategy rules.

### 17.2 Trade audit acceptance

- Trader can identify the full setup chain on the chart.
- Status display is readable on a white background.
- Setup ID is visible.
- HTF, parent, opposing gap, inversion, entry, SL, TP, and BE are clearly auditable.
- Abandoned setup reset reason is visible or recoverable.
- Completed trades can be audited historically during debugging.

### 17.3 Backtest acceptance

- Performance is reported in R, not only raw dollars.
- Costs and slippage are included in a sensitivity test.
- Results are segmented by direction, session, HTF, and parent timeframe.
- No single fragile subgroup explains the entire edge unless deliberately accepted.
- Parameter sweeps show reasonable stability.
- Walk-forward results are not materially worse than tuned results.

### 17.4 Strategy acceptance

- Open questions are resolved or explicitly accepted as current behavior.
- Causality rule is decided: confirmation-after vs full-structure-after.
- HTF record cap is treated as a strategic rule, not an accidental storage limit.
- Timeouts are either added or intentionally left open.
- Entry locality is either justified or retuned.
- The chosen SL/TP/BE model is backed by R-multiple analysis.

---

## 18. Do-Not-Break Rules

These are the core invariants that must remain true unless AlgoChef intentionally changes the strategy.

1. Strategy is designed for 1m execution.
2. Only one setup/trade is active at a time.
3. HTF retest creates context only; it is not an entry.
4. Parent FVG direction must match HTF direction.
5. Parent FVG must confirm inside the parent reaction window.
6. Parent priority before lock is 30m > 15m > 10m > 5m > 3m.
7. Parent remains replaceable until 1m retest lock.
8. Parent cannot be replaced after lock.
9. Parent full fill before entry invalidates the setup under current default.
10. Parent structural invalidation before entry resets/removes the parent.
11. After entry, structural invalidation is ignored; trade is managed by SL/TP/BE.
12. Opposing 1m FVG must be opposite direction to the intended trade.
13. Opposing 1m FVG must be near or inside the parent under current proximity rule.
14. Inversion requires body close through the selected opposing gap boundary.
15. Inversion must be checked before replacing the selected opposing gap.
16. Final entry requires a new same-direction 1m Fair Value Gap after inversion.
17. Entry cannot occur on the inversion candle.
18. Final entry must occur during enabled session.
19. Outside-session final entry signals are not queued for later.
20. Risk must be at least one tick.
21. Default SL uses manipulation swing from parent retest through entry.
22. Default TP is fixed 1R unless retuned.
23. Default BE requires body close through parent internal high/low.
24. BE cannot activate on the entry candle.
25. SL/TP/BE continue after session ends.
26. Higher-timeframe and parent structures must use confirmed candles only.
27. Historical/debug audit evidence must preserve setup identity enough for audit.
28. Current tuned defaults should not be reverted casually.

---

## 19. Strategic Interpretation of Current Defaults

Some settings are not merely technical parameters. They materially change the trading thesis.

### 19.1 Minimum FVG size = 8 ticks

Interpretation:

- Filters small 1m gaps and weak HTF/parent gaps.
- Better suited to avoiding noise in instruments like NQ.

Risk:

- Same threshold across all timeframes may be too small for 1H/4H but too large for some 1m execution contexts.

Potential improvement:

- Test timeframe-specific or volatility-scaled FVG size.

### 19.2 Parent ↔ HTF distance = 80 ticks

Interpretation:

- Forces parent FVG to be reasonably near HTF context.

Risk:

- Vertical nearness is not the same as causal reaction.

Potential improvement:

- Require overlap, post-tap displacement, or full structure after HTF tap.

### 19.3 Entry FVG near parent = off

Interpretation:

- Allows final continuation confirmation even after price leaves the parent area.

Risk:

- Can create chase entries and wider stops.

Potential improvement:

- Add risk/reward constraint or separate entry locality threshold.

### 19.4 Max HTF records = 1

Interpretation:

- Strong freshness filter.

Risk:

- Can hide conflicts and ignore older still-valid levels.
- Can preserve an active old selected level while fresher zones form during a trade.

Potential improvement:

- Track more HTF zones and rank by freshness, timeframe, distance, and fill state.

### 19.5 BE mode = body close

Interpretation:

- Reduces premature break-even moves from wick probes.

Risk:

- Gives back more trades before BE protection.

Potential improvement:

- Compare body-close, wick-touch, and no-BE variants in R.

### 19.6 TP = 1R

Interpretation:

- Simple, fast target.

Risk:

- Sensitive to costs.
- May cap the asymmetric benefit of true displacement.

Potential improvement:

- Test 1.5R, 2R, liquidity target, and partial/runner structures.

---

## 20. Current Base-Case Assessment

This section incorporates the delegated logic, risk/backtest, and documentation reviews. The reviewers agreed that the conceptual model is coherent and auditable, but not yet proven profitable or production-ready. The key weakness is not the headline thesis; it is whether the deterministic filters preserve the discretionary edge without creating stale, loose, or cost-sensitive entries.

### 20.1 What is strong

- The strategy has a clear causal chain.
- It avoids entering directly on HTF tap.
- It requires same-direction parent displacement.
- It waits for parent retest instead of chasing.
- It models failed opposing displacement via iFVG.
- It prevents same-candle inversion/entry.
- It has an explicit manipulation-based stop-loss.
- It has a defined BE trigger tied to parent structure.
- It has trade audit requirements, which are essential for SMC validation.

### 20.2 What is fragile

- Causality may be looser than intended.
- Retest rules may be too permissive.
- Max HTF records = 1 may hide important context.
- Several phases lack timeouts.
- Entry can occur away from parent under current default.
- Fixed 1R may not capture enough edge.
- Costs/slippage can materially degrade performance.
- 1m historical bars cannot resolve all intrabar event order.

### 20.3 Most likely reasons current performance could look poor

1. Too many mechanical FVG chains do not match discretionary high-quality SMC setups.
2. Stale setups remain active too long.
3. Parent and execution structures are not causally strict enough.
4. Final entries can occur too far from parent.
5. Fixed 1R does not compensate for losses and live costs.
6. Broad sessions include low-quality liquidity periods.
7. No trend, liquidity sweep, displacement-strength, or news filter exists.
8. Backtest order assumptions overstate or misstate fill quality.
9. One active setup can block better newer opportunities.
10. HTF record cap may cause the strategy to trade the wrong context.

### 20.4 Highest-impact next research decisions

If improving profitability is the goal, prioritize these in order:

1. Decide strict causality: full structure after trigger vs confirmation after trigger.
2. Add/compare timeouts for parent retest, opposing gap, and inversion.
3. Analyze performance by parent timeframe and session.
4. Compare entry locality on/off.
5. Compare max HTF records 1 vs 2/5/20.
6. Compare fixed 1R vs larger/liquidity targets.
7. Add cost/slippage sensitivity.
8. Manually audit losers to classify whether failures are logic bugs, weak context, or normal losses.

---

## 21. Clean Strategy Specification Checklist

A trade is valid only if every item below is true.

### 21.1 Bullish trade checklist

1. A bullish 1H or 4H Fair Value Gap is confirmed and unfilled.
2. 1m price retests that HTF zone.
3. No same-rank bullish/bearish HTF conflict invalidates selection.
4. A bullish parent Fair Value Gap confirms inside the reaction window.
5. Parent is near the selected HTF zone under current proximity rule.
6. Parent has not fully filled or invalidated.
7. 1m price retests selected parent after parent is available.
8. Parent locks.
9. A bearish 1m Fair Value Gap forms near/inside parent after parent retest context begins.
10. The bearish 1m Fair Value Gap inverts by body close above its top.
11. A new bullish 1m Fair Value Gap confirms after inversion.
12. Entry is inside enabled session.
13. Risk is at least one tick.
14. Stop is below manipulation swing low minus buffer.
15. TP is 1R unless retuned.
16. BE trigger is body close above parent internal high.

### 21.2 Bearish trade checklist

1. A bearish 1H or 4H Fair Value Gap is confirmed and unfilled.
2. 1m price retests that HTF zone.
3. No same-rank bullish/bearish HTF conflict invalidates selection.
4. A bearish parent Fair Value Gap confirms inside the reaction window.
5. Parent is near the selected HTF zone under current proximity rule.
6. Parent has not fully filled or invalidated.
7. 1m price retests selected parent after parent is available.
8. Parent locks.
9. A bullish 1m Fair Value Gap forms near/inside parent after parent retest context begins.
10. The bullish 1m Fair Value Gap inverts by body close below its bottom.
11. A new bearish 1m Fair Value Gap confirms after inversion.
12. Entry is inside enabled session.
13. Risk is at least one tick.
14. Stop is above manipulation swing high plus buffer.
15. TP is 1R unless retuned.
16. BE trigger is body close below parent internal low.

---

## 22. Final Notes

This strategy is now sufficiently specified to be reviewed, debugged, and improved systematically. The next useful work is not more narrative; it is controlled validation: trade audits, R-normalized segment analysis, cost/slippage sensitivity, causality A/B tests, and targeted regression tests for the gaps identified above.

The most important distinction is between a mechanically valid FVG chain and a high-quality SMC setup. The current rules define the mechanical chain. The remaining work is to determine whether the mechanical chain captures the discretionary edge AlgoChef actually wants, or whether it needs stricter causality, better context filters, stronger timeouts, and more realistic exit logic.

The highest-value deceptively simple question is:

> Are we trading a true post-HTF-reaction displacement sequence, or are we sometimes trading structures that merely confirmed after the reaction but partially formed before it?

That single distinction may explain a large part of the difference between a discretionary idea that looks convincing on a chart and an unprofitable mechanical backtest.
