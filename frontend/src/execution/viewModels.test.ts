import { describe, expect, it } from 'vitest';
import { formatAge, formatClock, formatSignedPoints, latestPrice, unrealizedFor } from './viewModels';
import type { MarketBar, OpenPosition } from '../domain/models';

const bar = (overrides: Partial<MarketBar>): MarketBar => ({
  timeframe: 147,
  tradingDay: '2026-05-21',
  barIndex: 0,
  barId: '147t:2026-05-21:0',
  openTimeUtc: '2026-05-21T14:00:00Z',
  closeTimeUtc: '2026-05-21T14:00:10Z',
  openTicks: 92000,
  highTicks: 92008,
  lowTicks: 91996,
  closeTicks: 92004,
  volume: 10,
  tradeCount: 10,
  complete: true,
  ...overrides,
});

const position = (overrides: Partial<OpenPosition> = {}): OpenPosition => ({
  predictionId: 'pred-1',
  touchId: 'touch-1',
  direction: 'long',
  contracts: 1,
  entryTsUtc: '2026-05-21T14:00:00Z',
  entryPrice: 23000,
  entryPriceConservative: 23000.25,
  tpPrice: 23015,
  slPrice: 22970,
  session: 'ny',
  levelKind: 'pdl',
  bundleId: 'bundle-a',
  mode: 'replay',
  pointValue: 20,
  lastPrice: 23001,
  unrealizedPoints: 1,
  unrealizedPointsConservative: 0.75,
  ...overrides,
});

describe('execution view models', () => {
  it('finds the newest print across forming and closed bars', () => {
    const last = latestPrice(
      [bar({ closeTicks: 92010, closeTimeUtc: '2026-05-21T14:05:00Z', complete: false })],
      [bar({ closeTicks: 92004, closeTimeUtc: '2026-05-21T14:00:10Z' })],
    );
    expect(last).toEqual({ price: 23002.5, timeUtc: '2026-05-21T14:05:00Z' });
    expect(latestPrice([], [])).toBeNull();
  });

  it('marks unrealized in both columns against the local print (long)', () => {
    const view = unrealizedFor(position(), { price: 23002.5, timeUtc: '2026-05-21T14:05:00Z' });
    expect(view.points).toBe(2.5);
    expect(view.pointsConservative).toBe(2.25);
    expect(view.markPrice).toBe(23002.5);
  });

  it('mirrors the arithmetic for shorts (conservative entry is BELOW)', () => {
    const short = position({ direction: 'short', entryPriceConservative: 22999.75 });
    const view = unrealizedFor(short, { price: 22990, timeUtc: '2026-05-21T14:05:00Z' });
    expect(view.points).toBe(10);
    expect(view.pointsConservative).toBe(9.75);
  });

  it('falls back to the backend-stamped mark when no local bar exists yet', () => {
    const view = unrealizedFor(position(), null);
    expect(view.points).toBe(1);
    expect(view.pointsConservative).toBe(0.75);
    expect(view.markPrice).toBe(23001);
  });

  it('formats signed points, ages on the event clock, and UTC clock cells', () => {
    expect(formatSignedPoints(2.5)).toBe('+2.50');
    expect(formatSignedPoints(-30.5)).toBe('-30.50');
    expect(formatSignedPoints(null)).toBe('—');
    expect(formatAge('2026-05-21T14:00:00Z', '2026-05-21T14:03:20Z')).toBe('3m 20s');
    expect(formatAge('2026-05-21T14:00:00Z', '2026-05-21T16:04:00Z')).toBe('2h 04m');
    expect(formatAge('2026-05-21T14:00:00Z', '2026-05-21T14:00:45Z')).toBe('45s');
    expect(formatAge('2026-05-21T14:00:00Z', null)).toBe('—');
    // A position "opened in the future" relative to the event clock is a data
    // problem, not a negative age.
    expect(formatAge('2026-05-21T15:00:00Z', '2026-05-21T14:00:00Z')).toBe('—');
    expect(formatClock('2026-05-21T14:05:07Z')).toBe('14:05:07');
    expect(formatClock(null)).toBe('—');
  });
});
