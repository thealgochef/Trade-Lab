import { useEffect, useMemo, useRef } from 'react';
import { CandlestickSeries, ColorType, CrosshairMode, createChart, type IChartApi, type ISeriesApi, type MouseEventParams } from 'lightweight-charts';
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
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  // Marker labels are no longer drawn inline (they overlapped); this id→text map
  // backs the hover tooltip, keyed by the same marker id lightweight-charts
  // reports via MouseEventParams.hoveredObjectId.
  const markerLabelsRef = useRef<Map<string, string>>(new Map());
  // Which marker the tooltip is currently showing, so a markers refresh while the
  // cursor sits still can reconcile a stale/removed label (no crosshair event fires
  // for a stationary cursor when setMarkers runs).
  const hoveredMarkerIdRef = useRef<string | null>(null);

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

    // Markers carry shape + colour inline but their full label now lives in a
    // hover tooltip. param.point is relative to the chart canvas, which fills the
    // position:relative .chart-shell (inset:0), so it maps straight to the
    // absolutely-positioned tooltip's coordinates.
    const handleCrosshairMove = (param: MouseEventParams) => {
      const tooltip = tooltipRef.current;
      if (!tooltip) return;
      const id = param.hoveredObjectId;
      const label = typeof id === 'string' ? markerLabelsRef.current.get(id) : undefined;
      if (!label || !param.point) {
        tooltip.style.display = 'none';
        hoveredMarkerIdRef.current = null;
        return;
      }
      hoveredMarkerIdRef.current = id as string;
      tooltip.textContent = label;
      tooltip.style.display = 'block';
      tooltip.style.left = `${param.point.x}px`;
      tooltip.style.top = `${param.point.y}px`;
      // Keep the tooltip on-chart: flip left near the right edge, and anchor its top
      // or bottom edge (instead of centring) near the top/bottom so it can't spill
      // off the pane or over the legend.
      const width = containerRef.current?.clientWidth ?? 0;
      const height = containerRef.current?.clientHeight ?? 0;
      const tx = width > 0 && param.point.x > width - 180 ? 'calc(-100% - 14px)' : '14px';
      const ty = height > 0 && param.point.y < 24 ? '0%' : height > 0 && param.point.y > height - 24 ? '-100%' : '-50%';
      tooltip.style.transform = `translate(${tx}, ${ty})`;
    };
    chart.subscribeCrosshairMove(handleCrosshairMove);

    return () => {
      chart.unsubscribeCrosshairMove(handleCrosshairMove);
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
  useEffect(() => {
    overlaysRef.current?.syncMarkers(markers);
    const labels = new Map<string, string>();
    for (const marker of markers) if (marker.text) labels.set(marker.id, marker.text);
    markerLabelsRef.current = labels;

    // Reconcile a tooltip that is open over a now-refreshed marker set: hide it if the
    // hovered marker is gone, or update its text in place if the label changed.
    const tooltip = tooltipRef.current;
    const hoveredId = hoveredMarkerIdRef.current;
    if (tooltip && hoveredId && tooltip.style.display === 'block') {
      const label = labels.get(hoveredId);
      if (!label) {
        tooltip.style.display = 'none';
        hoveredMarkerIdRef.current = null;
      } else if (tooltip.textContent !== label) {
        tooltip.textContent = label;
      }
    }
  }, [markers]);

  const legend = useMemo(() => ({ eligible: levels.filter((level) => level.eligible).length, display: levels.filter((level) => !level.eligible).length }), [levels]);

  return (
    <div className="chart-shell">
      <div ref={containerRef} className="chart-canvas" data-testid="trading-chart-canvas" />
      <div ref={tooltipRef} className="chart-marker-tooltip" data-testid="marker-tooltip" style={{ display: 'none' }} />
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
