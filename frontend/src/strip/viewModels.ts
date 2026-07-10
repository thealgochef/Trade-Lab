// COCKPIT P2: pure view-model helpers for the trader strip.
//
// Price/day math works on the bars already held by the market store (forming +
// recent closed, all timeframes mixed). Clock math is America/New_York wall time
// derived through the Intl timezone database — never a fixed UTC offset — so DST
// transitions (spring-forward / fall-back) produce real elapsed-time countdowns.
import type { MarketBar } from '../domain/models';

export const NY_TIME_ZONE = 'America/New_York';
export const NY_OPEN = { hour: 9, minute: 30 } as const;
export const NY_FLATTEN = { hour: 16, minute: 40 } as const;

const STRIP_TICK_SIZE = 0.25;

export type LatestPrint = {
  priceTicks: number;
  closeTimeUtc: string;
  /** True when the print came from a forming (incomplete) bar. */
  forming: boolean;
};

/**
 * The newest print visible in the bar streams: the close of the most recent
 * forming or closed bar across every timeframe (every timeframe's forming bar
 * closes on the same latest trade, so any winner carries the same price).
 */
export function latestPrint(currentBars: MarketBar[], closedBars: MarketBar[]): LatestPrint | null {
  let best: LatestPrint | null = null;
  const consider = (bar: MarketBar, forming: boolean) => {
    const at = Date.parse(bar.closeTimeUtc);
    if (!Number.isFinite(at) || !Number.isFinite(bar.closeTicks)) return;
    if (best === null) {
      best = { priceTicks: bar.closeTicks, closeTimeUtc: bar.closeTimeUtc, forming };
      return;
    }
    const bestAt = Date.parse(best.closeTimeUtc);
    // Prefer the forming bar on wall-clock ties: it is the live print.
    if (at > bestAt || (at === bestAt && forming && !best.forming)) {
      best = { priceTicks: bar.closeTicks, closeTimeUtc: bar.closeTimeUtc, forming };
    }
  };
  for (const bar of currentBars) consider(bar, !bar.complete);
  for (const bar of closedBars) consider(bar, false);
  return best;
}

/**
 * The trading day's first bar open, in ticks. Exact-or-nothing: only a bar with
 * backend bar_index 0 for the given trading day qualifies (every timeframe's bar
 * 0 opens on the day's first trade, so any timeframe works). When bar 0 has been
 * truncated out of the retained window — or bars carry no bar_index at all — the
 * session net change is honestly unavailable and the caller renders an em-dash.
 */
export function dayFirstOpenTicks(
  currentBars: MarketBar[],
  closedBars: MarketBar[],
  tradingDay: string | null,
): number | null {
  if (!tradingDay) return null;
  for (const bars of [closedBars, currentBars]) {
    for (const bar of bars) {
      if (bar.tradingDay === tradingDay && bar.barIndex === 0 && Number.isFinite(bar.openTicks)) {
        return bar.openTicks;
      }
    }
  }
  return null;
}

/** Day high/low in ticks across every retained bar of the given trading day. */
export function dayHighLowTicks(
  currentBars: MarketBar[],
  closedBars: MarketBar[],
  tradingDay: string | null,
): { highTicks: number; lowTicks: number } | null {
  if (!tradingDay) return null;
  let high = Number.NEGATIVE_INFINITY;
  let low = Number.POSITIVE_INFINITY;
  for (const bars of [currentBars, closedBars]) {
    for (const bar of bars) {
      if (bar.tradingDay !== tradingDay) continue;
      if (Number.isFinite(bar.highTicks) && bar.highTicks > high) high = bar.highTicks;
      if (Number.isFinite(bar.lowTicks) && bar.lowTicks < low) low = bar.lowTicks;
    }
  }
  if (!Number.isFinite(high) || !Number.isFinite(low)) return null;
  return { highTicks: high, lowTicks: low };
}

export const stripTicksToPrice = (ticks: number): number => ticks * STRIP_TICK_SIZE;

export const formatStripPrice = (ticks: number): string =>
  stripTicksToPrice(ticks).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const formatSignedPoints = (points: number): string =>
  `${points > 0 ? '+' : points < 0 ? '−' : ''}${Math.abs(points).toFixed(2)}`;

// --------------------------------------------------------------------------- //
// America/New_York wall-clock math (Intl-backed; DST-safe by construction)
// --------------------------------------------------------------------------- //

export type EtParts = {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  second: number;
};

const ET_FORMATTER = new Intl.DateTimeFormat('en-US', {
  timeZone: NY_TIME_ZONE,
  hourCycle: 'h23',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
});

/** Read the ET wall-clock fields for an epoch instant via the Intl tz database. */
export function etParts(epochMs: number): EtParts {
  const parts: Record<string, number> = {};
  for (const part of ET_FORMATTER.formatToParts(epochMs)) {
    if (part.type !== 'literal') parts[part.type] = Number(part.value);
  }
  return {
    year: parts.year,
    month: parts.month,
    day: parts.day,
    hour: parts.hour,
    minute: parts.minute,
    second: parts.second,
  };
}

const partsAsUtcMs = (p: EtParts): number =>
  Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);

/**
 * The epoch instant at which the ET wall clock reads the given fields.
 *
 * Inverse timezone lookup by successive correction: start from the UTC guess,
 * measure how the guess actually renders in ET, and shift by the difference.
 * Two rounds converge for every real NY offset; 09:30/16:40 are never inside
 * the 02:00 transition hole, so the result is exact on DST-transition days.
 */
export function etWallTimeToEpochMs(target: Omit<EtParts, 'second'> & { second?: number }): number {
  const desired = partsAsUtcMs({ ...target, second: target.second ?? 0 });
  let guess = desired;
  for (let i = 0; i < 2; i += 1) {
    const rendered = partsAsUtcMs(etParts(guess));
    const diff = rendered - desired;
    if (diff === 0) break;
    guess -= diff;
  }
  return guess;
}

// Per-UTC-hour offset cache: the ET offset is constant within any UTC hour (US
// DST transitions land on exact UTC hour boundaries), so one Intl lookup per
// distinct hour makes bulk per-bar classification cheap (COCKPIT P4b).
const ET_OFFSET_CACHE = new Map<number, number>();

/** ET wall-clock minutes-of-day (0..1439) for an epoch instant, DST-correct. */
export function etMinutesOfDay(epochMs: number): number {
  const hourKey = Math.floor(epochMs / 3_600_000);
  let offsetMs = ET_OFFSET_CACHE.get(hourKey);
  if (offsetMs === undefined) {
    const hourStart = hourKey * 3_600_000;
    offsetMs = partsAsUtcMs(etParts(hourStart)) - hourStart;
    if (ET_OFFSET_CACHE.size > 20_000) ET_OFFSET_CACHE.clear();
    ET_OFFSET_CACHE.set(hourKey, offsetMs);
  }
  const shifted = new Date(epochMs + offsetMs);
  return shifted.getUTCHours() * 60 + shifted.getUTCMinutes();
}

export type Countdown = {
  mode: 'until' | 'since';
  seconds: number;
};

/**
 * Real elapsed seconds between now and today's (ET calendar day's) target wall
 * time. `until` before the target, `since` after — day-scoped, so past midnight
 * ET the countdown flips back to `until` for the new day.
 */
export function nyCountdown(nowMs: number, target: { hour: number; minute: number }): Countdown {
  const today = etParts(nowMs);
  const targetMs = etWallTimeToEpochMs({
    year: today.year,
    month: today.month,
    day: today.day,
    hour: target.hour,
    minute: target.minute,
  });
  const deltaSeconds = Math.round((targetMs - nowMs) / 1000);
  return deltaSeconds >= 0
    ? { mode: 'until', seconds: deltaSeconds }
    : { mode: 'since', seconds: -deltaSeconds };
}

/** h:mm:ss with unbounded hours. */
export function formatCountdown(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const two = (value: number) => String(value).padStart(2, '0');
  return `${h}:${two(m)}:${two(s)}`;
}
