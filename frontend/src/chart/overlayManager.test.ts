import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { SeriesMarker, Time } from 'lightweight-charts';
import { ChartOverlayManager } from './overlayManager';
import { normalizeLevels } from './viewModels';
import type { LevelOverlay } from './viewModels';
import type { MarketLevel } from '../domain/models';

const mocks = vi.hoisted(() => ({
  createSeriesMarkers: vi.fn(),
  setMarkers: vi.fn(),
  removeMarkers: vi.fn(),
}));

vi.mock('lightweight-charts', () => ({
  createSeriesMarkers: mocks.createSeriesMarkers,
}));

const createSeries = () => ({
  createPriceLine: vi.fn((options: unknown) => ({ options, id: crypto.randomUUID() })),
  removePriceLine: vi.fn(),
});

describe('ChartOverlayManager', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.createSeriesMarkers.mockReturnValue({ setMarkers: mocks.setMarkers, remove: mocks.removeMarkers });
  });

  it('removes stale price lines when levels disappear and cleans up remaining lines on destroy', () => {
    const series = createSeries();
    const manager = new ChartOverlayManager(series as never);
    const first: LevelOverlay = { id: 'pdh', price: 19000, title: 'PDH EL', color: '#d7e3ee', lineWidth: 2, lineStyle: 0, eligible: true };
    const second: LevelOverlay = { id: 'asia', price: 18950, title: 'ASIA DISP', color: '#708194', lineWidth: 1, lineStyle: 2, eligible: false };

    manager.syncLevels([first, second]);
    const createdLines = series.createPriceLine.mock.results.map((result) => result.value);
    manager.syncLevels([first]);

    expect(series.removePriceLine).toHaveBeenCalledWith(createdLines[1]);
    expect(series.createPriceLine).toHaveBeenLastCalledWith(expect.objectContaining({ price: 19000, title: 'PDH EL' }));

    manager.destroy();

    expect(series.removePriceLine).toHaveBeenCalledWith(series.createPriceLine.mock.results.at(-1)?.value);
    expect(mocks.removeMarkers).toHaveBeenCalledOnce();
  });

  it('uses distinct eligible and display-only styles with deterministic professional origin colors', () => {
    const levels: MarketLevel[] = [
      { kind: 'pdh', priceTicks: 76000, tradingDay: '2026-05-21', originSession: 'ny', developing: false, eligible: true },
      { kind: 'asia_high', priceTicks: 76100, tradingDay: '2026-05-21', originSession: 'asia', developing: false, eligible: true },
      { kind: 'london_low', priceTicks: 75900, tradingDay: '2026-05-21', originSession: 'london', developing: false, eligible: true },
      { kind: 'custom', priceTicks: 75800, tradingDay: '2026-05-21', originSession: null, developing: false, eligible: false },
    ];

    const firstPass = normalizeLevels(levels);
    const secondPass = normalizeLevels(levels);

    expect(firstPass.map((level) => level.color)).toEqual(['#d7e3ee', '#a78bfa', '#38bdf8', '#708194']);
    expect(firstPass).toEqual(secondPass);
    expect(firstPass[0]).toMatchObject({ lineWidth: 2, lineStyle: 0 });
    expect(firstPass[3]).toMatchObject({ lineWidth: 1, lineStyle: 2, eligible: false });
  });

  it('replaces marker sets without accumulating duplicate markers', () => {
    const series = createSeries();
    const manager = new ChartOverlayManager(series as never);
    const markers: SeriesMarker<Time>[] = [
      { time: 1 as Time, position: 'belowBar', shape: 'arrowUp', color: '#36d399', text: 'touch' },
      { time: 2 as Time, position: 'aboveBar', shape: 'square', color: '#4ea1ff', text: 'observation' },
    ];

    manager.syncMarkers(markers);
    manager.syncMarkers(markers.slice(1));

    expect(mocks.setMarkers).toHaveBeenNthCalledWith(1, markers);
    expect(mocks.setMarkers).toHaveBeenNthCalledWith(2, [markers[1]]);
    expect(mocks.setMarkers).toHaveBeenCalledTimes(2);
  });
});
