import { describe, expect, it } from 'vitest';
import { TickMarkType, type Time } from 'lightweight-charts';
import { axisIndexInvalidated, buildAxisTimeIndex, formatCrosshairCt, formatTickMarkCt } from './axisTime';
import { normalizeBarsForTimeframe } from './viewModels';
import type { MarketBar } from '../domain/models';

const bar = (overrides: Partial<MarketBar>): MarketBar => ({
  timeframe: 147,
  tradingDay: '2026-05-21',
  barIndex: 0,
  barId: '147t:2026-05-21:0',
  openTimeUtc: '2026-05-21T14:00:00Z',
  closeTimeUtc: '2026-05-21T14:00:10Z',
  openTicks: 76000,
  highTicks: 76008,
  lowTicks: 75996,
  closeTicks: 76004,
  volume: 10,
  tradeCount: 10,
  complete: true,
  ...overrides,
});

// COCKPIT-FIX F4: the chart time coordinate is synthetic (trading-day midnight
// UTC + bar_index), so every case below goes through the real normalization
// pipeline and asserts the label comes from the bar's REAL open instant.
describe('axis time formatting (COCKPIT-FIX F4)', () => {
  it('formats a January (CST, UTC-6) tick as 12-hour Central wall-clock time', () => {
    const bars = normalizeBarsForTimeframe([
      bar({ tradingDay: '2026-01-15', barIndex: 42, barId: '147t:2026-01-15:42', openTimeUtc: '2026-01-15T15:30:00Z', closeTimeUtc: '2026-01-15T15:30:10Z' }),
    ], 147);
    const index = buildAxisTimeIndex(bars);

    expect(formatTickMarkCt(index, bars[0].time, TickMarkType.Time)).toBe('9:30 am');
  });

  it('formats a July (CDT, UTC-5) tick as 12-hour Central wall-clock time', () => {
    const bars = normalizeBarsForTimeframe([
      bar({ tradingDay: '2026-07-08', barIndex: 42, barId: '147t:2026-07-08:42', openTimeUtc: '2026-07-08T15:30:00Z', closeTimeUtc: '2026-07-08T15:30:10Z' }),
    ], 147);
    const index = buildAxisTimeIndex(bars);

    // Same UTC wall clock as the January case, one hour later in CT: DST pinned.
    expect(formatTickMarkCt(index, bars[0].time, TickMarkType.Time)).toBe('10:30 am');
    // Seconds-granularity ticks keep their seconds — adjacent same-minute tick
    // bars must not all render the same label.
    expect(formatTickMarkCt(index, bars[0].time, TickMarkType.TimeWithSeconds)).toBe('10:30:00 am');
  });

  it('renders a Central date label on day-boundary tick types', () => {
    const bars = normalizeBarsForTimeframe([
      bar({ tradingDay: '2026-07-08', barIndex: 0, barId: '147t:2026-07-08:0', openTimeUtc: '2026-07-08T15:30:00Z', closeTimeUtc: '2026-07-08T15:30:10Z' }),
      // 03:00Z is still the PREVIOUS day in Chicago — the date label must say so.
      bar({ tradingDay: '2026-07-09', barIndex: 0, barId: '147t:2026-07-09:0', openTimeUtc: '2026-07-09T03:00:00Z', closeTimeUtc: '2026-07-09T03:00:10Z' }),
    ], 147);
    const index = buildAxisTimeIndex(bars);

    expect(formatTickMarkCt(index, bars[0].time, TickMarkType.DayOfMonth)).toBe('Jul 8');
    expect(formatTickMarkCt(index, bars[1].time, TickMarkType.DayOfMonth)).toBe('Jul 8');
    expect(formatTickMarkCt(index, bars[0].time, TickMarkType.Month)).toBe('Jul 2026');
    expect(formatTickMarkCt(index, bars[0].time, TickMarkType.Year)).toBe('2026');
  });

  it('maps each synthetic tick to its own bar, not to the coordinate read as an epoch', () => {
    const bars = normalizeBarsForTimeframe([
      bar({ barIndex: 7, barId: '147t:2026-05-21:7', openTimeUtc: '2026-05-21T14:00:00Z', closeTimeUtc: '2026-05-21T14:00:10Z' }),
      bar({ barIndex: 8, barId: '147t:2026-05-21:8', openTimeUtc: '2026-05-21T14:07:12Z', closeTimeUtc: '2026-05-21T14:07:22Z' }),
    ], 147);
    const index = buildAxisTimeIndex(bars);

    // Adjacent synthetic seconds resolve seven wall-clock minutes apart —
    // proof the label comes from the bar mapping, not the tick value itself.
    expect(bars.map((entry) => Number(entry.time))).toEqual([1779321607, 1779321608]);
    expect(formatTickMarkCt(index, bars[0].time, TickMarkType.Time)).toBe('9:00 am');
    expect(formatTickMarkCt(index, bars[1].time, TickMarkType.Time)).toBe('9:07 am');
  });

  it('renders empty labels for ticks outside the loaded bars', () => {
    const bars = normalizeBarsForTimeframe([bar({})], 147);
    const index = buildAxisTimeIndex(bars);
    const beyond = (Number(bars[0].time) + 500) as unknown as Time;

    expect(formatTickMarkCt(index, beyond, TickMarkType.Time)).toBe('');
    expect(formatCrosshairCt(index, beyond)).toBe('');
    expect(formatCrosshairCt(new Map(), bars[0].time)).toBe('');
  });

  it('formats the crosshair label with date and seconds-precision Central time', () => {
    const bars = normalizeBarsForTimeframe([
      bar({ tradingDay: '2026-07-08', barIndex: 3, barId: '147t:2026-07-08:3', openTimeUtc: '2026-07-08T15:30:45Z', closeTimeUtc: '2026-07-08T15:30:55Z' }),
    ], 147);
    const index = buildAxisTimeIndex(bars);

    expect(formatCrosshairCt(index, bars[0].time)).toBe('Jul 8 · 10:30:45 am');
  });

  it('flags an index rebuild as invalidating only when an existing key remaps or disappears', () => {
    const entry = (key: number, at: number): [number, number] => [key, at];

    // Pure append (the live hot path): no flush.
    expect(axisIndexInvalidated(new Map([entry(1, 100)]), new Map([entry(1, 100), entry(2, 200)]))).toBe(false);
    // Identical rebuild (fresh Map object, same content): no flush.
    expect(axisIndexInvalidated(new Map([entry(1, 100)]), new Map([entry(1, 100)]))).toBe(false);
    // Timeframe switch: the same synthetic key resolves to a different open.
    expect(axisIndexInvalidated(new Map([entry(1, 100)]), new Map([entry(1, 999)]))).toBe(true);
    // Repopulate: a previously known key vanished.
    expect(axisIndexInvalidated(new Map([entry(1, 100)]), new Map([entry(2, 200)]))).toBe(true);
  });
});
