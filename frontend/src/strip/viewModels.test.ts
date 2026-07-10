import { describe, expect, it } from 'vitest';
import type { MarketBar } from '../domain/models';
import {
  NY_FLATTEN,
  NY_OPEN,
  dayFirstOpenTicks,
  dayHighLowTicks,
  etWallTimeToEpochMs,
  formatCountdown,
  formatSignedPoints,
  formatStripPrice,
  latestPrint,
  nyCountdown,
} from './viewModels';

const bar = (overrides: Partial<MarketBar>): MarketBar => ({
  timeframe: 147,
  tradingDay: '2026-07-10',
  barIndex: 1,
  barId: null,
  openTimeUtc: '2026-07-10T13:30:00Z',
  closeTimeUtc: '2026-07-10T13:31:00Z',
  openTicks: 96_000,
  highTicks: 96_040,
  lowTicks: 95_980,
  closeTicks: 96_020,
  volume: 10,
  tradeCount: 10,
  complete: true,
  ...overrides,
});

describe('latestPrint', () => {
  it('returns null with no bars', () => {
    expect(latestPrint([], [])).toBeNull();
  });

  it('picks the newest close across forming and closed bars', () => {
    const forming = bar({ complete: false, closeTicks: 96_100, closeTimeUtc: '2026-07-10T14:00:05Z' });
    const closed = bar({ closeTicks: 96_050, closeTimeUtc: '2026-07-10T13:59:00Z' });
    expect(latestPrint([forming], [closed])?.priceTicks).toBe(96_100);
  });

  it('prefers the forming bar on wall-clock ties', () => {
    const forming = bar({ complete: false, closeTicks: 96_100, closeTimeUtc: '2026-07-10T14:00:00Z' });
    const closed = bar({ closeTicks: 96_050, closeTimeUtc: '2026-07-10T14:00:00Z' });
    const print = latestPrint([forming], [closed]);
    expect(print?.priceTicks).toBe(96_100);
    expect(print?.forming).toBe(true);
  });
});

describe('dayFirstOpenTicks', () => {
  it('requires bar_index 0 of the current trading day (exact-or-nothing)', () => {
    const first = bar({ barIndex: 0, openTicks: 95_900 });
    const later = bar({ barIndex: 5, openTicks: 96_000 });
    expect(dayFirstOpenTicks([], [first, later], '2026-07-10')).toBe(95_900);
    // Bar 0 truncated out of retention: honestly unavailable.
    expect(dayFirstOpenTicks([], [later], '2026-07-10')).toBeNull();
  });

  it('ignores other trading days and null trading day', () => {
    const otherDay = bar({ barIndex: 0, tradingDay: '2026-07-09', openTicks: 95_000 });
    expect(dayFirstOpenTicks([], [otherDay], '2026-07-10')).toBeNull();
    expect(dayFirstOpenTicks([], [otherDay], null)).toBeNull();
  });
});

describe('dayHighLowTicks', () => {
  it('spans forming and closed bars of the trading day only', () => {
    const closed = bar({ highTicks: 96_200, lowTicks: 95_800 });
    const forming = bar({ complete: false, highTicks: 96_260, lowTicks: 95_900 });
    const stale = bar({ tradingDay: '2026-07-09', highTicks: 99_000, lowTicks: 90_000 });
    expect(dayHighLowTicks([forming], [closed, stale], '2026-07-10')).toEqual({
      highTicks: 96_260,
      lowTicks: 95_800,
    });
    expect(dayHighLowTicks([], [], '2026-07-10')).toBeNull();
  });
});

describe('ET wall-clock math (Intl-backed, DST-safe)', () => {
  it('maps 09:30 ET to 13:30Z under EDT and 14:30Z under EST', () => {
    expect(etWallTimeToEpochMs({ year: 2026, month: 7, day: 10, hour: 9, minute: 30 })).toBe(
      Date.parse('2026-07-10T13:30:00Z'),
    );
    expect(etWallTimeToEpochMs({ year: 2026, month: 1, day: 15, hour: 9, minute: 30 })).toBe(
      Date.parse('2026-01-15T14:30:00Z'),
    );
  });

  it('spring-forward day: 01:30 EST to the 09:30 EDT open is 7 real hours', () => {
    // 2026-03-08 is the US spring-forward date; 02:00-03:00 ET does not exist.
    const now = Date.parse('2026-03-08T06:30:00Z'); // 01:30 EST
    expect(nyCountdown(now, NY_OPEN)).toEqual({ mode: 'until', seconds: 7 * 3600 });
  });

  it('fall-back day: 01:30 EDT to the 09:30 EST open is 9 real hours', () => {
    // 2026-11-01 is the US fall-back date; the 01:00-02:00 ET hour repeats.
    const now = Date.parse('2026-11-01T05:30:00Z'); // 01:30 EDT
    expect(nyCountdown(now, NY_OPEN)).toEqual({ mode: 'until', seconds: 9 * 3600 });
  });

  it('switches to since after the target passes, day-scoped', () => {
    const after = Date.parse('2026-07-10T21:00:00Z'); // 17:00 EDT
    expect(nyCountdown(after, NY_FLATTEN)).toEqual({ mode: 'since', seconds: 20 * 60 });
    const before = Date.parse('2026-07-10T20:00:00Z'); // 16:00 EDT
    expect(nyCountdown(before, NY_FLATTEN)).toEqual({ mode: 'until', seconds: 40 * 60 });
  });
});

describe('formatting', () => {
  it('formats prices, signed points, and countdowns', () => {
    expect(formatStripPrice(96_020)).toBe('24,005.00');
    expect(formatSignedPoints(12.5)).toBe('+12.50');
    expect(formatSignedPoints(-3.25)).toBe('−3.25');
    expect(formatSignedPoints(0)).toBe('0.00');
    expect(formatCountdown(7 * 3600 + 5 * 60 + 9)).toBe('7:05:09');
    expect(formatCountdown(59)).toBe('0:00:59');
  });
});
