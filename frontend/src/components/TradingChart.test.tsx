import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { TradingChart } from './TradingChart';
import type { ChartBar, LevelOverlay, MarkerOverlay } from '../chart/viewModels';

const mocks = vi.hoisted(() => ({
  createChart: vi.fn(),
  addSeries: vi.fn(),
  setData: vi.fn(),
  update: vi.fn(),
  remove: vi.fn(),
  createPriceLine: vi.fn((options) => ({ options })),
  removePriceLine: vi.fn(),
  createSeriesMarkers: vi.fn(),
  setMarkers: vi.fn(),
  removeMarkers: vi.fn(),
}));

vi.mock('lightweight-charts', () => ({
  CandlestickSeries: 'CandlestickSeries',
  ColorType: { Solid: 'solid' },
  CrosshairMode: { Normal: 1 },
  createChart: mocks.createChart,
  createSeriesMarkers: mocks.createSeriesMarkers,
}));

const chartBar = (key: string, time: number, close: number): ChartBar => ({ key, timeframe: 147, time: time as ChartBar['time'], open: close - 1, high: close + 1, low: close - 2, close, complete: true, openTimeUtc: '2026-05-21T14:00:00Z', closeTimeUtc: '2026-05-21T14:00:10Z' });

const renderChart = (props?: Partial<React.ComponentProps<typeof TradingChart>>) => render(<TradingChart timeframe={147} bars={[]} levels={[]} markers={[]} emptyTitle="empty" emptySubtitle="offline" {...props} />);

describe('TradingChart', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.addSeries.mockReset();
    mocks.createChart.mockReset();
    mocks.createSeriesMarkers.mockReset();
    mocks.addSeries.mockReturnValue({ setData: mocks.setData, update: mocks.update, createPriceLine: mocks.createPriceLine, removePriceLine: mocks.removePriceLine });
    mocks.createChart.mockReturnValue({ addSeries: mocks.addSeries, remove: mocks.remove });
    mocks.createSeriesMarkers.mockReturnValue({ setMarkers: mocks.setMarkers, remove: mocks.removeMarkers });
  });

  afterEach(() => {
    cleanup();
  });

  it('sets full data on first render and timeframe changes', () => {
    const first = [chartBar('a', 1, 100), chartBar('b', 2, 101)];
    const { rerender } = renderChart({ bars: first });

    expect(mocks.setData).toHaveBeenCalledWith(first);

    const next = [{ ...chartBar('x', 3, 102), timeframe: 987 as const }];
    rerender(<TradingChart timeframe={987} bars={next} levels={[]} markers={[]} emptyTitle="empty" emptySubtitle="offline" />);

    expect(mocks.setData).toHaveBeenLastCalledWith(next);
  });

  it('creates one chart and candlestick series on mount even when props rerender', () => {
    const first = [chartBar('a', 1, 100)];
    const { rerender } = renderChart({ bars: first });

    rerender(<TradingChart timeframe={147} bars={[chartBar('a', 1, 101)]} levels={[]} markers={[]} emptyTitle="empty" emptySubtitle="offline" />);
    rerender(<TradingChart timeframe={147} bars={[chartBar('a', 1, 102)]} levels={[]} markers={[]} emptyTitle="empty" emptySubtitle="offline" />);

    expect(mocks.createChart).toHaveBeenCalledOnce();
    expect(mocks.addSeries).toHaveBeenCalledOnce();
    expect(mocks.createSeriesMarkers).toHaveBeenCalledOnce();
  });

  it('sets full data when history shrinks or the stable prefix changes', () => {
    const first = [chartBar('a', 1, 100), chartBar('b', 2, 101), chartBar('c', 3, 102)];
    const { rerender } = renderChart({ bars: first });
    mocks.setData.mockClear();
    mocks.update.mockClear();

    const shrunk = [chartBar('b', 2, 101), chartBar('c', 3, 102)];
    rerender(<TradingChart timeframe={147} bars={shrunk} levels={[]} markers={[]} emptyTitle="empty" emptySubtitle="offline" />);
    expect(mocks.setData).toHaveBeenLastCalledWith(shrunk);

    mocks.setData.mockClear();
    const incompatible = [chartBar('x', 1, 100), chartBar('c', 3, 103)];
    rerender(<TradingChart timeframe={147} bars={incompatible} levels={[]} markers={[]} emptyTitle="empty" emptySubtitle="offline" />);
    expect(mocks.setData).toHaveBeenLastCalledWith(incompatible);
    expect(mocks.update).not.toHaveBeenCalled();
  });

  it('uses incremental update for current candle updates and closed-bar appends', () => {
    const first = [chartBar('a', 1, 100), chartBar('b', 2, 101)];
    const { rerender } = renderChart({ bars: first });
    mocks.update.mockClear();

    const updated = [first[0], chartBar('b', 2, 105)];
    rerender(<TradingChart timeframe={147} bars={updated} levels={[]} markers={[]} emptyTitle="empty" emptySubtitle="offline" />);
    expect(mocks.update).toHaveBeenCalledWith(updated[1]);

    const appended = [...updated, chartBar('c', 3, 106)];
    rerender(<TradingChart timeframe={147} bars={appended} levels={[]} markers={[]} emptyTitle="empty" emptySubtitle="offline" />);
    expect(mocks.update).toHaveBeenCalledWith(updated[1]);
    expect(mocks.update).toHaveBeenCalledWith(appended[2]);
  });

  it('uses series.update instead of setData when an in-progress candle keeps its key and open-time coordinate', () => {
    const first = [{ ...chartBar('147:2026-05-21:2026-05-21T14:00:00Z', 1779372000, 100), complete: false }];
    const { rerender } = renderChart({ bars: first });
    mocks.setData.mockClear();
    mocks.update.mockClear();

    const updated = [{ ...chartBar('147:2026-05-21:2026-05-21T14:00:00Z', 1779372000, 101), complete: false }];
    rerender(<TradingChart timeframe={147} bars={updated} levels={[]} markers={[]} emptyTitle="empty" emptySubtitle="offline" />);

    expect(mocks.setData).not.toHaveBeenCalled();
    expect(mocks.update).toHaveBeenCalledOnce();
    expect(mocks.update).toHaveBeenCalledWith(updated[0]);
  });

  it('sets full data when the final logical bar changes without array growth', () => {
    const closedA = chartBar('a', 1, 100);
    const currentB = { ...chartBar('b', 2, 101), complete: false };
    const currentC = { ...chartBar('c', 3, 102), complete: false };
    const { rerender } = renderChart({ bars: [closedA, currentB] });
    mocks.setData.mockClear();
    mocks.update.mockClear();

    rerender(<TradingChart timeframe={147} bars={[closedA, currentC]} levels={[]} markers={[]} emptyTitle="empty" emptySubtitle="offline" />);

    expect(mocks.setData).toHaveBeenCalledOnce();
    expect(mocks.setData).toHaveBeenCalledWith([closedA, currentC]);
    expect(mocks.update).not.toHaveBeenCalled();
  });

  it('updates and removes level price lines', () => {
    const eligible: LevelOverlay = { id: 'pdh', price: 19000, title: 'PDH EL', color: '#fff', lineWidth: 2, lineStyle: 0, eligible: true };
    const display: LevelOverlay = { id: 'asia', price: 18950, title: 'ASIA DISP', color: '#708194', lineWidth: 1, lineStyle: 2, eligible: false };
    const { rerender } = renderChart({ levels: [eligible, display] });

    expect(mocks.createPriceLine).toHaveBeenCalledWith(expect.objectContaining({ price: 19000, lineWidth: 2, lineStyle: 0 }));
    expect(mocks.createPriceLine).toHaveBeenCalledWith(expect.objectContaining({ price: 18950, lineWidth: 1, lineStyle: 2 }));

    rerender(<TradingChart timeframe={147} bars={[]} levels={[eligible]} markers={[]} emptyTitle="empty" emptySubtitle="offline" />);

    expect(mocks.removePriceLine).toHaveBeenCalled();
  });

  it('sets marker overlays and cleans up chart resources', () => {
    const markers: MarkerOverlay[] = [{ id: 'touch:t1', time: 1 as MarkerOverlay['time'], position: 'belowBar', shape: 'arrowUp', color: '#36d399', text: 'touch' }];
    const { unmount } = renderChart({ markers });

    expect(mocks.setMarkers).toHaveBeenCalledWith(markers);

    unmount();

    expect(mocks.removeMarkers).toHaveBeenCalled();
    expect(mocks.remove).toHaveBeenCalled();
  });

  it('removes all created price lines on unmount', () => {
    const eligible: LevelOverlay = { id: 'pdh', price: 19000, title: 'PDH EL', color: '#fff', lineWidth: 2, lineStyle: 0, eligible: true };
    const display: LevelOverlay = { id: 'asia', price: 18950, title: 'ASIA DISP', color: '#708194', lineWidth: 1, lineStyle: 2, eligible: false };
    const { unmount } = renderChart({ levels: [eligible, display] });
    const createdLines = mocks.createPriceLine.mock.results.map((result) => result.value);

    unmount();

    expect(mocks.removePriceLine).toHaveBeenCalledWith(createdLines[0]);
    expect(mocks.removePriceLine).toHaveBeenCalledWith(createdLines[1]);
  });

  it('renders an empty offline state without throwing', () => {
    expect(() => renderChart({ bars: [], emptyTitle: 'Runtime snapshot idle', emptySubtitle: 'Backend offline · WebSocket offline' })).not.toThrow();

    expect(screen.getByText('Runtime snapshot idle')).toBeInTheDocument();
    expect(screen.getByText('Backend offline · WebSocket offline')).toBeInTheDocument();
    expect(screen.getByTestId('trading-chart-canvas')).toBeInTheDocument();
  });
});
