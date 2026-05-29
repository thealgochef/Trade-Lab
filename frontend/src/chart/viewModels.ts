import type { CandlestickData, SeriesMarker, Time, UTCTimestamp } from 'lightweight-charts';
import type { MarketBar, MarketLevel, MarketTouch, Observation, Timeframe } from '../domain/models';

export const PRICE_TICK_SIZE = 0.25;
export const MAX_BARS_PER_TIMEFRAME = 2_500;

export type ChartBar = CandlestickData<Time> & {
  key: string;
  timeframe: Timeframe;
  complete: boolean;
};

export type LevelOverlay = {
  id: string;
  price: number;
  title: string;
  color: string;
  lineWidth: 1 | 2;
  lineStyle: 0 | 1 | 2 | 3 | 4;
  eligible: boolean;
};

export type MarkerOverlay = SeriesMarker<Time> & { id: string };

const SUPPORTED_TIMEFRAMES: Timeframe[] = [147, 987, 2000];

export const isSupportedTimeframe = (value: number): value is Timeframe => SUPPORTED_TIMEFRAMES.includes(value as Timeframe);

export const ticksToPrice = (ticks: number) => ticks * PRICE_TICK_SIZE;

const toTimestamp = (iso: string): UTCTimestamp => Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;

export const barKey = (bar: MarketBar): string => bar.barId ?? `${bar.timeframe}:${bar.tradingDay}:${bar.openTimeUtc}`;

const chartTimestamp = (bar: MarketBar): UTCTimestamp => {
  if (typeof bar.barIndex === 'number' && Number.isInteger(bar.barIndex) && bar.barIndex >= 0) {
    const base = Math.floor(Date.parse(`${bar.tradingDay}T00:00:00Z`) / 1000);
    if (Number.isFinite(base)) return (base + bar.barIndex) as UTCTimestamp;
  }
  return toTimestamp(bar.openTimeUtc);
};

const compareBars = (a: MarketBar, b: MarketBar): number => {
  if (a.tradingDay !== b.tradingDay) return a.tradingDay.localeCompare(b.tradingDay);
  if (typeof a.barIndex === 'number' && typeof b.barIndex === 'number' && a.barIndex !== b.barIndex) return a.barIndex - b.barIndex;
  return new Date(a.openTimeUtc).getTime() - new Date(b.openTimeUtc).getTime();
};

// Runtime tick bars are already aggregated by the backend Phase 2C contract; the
// chart intentionally consumes only OHLC bars and never needs raw ticks.
export const normalizeBarsForTimeframe = (bars: MarketBar[], timeframe: Timeframe, maxBars = MAX_BARS_PER_TIMEFRAME): ChartBar[] => {
  const deduped = new Map<string, MarketBar>();
  for (const bar of bars) {
    if (bar.timeframe !== timeframe || !isFiniteBar(bar)) continue;
    deduped.set(barKey(bar), bar);
  }

  return [...deduped.values()]
    .sort(compareBars)
    .slice(-maxBars)
    .map((bar) => ({
      key: barKey(bar),
      timeframe,
      // Tick bars can share wall-clock seconds, so chart time is a synthetic
      // monotonic coordinate based on backend bar_index when available.
      time: chartTimestamp(bar),
      open: ticksToPrice(bar.openTicks),
      high: ticksToPrice(bar.highTicks),
      low: ticksToPrice(bar.lowTicks),
      close: ticksToPrice(bar.closeTicks),
      complete: bar.complete,
    }));
};

export const normalizeLevels = (levels: MarketLevel[]): LevelOverlay[] => {
  const deduped = new Map<string, MarketLevel>();
  for (const level of levels) deduped.set(levelId(level), level);
  return [...deduped.values()].map((level) => {
    const family = levelFamily(level.kind, level.originSession);
    return {
      id: levelId(level),
      price: ticksToPrice(level.priceTicks),
      title: `${formatLevelKind(level.kind)} ${level.eligible ? 'EL' : 'DISP'}`,
      color: level.eligible ? family.color : '#708194',
      lineWidth: level.eligible ? 2 : 1,
      lineStyle: level.eligible ? 0 : 2,
      eligible: level.eligible,
    };
  });
};

export const normalizeTouchMarkers = (touches: MarketTouch[]): MarkerOverlay[] => {
  const deduped = new Map<string, MarketTouch>();
  for (const touch of touches) deduped.set(touchMarkerKey(touch), touch);
  return [...deduped.values()]
    .sort((a, b) => new Date(a.timeUtc).getTime() - new Date(b.timeUtc).getTime())
    .map((touch) => ({
      id: `touch:${touchMarkerKey(touch)}`,
      time: toTimestamp(touch.timeUtc),
      position: touch.createdObservation ? 'belowBar' : 'aboveBar',
      shape: touch.createdObservation ? 'arrowUp' : 'circle',
      color: touch.createdObservation ? '#36d399' : '#f2b84b',
      text: `${formatLevelKind(touch.levelKind)} touch`,
    }));
};

export const normalizeObservationMarkers = (observations: Observation[]): MarkerOverlay[] => {
  const deduped = new Map<string, Observation>();
  for (const observation of observations) deduped.set(observation.id, observation);
  return [...deduped.values()]
    .sort((a, b) => new Date(a.startUtc).getTime() - new Date(b.startUtc).getTime())
    .map((observation) => ({
      id: `observation:${observation.id}`,
      time: toTimestamp(observation.startUtc),
      position: observation.status === 'active' ? 'belowBar' : 'aboveBar',
      shape: 'square',
      color: observation.status === 'active' ? '#4ea1ff' : '#7c8b9b',
      text: `${formatLevelKind(observation.levelKind)} obs ${observation.status}`,
    }));
};

export const combineMarkers = (touches: MarketTouch[], observations: Observation[]): MarkerOverlay[] => {
  const deduped = new Map<string, MarkerOverlay>();
  for (const marker of [...normalizeTouchMarkers(touches), ...normalizeObservationMarkers(observations)]) deduped.set(marker.id, marker);
  return [...deduped.values()].sort((a, b) => Number(a.time) - Number(b.time));
};

const isFiniteBar = (bar: MarketBar) => [bar.openTicks, bar.highTicks, bar.lowTicks, bar.closeTicks].every(Number.isFinite);

const levelId = (level: MarketLevel) => `${level.kind}:${level.priceTicks}:${level.tradingDay}:${level.originSession ?? 'none'}`;

const touchMarkerKey = (touch: MarketTouch) => touch.id || `${touch.timeUtc}:${touch.levelKind}:${touch.priceTicks}`;

const formatLevelKind = (kind: string) => kind.replaceAll('_', ' ').toUpperCase();

const levelFamily = (kind: string, originSession: string | null): { color: string } => {
  const source = `${kind} ${originSession ?? ''}`.toLowerCase();
  if (source.includes('pdh') || source.includes('pdl')) return { color: '#d7e3ee' };
  if (source.includes('asia')) return { color: '#a78bfa' };
  if (source.includes('london')) return { color: '#38bdf8' };
  if (source.includes('ny')) return { color: '#36d399' };
  return { color: '#f2b84b' };
};
