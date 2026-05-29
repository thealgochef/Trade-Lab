import { describe, expect, it } from 'vitest';
import { MAX_BARS_PER_TIMEFRAME, combineMarkers, normalizeBarsForTimeframe, normalizeLevels, normalizeTouchMarkers } from './viewModels';
import type { MarketBar, MarketLevel, MarketTouch, Observation } from '../domain/models';

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

describe('chart view model normalization', () => {
  it('normalizes backend tick bars to numeric lightweight-chart candles with stable bar-index coordinates', () => {
    const bars = normalizeBarsForTimeframe([
      bar({
        openTimeUtc: '2026-05-21T14:00:00.250Z',
        closeTimeUtc: '2026-05-21T14:00:10.999Z',
        barIndex: 42,
        barId: '147t:2026-05-21:42',
        openTicks: 76001,
        highTicks: 76010,
        lowTicks: 75999,
        closeTicks: 76004,
      }),
    ], 147);

    expect(bars[0]).toMatchObject({
      key: '147t:2026-05-21:42',
      timeframe: 147,
      time: 1779321642,
      open: 19000.25,
      high: 19002.5,
      low: 18999.75,
      close: 19001,
      complete: true,
    });
  });

  it('orders and dedupes bars by timeframe and stable bar key', () => {
    const bars = normalizeBarsForTimeframe([
      bar({ barIndex: 2, barId: '147t:2026-05-21:2', openTimeUtc: '2026-05-21T14:01:00Z', closeTimeUtc: '2026-05-21T14:01:10Z', closeTicks: 76012 }),
      bar({ barIndex: 1, barId: '147t:2026-05-21:1', openTimeUtc: '2026-05-21T14:00:00Z', closeTimeUtc: '2026-05-21T14:00:10Z', closeTicks: 76000 }),
      bar({ barIndex: 1, barId: '147t:2026-05-21:1', openTimeUtc: '2026-05-21T14:00:00Z', closeTimeUtc: '2026-05-21T14:00:10Z', closeTicks: 76004 }),
      bar({ timeframe: 987, openTimeUtc: '2026-05-21T14:02:00Z' }),
    ], 147);

    expect(bars).toHaveLength(2);
    expect(bars.map((entry) => entry.key)).toEqual(['147t:2026-05-21:1', '147t:2026-05-21:2']);
    expect(bars[0].close).toBe(19001);
  });

  it('preserves all tick bars using bar-index coordinates', () => {
    const bars = normalizeBarsForTimeframe([
      bar({ openTimeUtc: '2026-05-21T14:00:00Z', closeTimeUtc: '', complete: false }),
      bar({ barIndex: 1, barId: '147t:2026-05-21:1', openTimeUtc: '2026-05-21T14:01:00Z', closeTimeUtc: '2026-05-21T14:01:10Z', complete: true }),
    ], 147);

    expect(bars.map((entry) => ({ time: entry.time, complete: entry.complete }))).toEqual([
      { time: 1779321600, complete: false },
      { time: 1779321601, complete: true },
    ]);
  });

  it('renders distinct chart times for same-second tick bars with different bar ids', () => {
    const bars = normalizeBarsForTimeframe([
      bar({ barIndex: 7, barId: '147t:2026-05-21:7', openTimeUtc: '2026-05-21T14:00:00.100Z' }),
      bar({ barIndex: 8, barId: '147t:2026-05-21:8', openTimeUtc: '2026-05-21T14:00:00.900Z' }),
    ], 147);

    expect(bars.map((entry) => entry.time)).toEqual([1779321607, 1779321608]);
    expect(new Set(bars.map((entry) => entry.key)).size).toBe(2);
  });

  it('keeps incomplete candle time stable when close time changes for the same bar key', () => {
    const first = normalizeBarsForTimeframe([
      bar({ barIndex: 9, barId: '147t:2026-05-21:9', openTimeUtc: '2026-05-21T14:00:00Z', closeTimeUtc: '2026-05-21T14:00:03Z', closeTicks: 76001, complete: false }),
    ], 147);
    const updated = normalizeBarsForTimeframe([
      bar({ barIndex: 9, barId: '147t:2026-05-21:9', openTimeUtc: '2026-05-21T14:00:00Z', closeTimeUtc: '2026-05-21T14:00:12Z', closeTicks: 76008, complete: false }),
    ], 147);

    expect(updated).toHaveLength(1);
    expect(updated[0].key).toBe(first[0].key);
    expect(updated[0].time).toBe(first[0].time);
    expect(updated[0].close).toBe(19002);
  });

  it('filters non-finite bars and retains only the latest max bars per timeframe', () => {
    const baseTime = Date.parse('2026-05-21T14:00:00Z');
    const bars = Array.from({ length: MAX_BARS_PER_TIMEFRAME + 2 }, (_, index) => bar({
      openTimeUtc: new Date(baseTime + index * 1000).toISOString(),
      barIndex: index,
      barId: `147t:2026-05-21:${index}`,
      closeTimeUtc: new Date(baseTime + index * 1000 + 500).toISOString(),
      closeTicks: 76000 + index,
    }));

    const normalized = normalizeBarsForTimeframe([...bars, bar({ openTimeUtc: '2026-05-21T15:00:00Z', closeTicks: Number.NaN })], 147);

    expect(normalized).toHaveLength(MAX_BARS_PER_TIMEFRAME);
    expect(normalized[0].key).toBe('147t:2026-05-21:2');
    expect(normalized.at(-1)?.close).toBe((76000 + MAX_BARS_PER_TIMEFRAME + 1) * 0.25);
  });

  it('maps eligible and display-only levels to visibly different price line styles', () => {
    const levels: MarketLevel[] = [
      { kind: 'pdh', priceTicks: 76000, tradingDay: '2026-05-21', originSession: 'ny', developing: false, eligible: true },
      { kind: 'asia_high', priceTicks: 76100, tradingDay: '2026-05-21', originSession: 'asia', developing: false, eligible: false },
    ];

    const overlays = normalizeLevels(levels);

    expect(overlays[0]).toMatchObject({ price: 19000, lineWidth: 2, lineStyle: 0, eligible: true });
    expect(overlays[1]).toMatchObject({ price: 19025, lineWidth: 1, lineStyle: 2, eligible: false });
    expect(overlays[0].color).not.toBe(overlays[1].color);
  });

  it('preserves deterministic level identity, price, label, origin family, and eligibility', () => {
    const levels: MarketLevel[] = [
      { kind: 'london_low', priceTicks: 75888, tradingDay: '2026-05-21', originSession: 'london', developing: true, eligible: true },
      { kind: 'london_low', priceTicks: 75888, tradingDay: '2026-05-21', originSession: 'london', developing: false, eligible: false },
    ];

    const overlays = normalizeLevels(levels);

    expect(overlays).toHaveLength(1);
    expect(overlays[0]).toMatchObject({
      id: 'london_low:75888:2026-05-21:london',
      price: 18972,
      title: 'LONDON LOW DISP',
      color: '#708194',
      eligible: false,
    });
  });

  it('maps and dedupes touch and observation markers', () => {
    const touches: MarketTouch[] = [
      { id: 't1', timeUtc: '2026-05-21T14:00:10Z', session: 'ny', levelKind: 'pdh', priceTicks: 76000, createdObservation: true },
      { id: 't1', timeUtc: '2026-05-21T14:00:10Z', session: 'ny', levelKind: 'pdh', priceTicks: 76000, createdObservation: true },
    ];
    const observations: Observation[] = [
      { id: 'o1', status: 'active', session: 'ny', levelKind: 'pdh', startUtc: '2026-05-21T14:00:11Z', scheduledEndUtc: '2026-05-21T14:10:11Z' },
      { id: 'o1', status: 'active', session: 'ny', levelKind: 'pdh', startUtc: '2026-05-21T14:00:11Z', scheduledEndUtc: '2026-05-21T14:10:11Z' },
    ];

    const markers = combineMarkers(touches, observations);

    expect(markers.map((marker) => marker.id)).toEqual(['touch:t1', 'observation:o1']);
    expect(markers[0]).toMatchObject({ position: 'belowBar', shape: 'arrowUp' });
    expect(markers[1]).toMatchObject({ shape: 'square' });
  });

  it('uses stable fallback touch marker keys when the backend id is absent', () => {
    const touches: MarketTouch[] = [
      { id: '', timeUtc: '2026-05-21T14:00:10Z', session: 'ny', levelKind: 'pdh', priceTicks: 76000, createdObservation: false },
      { id: '', timeUtc: '2026-05-21T14:00:10Z', session: 'ny', levelKind: 'pdh', priceTicks: 76000, createdObservation: true },
    ];

    const markers = normalizeTouchMarkers(touches);

    expect(markers).toHaveLength(1);
    expect(markers[0]).toMatchObject({ id: 'touch:2026-05-21T14:00:10Z:pdh:76000', shape: 'arrowUp', position: 'belowBar' });
  });
});
