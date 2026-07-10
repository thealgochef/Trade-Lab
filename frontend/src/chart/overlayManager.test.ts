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
  attachPrimitive: vi.fn(),
  detachPrimitive: vi.fn(),
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

  it('keeps TP/SL lines on their own layer, removes them on close, and attaches the session primitive', () => {
    const series = createSeries();
    const manager = new ChartOverlayManager(series as never);
    expect(series.attachPrimitive).toHaveBeenCalledOnce();

    const level: LevelOverlay = { id: 'pdh', price: 19000, title: 'PDH EL', color: '#d7e3ee', lineWidth: 2, lineStyle: 0, eligible: true };
    manager.syncLevels([level]);
    manager.syncPositionLines([
      { id: 'tp:pred-1', price: 23015, title: 'TP long', color: '#36d399', lineWidth: 1, lineStyle: 3 },
      { id: 'sl:pred-1', price: 22970, title: 'SL long', color: '#ff6b6b', lineWidth: 1, lineStyle: 3 },
    ]);
    expect(series.createPriceLine).toHaveBeenCalledTimes(3);

    // Position close: its lines vanish; the level line stays untouched.
    const tpLine = series.createPriceLine.mock.results[1].value;
    const slLine = series.createPriceLine.mock.results[2].value;
    manager.syncPositionLines([]);
    expect(series.removePriceLine).toHaveBeenCalledWith(tpLine);
    expect(series.removePriceLine).toHaveBeenCalledWith(slLine);
    expect(series.removePriceLine).not.toHaveBeenCalledWith(series.createPriceLine.mock.results[0].value);

    manager.destroy();
    expect(series.detachPrimitive).toHaveBeenCalledOnce();
  });

  it('replaces marker sets without accumulating duplicates and keeps inline text with an enlarged glyph', () => {
    const series = createSeries();
    const manager = new ChartOverlayManager(series as never);
    const markers: SeriesMarker<Time>[] = [
      { time: 1 as Time, position: 'belowBar', shape: 'arrowUp', color: '#36d399', text: 'touch' },
      { time: 2 as Time, position: 'aboveBar', shape: 'square', color: '#4ea1ff', text: 'observation' },
    ];

    manager.syncMarkers(markers);
    manager.syncMarkers(markers.slice(1));

    // Inline `text` stays as an always-on label and each marker gets an enlarged `size` so the
    // symbols read clearly; everything else (incl. id when present) passes through.
    expect(mocks.setMarkers).toHaveBeenNthCalledWith(1, [
      { time: 1, position: 'belowBar', shape: 'arrowUp', color: '#36d399', text: 'touch', size: 2 },
      { time: 2, position: 'aboveBar', shape: 'square', color: '#4ea1ff', text: 'observation', size: 2 },
    ]);
    expect(mocks.setMarkers).toHaveBeenNthCalledWith(2, [
      { time: 2, position: 'aboveBar', shape: 'square', color: '#4ea1ff', text: 'observation', size: 2 },
    ]);
    expect(mocks.setMarkers).toHaveBeenCalledTimes(2);
  });
});
