import { useEffect, useMemo, useRef } from 'react';
import { CandlestickSeries, ColorType, CrosshairMode, createChart, type IChartApi, type ISeriesApi, type MouseEventParams, type TickMarkType, type Time } from 'lightweight-charts';
import { ChartOverlayManager } from '../chart/overlayManager';
import { axisIndexInvalidated, buildAxisTimeIndex, formatCrosshairCt, formatTickMarkCt } from '../chart/axisTime';
import { sessionBands, type ChartBar, type LevelOverlay, type MarkerOverlay, type PositionLineOverlay } from '../chart/viewModels';
import type { Timeframe } from '../domain/models';

// Treat the viewport as "parked at the live edge" while its right edge sits within this many
// bars of the newest bar. timeScale.scrollPosition() returns that right offset in bars: ~0
// while following the latest bar, increasingly negative once the user scrolls back to inspect.
const FOLLOW_EDGE_BARS = 2;
// On a fresh load carrying at least this many bars (e.g. a restart after the warm-up already
// completed, or any substantial snapshot), frame the whole retained range. Below it — the tiny
// opening batch at the start of a streaming warm-up — we skip the fit so the chart doesn't zoom
// onto 2-3 bars; the settle branch reveals the full range once that warm-up finishes growing.
const REVEAL_MIN_BARS = 50;

type TradingChartProps = {
  timeframe: Timeframe;
  bars: ChartBar[];
  levels: LevelOverlay[];
  markers: MarkerOverlay[];
  // COCKPIT P4a: TP/SL barrier lines for open paper positions.
  positionLines?: PositionLineOverlay[];
  emptyTitle: string;
  emptySubtitle: string;
};

const NO_POSITION_LINES: PositionLineOverlay[] = [];

export function TradingChart({ timeframe, bars, levels, markers, positionLines = NO_POSITION_LINES, emptyTitle, emptySubtitle }: TradingChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const overlaysRef = useRef<ChartOverlayManager | null>(null);
  const previousBarsRef = useRef<{ timeframe: Timeframe; bars: ChartBar[] } | null>(null);
  // True while warm-up / backfill snapshots are still growing the bar set. The reveal
  // fitContent() is deferred until they settle (the first incremental live update) rather
  // than re-laying-out the whole chart on every snapshot — which made a multi-session
  // warm-up (one big snapshot per second for minutes) lag badly.
  const warmGrowingRef = useRef(false);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  // Marker labels render inline (with an enlarged glyph); this id→text map still
  // backs the hover tooltip, keyed by the same marker id lightweight-charts
  // reports via MouseEventParams.hoveredObjectId.
  const markerLabelsRef = useRef<Map<string, string>>(new Map());
  // Which marker the tooltip is currently showing, so a markers refresh while the
  // cursor sits still can reconcile a stale/removed label (no crosshair event fires
  // for a stationary cursor when setMarkers runs).
  const hoveredMarkerIdRef = useRef<string | null>(null);
  // COCKPIT-FIX F4: chart-time -> real-open-epoch lookup for the axis/crosshair
  // formatters. A ref (refreshed with the bars) because the formatters are
  // installed once at createChart and must see the current bar set.
  const axisTimeIndexRef = useRef<Map<number, number>>(new Map());

  useEffect(() => {
    if (!containerRef.current) return;
    // lightweight-charts is imperative; this component owns chart/series
    // lifecycle so parent components pass normalized view models only.
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: '#071017' }, textColor: '#9fb0c2' },
      grid: { vertLines: { color: '#122231' }, horzLines: { color: '#122231' } },
      rightPriceScale: { borderColor: '#203244', scaleMargins: { top: 0.12, bottom: 0.12 } },
      // Display-only Central Time labels: ticks resolve to their bar's real
      // wall-clock open; the synthetic time coordinate itself is untouched.
      timeScale: {
        borderColor: '#203244',
        timeVisible: true,
        secondsVisible: true,
        tickMarkFormatter: (time: Time, tickMarkType: TickMarkType) => formatTickMarkCt(axisTimeIndexRef.current, time, tickMarkType),
      },
      localization: { timeFormatter: (time: Time) => formatCrosshairCt(axisTimeIndexRef.current, time) },
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
    const chart = chartRef.current;
    if (!series || !chart) return;
    // Refresh the axis-label lookup before the data reaches the series, so the
    // tick layout triggered by setData/update always resolves against the bars
    // it is laying out.
    const previousAxisIndex = axisTimeIndexRef.current;
    axisTimeIndexRef.current = buildAxisTimeIndex(bars);
    const previous = previousBarsRef.current;
    const timeScale = chart.timeScale();
    // lightweight-charts memoizes formatted tick labels by time key and only
    // flushes that cache via applyOptions — never on setData. When an existing
    // key remaps to a different real open (timeframe switch: the synthetic
    // coordinates collide across timeframes; replay repopulate), flush it or
    // the axis keeps showing the previous mapping's wall clock. Pure appends —
    // the live hot path — never trigger this.
    if (axisIndexInvalidated(previousAxisIndex, axisTimeIndexRef.current)) timeScale.applyOptions({});

    // A "fresh" series generation: the first bars, a timeframe switch, or a repopulate after the
    // store cleared (warm-up / replay (re)start).
    const fresh = !previous || previous.timeframe !== timeframe || previous.bars.length === 0;
    const following = timeScale.scrollPosition() >= -FOLLOW_EDGE_BARS;
    // A wholesale snapshot that grows the set by more than one bar is the warm-up filling in (or
    // a backfill) — as opposed to a single live append or a retention-cap shift.
    const batchGrew = !!previous && bars.length > previous.bars.length + 1;
    // While the user is scrolled back inspecting, capture their time window so a wholesale
    // setData below cannot drift it out from under them (e.g. a cap eviction). A time range
    // stays anchored to the same bars across that shift.
    const preserved = !fresh && !following && bars.length > 0 ? timeScale.getVisibleRange() : null;

    const replaced = applyBarData(series, timeframe, bars, previous);
    previousBarsRef.current = { timeframe, bars };
    if (bars.length === 0) {
      warmGrowingRef.current = false;
      return;
    }

    // Reveal the FULL retained range — so warm-up bars and the labels printed on them stay
    // reachable instead of scrolling off the live edge — but WITHOUT re-laying-out the chart on
    // every snapshot. Fit once on a fresh load; while warm-up snapshots keep growing the set
    // (which now render incrementally, not via setData), just remember it and fit ONCE when they
    // settle into single-bar live updates. fitContent() scales to the actual data (no dead
    // whitespace), and a user scrolled back to inspect a marker is never refit or yanked forward.
    if (fresh) {
      if (bars.length >= REVEAL_MIN_BARS) timeScale.fitContent();
      warmGrowingRef.current = false;
    } else if (following && batchGrew) {
      warmGrowingRef.current = true;
    } else if (warmGrowingRef.current && following && !batchGrew && !replaced) {
      timeScale.fitContent();
      warmGrowingRef.current = false;
    } else if (replaced && preserved) {
      timeScale.setVisibleRange(preserved);
    }
  }, [timeframe, bars]);

  useEffect(() => overlaysRef.current?.syncLevels(levels), [levels]);
  useEffect(() => overlaysRef.current?.syncPositionLines(positionLines), [positionLines]);
  // COCKPIT P4b: session shading bands recompute with the bar set (ET
  // classification is cached per UTC hour, so this is arithmetic per bar).
  const bands = useMemo(() => sessionBands(bars), [bars]);
  useEffect(() => overlaysRef.current?.syncSessionBands(bands), [bands]);
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
      <span className="chart-axis-tz" data-testid="axis-tz-label">CT</span>
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

// Returns true when the series was wholesale-replaced via setData (first load, timeframe switch,
// shrink/prefix change, retention-cap eviction, or a warm-up snapshot), false when only the
// trailing bars were incrementally updated. The caller uses this to decide whether the viewport
// needs (re)framing — only the wholesale-replace path can drop the user's history out of view.
const applyBarData = (series: ISeriesApi<'Candlestick'>, timeframe: Timeframe, bars: ChartBar[], previous: { timeframe: Timeframe; bars: ChartBar[] } | null): boolean => {
  if (!previous || previous.timeframe !== timeframe || previous.bars.length === 0 || shouldReplaceBarData(previous.bars, bars)) {
    series.setData(bars);
    return true;
  }

  if (bars.length === 0) {
    series.setData([]);
    return true;
  }

  const start = Math.max(previous.bars.length - 1, 0);
  for (const bar of bars.slice(start)) series.update(bar);
  return false;
};

const shouldReplaceBarData = (previous: ChartBar[], next: ChartBar[]) => {
  if (next.length < previous.length) return true;

  if (next.length === previous.length) {
    if (next.length === 0) return false;
    return !hasMatchingPrefix(previous, next, previous.length - 1) || !isSameLogicalBar(previous[previous.length - 1], next[next.length - 1]);
  }

  // next is longer: a clean append (the existing bars unchanged) is pushed incrementally — only
  // the new bars reach the series instead of redrawing the whole set, which keeps a multi-session
  // warm-up (snapshots that append many bars at once) smooth. A changed prefix (e.g. a
  // retention-cap eviction dropping the front bar) still needs a full setData.
  return !hasMatchingPrefix(previous, next, previous.length);
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
