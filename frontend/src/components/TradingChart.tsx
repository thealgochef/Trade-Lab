import { useEffect, useMemo, useRef } from 'react';
import { CandlestickSeries, ColorType, CrosshairMode, createChart, type IChartApi, type ISeriesApi } from 'lightweight-charts';
import { ChartOverlayManager } from '../chart/overlayManager';
import type { ChartBar, LevelOverlay, MarkerOverlay } from '../chart/viewModels';
import type { Timeframe } from '../domain/models';

type TradingChartProps = {
  timeframe: Timeframe;
  bars: ChartBar[];
  levels: LevelOverlay[];
  markers: MarkerOverlay[];
  emptyTitle: string;
  emptySubtitle: string;
};

export function TradingChart({ timeframe, bars, levels, markers, emptyTitle, emptySubtitle }: TradingChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const overlaysRef = useRef<ChartOverlayManager | null>(null);
  const previousBarsRef = useRef<{ timeframe: Timeframe; bars: ChartBar[] } | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    // lightweight-charts is imperative; this component owns chart/series
    // lifecycle so parent components pass normalized view models only.
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: '#071017' }, textColor: '#9fb0c2' },
      grid: { vertLines: { color: '#122231' }, horzLines: { color: '#122231' } },
      rightPriceScale: { borderColor: '#203244', scaleMargins: { top: 0.12, bottom: 0.12 } },
      timeScale: { borderColor: '#203244', timeVisible: true, secondsVisible: true },
      crosshair: { mode: CrosshairMode.Normal },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#36d399',
      downColor: '#ff6b6b',
      borderUpColor: '#36d399',
      borderDownColor: '#ff6b6b',
      wickUpColor: '#36d399',
      wickDownColor: '#ff6b6b',
    });
    chartRef.current = chart;
    seriesRef.current = series;
    overlaysRef.current = new ChartOverlayManager(series);

    return () => {
      overlaysRef.current?.destroy();
      overlaysRef.current = null;
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      previousBarsRef.current = null;
    };
  }, []);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    applyBarData(series, timeframe, bars, previousBarsRef.current);
    previousBarsRef.current = { timeframe, bars };
  }, [timeframe, bars]);

  useEffect(() => overlaysRef.current?.syncLevels(levels), [levels]);
  useEffect(() => overlaysRef.current?.syncMarkers(markers), [markers]);

  const legend = useMemo(() => ({ eligible: levels.filter((level) => level.eligible).length, display: levels.filter((level) => !level.eligible).length }), [levels]);

  return (
    <div className="chart-shell">
      <div ref={containerRef} className="chart-canvas" data-testid="trading-chart-canvas" />
      <div className="chart-legend" aria-label="Chart legend">
        <span><i className="legend-line eligible" /> Eligible {legend.eligible}</span>
        <span><i className="legend-line display" /> Display-only {legend.display}</span>
        <span><i className="legend-marker" /> Touch / observation</span>
      </div>
      {bars.length === 0 && (
        <div className="empty-overlay">
          <strong>{emptyTitle}</strong>
          <span>{emptySubtitle}</span>
          <small>Waiting for Phase 2C OHLC tick-bar snapshots or deltas; raw ticks are not required for rendering.</small>
        </div>
      )}
    </div>
  );
}

const applyBarData = (series: ISeriesApi<'Candlestick'>, timeframe: Timeframe, bars: ChartBar[], previous: { timeframe: Timeframe; bars: ChartBar[] } | null) => {
  if (!previous || previous.timeframe !== timeframe || shouldReplaceBarData(previous.bars, bars)) {
    series.setData(bars);
    return;
  }

  if (bars.length === 0) {
    series.setData([]);
    return;
  }

  const start = Math.max(previous.bars.length - 1, 0);
  for (const bar of bars.slice(start)) series.update(bar);
};

const shouldReplaceBarData = (previous: ChartBar[], next: ChartBar[]) => {
  if (next.length < previous.length) return true;

  if (next.length === previous.length) {
    if (next.length === 0) return false;
    return !hasMatchingPrefix(previous, next, previous.length - 1) || !isSameLogicalBar(previous[previous.length - 1], next[next.length - 1]);
  }

  if (next.length === previous.length + 1) {
    return !hasMatchingPrefix(previous, next, previous.length);
  }

  return true;
};

const hasMatchingPrefix = (previous: ChartBar[], next: ChartBar[], length: number) => {
  for (let index = 0; index < length; index += 1) {
    if (!isSameLogicalBar(previous[index], next[index])) return false;
  }
  return true;
};

const isSameLogicalBar = (previous: ChartBar | undefined, next: ChartBar | undefined) => {
  return previous?.key === next?.key && previous?.time === next?.time;
};
