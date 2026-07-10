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
  subscribeCrosshairMove: vi.fn(),
  unsubscribeCrosshairMove: vi.fn(),
  timeScale: vi.fn(),
  scrollPosition: vi.fn(() => 0),
  getVisibleRange: vi.fn((): { from: number; to: number } | null => null),
  setVisibleRange: vi.fn(),
  fitContent: vi.fn(),
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
    mocks.addSeries.mockReturnValue({ setData: mocks.setData, update: mocks.update, createPriceLine: mocks.createPriceLine, removePriceLine: mocks.removePriceLine, attachPrimitive: vi.fn(), detachPrimitive: vi.fn() });
    mocks.scrollPosition.mockReturnValue(0);
    mocks.getVisibleRange.mockReturnValue(null);
    mocks.timeScale.mockReturnValue({ scrollPosition: mocks.scrollPosition, getVisibleRange: mocks.getVisibleRange, setVisibleRange: mocks.setVisibleRange, fitContent: mocks.fitContent });
    mocks.createChart.mockReturnValue({ addSeries: mocks.addSeries, remove: mocks.remove, subscribeCrosshairMove: mocks.subscribeCrosshairMove, unsubscribeCrosshairMove: mocks.unsubscribeCrosshairMove, timeScale: mocks.timeScale });
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

  it('does not fit on a tiny fresh load — the default zoom gives a readable rolling window, not a zoom onto the opening batch', () => {
    renderChart({ bars: [chartBar('a', 1, 100), chartBar('b', 2, 101), chartBar('c', 3, 102)] });
    expect(mocks.fitContent).not.toHaveBeenCalled();
  });

  it('fits on a substantial fresh load (e.g. a restart after the warm-up already completed) so all retained bars show', () => {
    const bars = Array.from({ length: 80 }, (_, index) => chartBar(`b${index}`, index + 1, 100 + index));
    renderChart({ bars });
    expect(mocks.fitContent).toHaveBeenCalled();
  });

  it('renders warm-up growth incrementally (no full redraw) and fits once when it settles', () => {
    const first = [chartBar('a', 1, 100)];
    const { rerender } = renderChart({ bars: first });
    mocks.setData.mockClear();
    mocks.update.mockClear();
    mocks.fitContent.mockClear();

    // A warm-up snapshot appends many bars onto the unchanged prefix → incremental update, NOT a
    // full setData redraw of the whole set, and no per-snapshot re-fit. This is the lag fix.
    const grown = [chartBar('a', 1, 100), chartBar('b', 2, 101), chartBar('c', 3, 102), chartBar('d', 4, 103)];
    rerender(<TradingChart timeframe={147} bars={grown} levels={[]} markers={[]} emptyTitle="empty" emptySubtitle="offline" />);
    expect(mocks.setData).not.toHaveBeenCalled();
    expect(mocks.update).toHaveBeenCalled();
    expect(mocks.fitContent).not.toHaveBeenCalled();

    // Settles into a single-bar live append → fit ONCE to reveal the full range.
    rerender(<TradingChart timeframe={147} bars={[...grown, chartBar('e', 5, 104)]} levels={[]} markers={[]} emptyTitle="empty" emptySubtitle="offline" />);
    expect(mocks.fitContent).toHaveBeenCalledTimes(1);
  });

  it('does not refit on a single live append', () => {
    const first = [chartBar('a', 1, 100), chartBar('b', 2, 101)];
    const { rerender } = renderChart({ bars: first });
    mocks.fitContent.mockClear();

    rerender(<TradingChart timeframe={147} bars={[...first, chartBar('c', 3, 102)]} levels={[]} markers={[]} emptyTitle="empty" emptySubtitle="offline" />);

    expect(mocks.update).toHaveBeenCalled();
    expect(mocks.fitContent).not.toHaveBeenCalled();
  });

  it('does not refit on a retention-cap eviction at the live edge (stable bar count, not a warm-up batch)', () => {
    const first = [chartBar('a', 1, 100), chartBar('b', 2, 101), chartBar('c', 3, 102)];
    const { rerender } = renderChart({ bars: first });
    mocks.fitContent.mockClear();

    const evicted = [chartBar('b', 2, 101), chartBar('c', 3, 102), chartBar('d', 4, 103)];
    rerender(<TradingChart timeframe={147} bars={evicted} levels={[]} markers={[]} emptyTitle="empty" emptySubtitle="offline" />);

    expect(mocks.setData).toHaveBeenCalledWith(evicted);
    expect(mocks.fitContent).not.toHaveBeenCalled();
  });

  it('does not refit a user who has scrolled back to inspect a label; it pins their window instead', () => {
    const first = [chartBar('a', 1, 100), chartBar('b', 2, 101), chartBar('c', 3, 102)];
    const { rerender } = renderChart({ bars: first });
    mocks.fitContent.mockClear();

    const scrolledRange = { from: 1, to: 2 };
    mocks.scrollPosition.mockReturnValue(-50); // scrolled back into history
    mocks.getVisibleRange.mockReturnValue(scrolledRange);

    const evicted = [chartBar('b', 2, 101), chartBar('c', 3, 102), chartBar('d', 4, 103)];
    rerender(<TradingChart timeframe={147} bars={evicted} levels={[]} markers={[]} emptyTitle="empty" emptySubtitle="offline" />);

    expect(mocks.fitContent).not.toHaveBeenCalled();
    expect(mocks.setVisibleRange).toHaveBeenCalledWith(scrolledRange);
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

  it('sets marker overlays with inline text labels and an enlarged glyph, and cleans up chart resources', () => {
    const markers: MarkerOverlay[] = [{ id: 'touch:t1', time: 1 as MarkerOverlay['time'], position: 'belowBar', shape: 'arrowUp', color: '#36d399', text: 'touch' }];
    const { unmount } = renderChart({ markers });

    // The inline `text` reaches lightweight-charts as an always-on label and the glyph is
    // enlarged (size: 2); `id` is preserved for hover hit-testing.
    expect(mocks.setMarkers).toHaveBeenCalledWith([{ id: 'touch:t1', time: 1, position: 'belowBar', shape: 'arrowUp', color: '#36d399', text: 'touch', size: 2 }]);

    const subscribedHandler = mocks.subscribeCrosshairMove.mock.calls.at(-1)?.[0];
    unmount();

    // Must unsubscribe the SAME handler reference (lightweight-charts removes by
    // identity); a call-count-only assertion would miss a leaked listener.
    expect(mocks.unsubscribeCrosshairMove).toHaveBeenCalledWith(subscribedHandler);
    expect(mocks.removeMarkers).toHaveBeenCalled();
    expect(mocks.remove).toHaveBeenCalled();
  });

  it('reveals a marker label tooltip on hover and hides it when the marker is not hovered', () => {
    const markers: MarkerOverlay[] = [{ id: 'prediction:p1', time: 5 as MarkerOverlay['time'], position: 'aboveBar', shape: 'arrowDown', color: '#36d399', text: 'pred tradeable_reversal (ineligible)' }];
    renderChart({ markers });

    const handler = mocks.subscribeCrosshairMove.mock.calls.at(-1)?.[0] as (param: unknown) => void;
    const tooltip = screen.getByTestId('marker-tooltip');
    expect(tooltip).toHaveStyle({ display: 'none' });

    handler({ hoveredObjectId: 'prediction:p1', point: { x: 120, y: 40 } });
    expect(tooltip).toHaveStyle({ display: 'block' });
    expect(tooltip).toHaveTextContent('pred tradeable_reversal (ineligible)');
    // Positioned at the crosshair point; jsdom has no layout (clientWidth/Height 0)
    // so neither the right-edge flip nor the top/bottom anchor engages.
    expect(tooltip.style.left).toBe('120px');
    expect(tooltip.style.top).toBe('40px');
    expect(tooltip.style.transform).toBe('translate(14px, -50%)');

    // Crosshair moves onto a bar with no marker → tooltip hides.
    handler({ hoveredObjectId: undefined, point: { x: 200, y: 60 } });
    expect(tooltip).toHaveStyle({ display: 'none' });

    // Re-show, then hover an unknown object id (e.g. a price line): a real
    // block→none transition, so this proves the map-miss branch hides the tooltip.
    handler({ hoveredObjectId: 'prediction:p1', point: { x: 120, y: 40 } });
    expect(tooltip).toHaveStyle({ display: 'block' });
    handler({ hoveredObjectId: 'not-a-marker', point: { x: 200, y: 60 } });
    expect(tooltip).toHaveStyle({ display: 'none' });
  });

  it('keeps the tooltip on-chart near the right, top, and bottom edges', () => {
    const markers: MarkerOverlay[] = [{ id: 'm', time: 5 as MarkerOverlay['time'], position: 'aboveBar', shape: 'circle', color: '#7c8b9b', text: 'pred x (ineligible)' }];
    renderChart({ markers });
    const container = screen.getByTestId('trading-chart-canvas');
    Object.defineProperty(container, 'clientWidth', { configurable: true, value: 600 });
    Object.defineProperty(container, 'clientHeight', { configurable: true, value: 400 });
    const handler = mocks.subscribeCrosshairMove.mock.calls.at(-1)?.[0] as (param: unknown) => void;
    const tooltip = screen.getByTestId('marker-tooltip');

    handler({ hoveredObjectId: 'm', point: { x: 560, y: 200 } }); // x > 600-180 → flip left
    expect(tooltip.style.transform).toBe('translate(calc(-100% - 14px), -50%)');
    handler({ hoveredObjectId: 'm', point: { x: 100, y: 10 } });   // y < 24 → anchor top edge
    expect(tooltip.style.transform).toBe('translate(14px, 0%)');
    handler({ hoveredObjectId: 'm', point: { x: 100, y: 395 } });  // y > 400-24 → anchor bottom edge
    expect(tooltip.style.transform).toBe('translate(14px, -100%)');
  });

  it('reconciles an open tooltip when markers refresh under a stationary cursor', () => {
    const active: MarkerOverlay = { id: 'observation:o1', time: 5 as MarkerOverlay['time'], position: 'belowBar', shape: 'square', color: '#4ea1ff', text: 'PDH obs active' };
    const other: MarkerOverlay = { id: 'prediction:p1', time: 6 as MarkerOverlay['time'], position: 'aboveBar', shape: 'circle', color: '#7c8b9b', text: 'pred x (ineligible)' };
    const { rerender } = renderChart({ markers: [active, other] });
    const handler = mocks.subscribeCrosshairMove.mock.calls.at(-1)?.[0] as (param: unknown) => void;
    const tooltip = screen.getByTestId('marker-tooltip');

    handler({ hoveredObjectId: 'observation:o1', point: { x: 80, y: 40 } });
    expect(tooltip).toHaveTextContent('PDH obs active');

    // Same id, label flips (active→expired): text updates in place, stays visible.
    rerender(<TradingChart timeframe={147} bars={[]} levels={[]} markers={[{ ...active, text: 'PDH obs expired' }, other]} emptyTitle="empty" emptySubtitle="offline" />);
    expect(tooltip).toHaveStyle({ display: 'block' });
    expect(tooltip).toHaveTextContent('PDH obs expired');

    // Hovered marker dropped from the refreshed set: tooltip hides.
    rerender(<TradingChart timeframe={147} bars={[]} levels={[]} markers={[other]} emptyTitle="empty" emptySubtitle="offline" />);
    expect(tooltip).toHaveStyle({ display: 'none' });
  });

  it('keeps the tooltip hidden when hovering a marker that has no label text', () => {
    const markers: MarkerOverlay[] = [{ id: 'm', time: 5 as MarkerOverlay['time'], position: 'aboveBar', shape: 'circle', color: '#7c8b9b' }];
    renderChart({ markers });
    const handler = mocks.subscribeCrosshairMove.mock.calls.at(-1)?.[0] as (param: unknown) => void;
    const tooltip = screen.getByTestId('marker-tooltip');

    handler({ hoveredObjectId: 'm', point: { x: 100, y: 50 } });
    expect(tooltip).toHaveStyle({ display: 'none' });
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
