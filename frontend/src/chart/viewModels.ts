import type { CandlestickData, SeriesMarker, Time, UTCTimestamp } from 'lightweight-charts';
import type { ClosedExecution, MarketBar, MarketLevel, MarketTouch, Observation, OpenPosition, Prediction, Timeframe } from '../domain/models';

export const PRICE_TICK_SIZE = 0.25;
// Sized to hold 2 full trading days (plus the current partial) per timeframe — the live
// warm-start replays two full prior trading days; matches the backend recent_closed_bar_limit
// so the chart never truncates below what the backend retains and sends.
export const MAX_BARS_PER_TIMEFRAME = 8_000;

export type ChartBar = CandlestickData<Time> & {
  key: string;
  timeframe: Timeframe;
  complete: boolean;
  // Raw wall-clock bounds are retained so chart annotations (markers) can be
  // mapped back onto each bar's synthetic chart-time coordinate.
  openTimeUtc: string;
  closeTimeUtc: string;
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
      openTimeUtc: bar.openTimeUtc,
      closeTimeUtc: bar.closeTimeUtc,
    }));
};

// Markers carry wall-clock event timestamps, but bars live on a synthetic
// monotonic chart-time axis (midnight + bar_index) so same-second tick bars stay
// distinct. This resolver maps an event timestamp onto the chart time of the bar
// that contains it. Bars arrive already sorted ascending by chart time, which
// matches open-time order.
//
// Events OLDER than the first loaded bar resolve to null (the marker is dropped),
// NOT to bars[0]. The chart only holds a recent window — the snapshot does not
// carry bars back to prior sessions — so kept predictions/outcomes (up to ~100,
// spanning many hours) routinely predate bars[0] by hours. Clamping them to bars[0]
// stacked every one onto the leftmost candle (an unreadable, misleading pile that
// implied they all happened at the window start). Dropping is honest: an event with
// no loaded bar gets no marker; in-window events still land on their real bar.
export const createChartTimeResolver = (bars: ChartBar[]): ((iso: string) => UTCTimestamp | null) => {
  const opens = bars.map((bar) => Date.parse(bar.openTimeUtc));
  return (iso: string) => {
    if (bars.length === 0) return null;
    const t = Date.parse(iso);
    if (!Number.isFinite(t)) return null;
    if (t < opens[0]) return null;
    let index = 0;
    for (let i = 0; i < bars.length; i += 1) {
      if (opens[i] <= t) index = i;
      else break;
    }
    return bars[index].time as UTCTimestamp;
  };
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

export const normalizeTouchMarkers = (touches: MarketTouch[], resolve: (iso: string) => UTCTimestamp | null): MarkerOverlay[] => {
  const deduped = new Map<string, MarketTouch>();
  for (const touch of touches) deduped.set(touchMarkerKey(touch), touch);
  return [...deduped.values()]
    .sort((a, b) => new Date(a.timeUtc).getTime() - new Date(b.timeUtc).getTime())
    .flatMap((touch) => {
      const time = resolve(touch.timeUtc);
      if (time === null) return [];
      return [{
        id: `touch:${touchMarkerKey(touch)}`,
        time,
        position: touch.createdObservation ? 'belowBar' : 'aboveBar',
        shape: touch.createdObservation ? 'arrowUp' : 'circle',
        color: touch.createdObservation ? '#36d399' : '#f2b84b',
        text: `${formatLevelKind(touch.levelKind)} touch`,
      } satisfies MarkerOverlay];
    });
};

export const normalizeObservationMarkers = (observations: Observation[], resolve: (iso: string) => UTCTimestamp | null): MarkerOverlay[] => {
  const deduped = new Map<string, Observation>();
  for (const observation of observations) deduped.set(observation.id, observation);
  return [...deduped.values()]
    .sort((a, b) => new Date(a.startUtc).getTime() - new Date(b.startUtc).getTime())
    .flatMap((observation) => {
      // Anchor to the observation end so "obs expired/completed" lands on the bar
      // where it resolves rather than doubling up on the touch marker.
      const time = resolve(observation.scheduledEndUtc || observation.startUtc);
      if (time === null) return [];
      return [{
        id: `observation:${observation.id}`,
        time,
        position: observation.status === 'active' ? 'belowBar' : 'aboveBar',
        shape: 'square',
        color: observation.status === 'active' ? '#4ea1ff' : '#7c8b9b',
        text: `${formatLevelKind(observation.levelKind)} obs ${observation.status}`,
      } satisfies MarkerOverlay];
    });
};

// Prediction markers are anchored to the touch bar (the prediction event ts is the
// touch ts). Eligible predictions get a filled diamond; ineligible ones a hollow
// dot so the "scored but not actionable" case stays visually distinct.
export const normalizePredictionMarkers = (predictions: Prediction[], resolve: (iso: string) => UTCTimestamp | null): MarkerOverlay[] => {
  const deduped = new Map<string, Prediction>();
  for (const prediction of predictions) deduped.set(prediction.id, prediction);
  return [...deduped.values()]
    .sort((a, b) => new Date(a.timeUtc).getTime() - new Date(b.timeUtc).getTime())
    .flatMap((prediction) => {
      const time = resolve(prediction.timeUtc);
      if (time === null) return [];
      const color = prediction.eligible ? predictionColor(prediction.predictedClass) : '#7c8b9b';
      return [{
        id: `prediction:${prediction.id}`,
        time,
        position: 'aboveBar',
        shape: prediction.eligible ? 'arrowDown' : 'circle',
        color,
        text: `pred ${prediction.predictedClass}${prediction.eligible ? '' : ' (ineligible)'}`,
      } satisfies MarkerOverlay];
    });
};

// Outcome markers reuse the touch-bar anchor (resolved from the originating
// prediction) so the resolved state lands on the same candle as its prediction.
export const normalizeOutcomeMarkers = (predictions: Prediction[], resolve: (iso: string) => UTCTimestamp | null): MarkerOverlay[] => {
  const deduped = new Map<string, Prediction>();
  for (const prediction of predictions) if (prediction.outcome) deduped.set(prediction.outcome.id, prediction);
  return [...deduped.values()]
    .sort((a, b) => new Date(a.timeUtc).getTime() - new Date(b.timeUtc).getTime())
    .flatMap((prediction) => {
      const outcome = prediction.outcome;
      if (!outcome) return [];
      const time = resolve(prediction.timeUtc);
      if (time === null) return [];
      return [{
        id: `outcome:${outcome.id}`,
        time,
        position: 'belowBar',
        shape: outcome.correct ? 'arrowUp' : 'arrowDown',
        color: outcome.correct ? '#36d399' : '#ff6b6b',
        text: `${outcome.correct ? 'correct' : 'miss'} ${outcome.actualClass}`,
      } satisfies MarkerOverlay];
    });
};

// Paper-execution markers (EXEC P3b) are PRICE-anchored (lightweight-charts v5
// SeriesMarkerPrice) — they sit at the actual fill price rather than hugging a
// bar edge, which keeps them visually distinct from every bar-anchored
// touch/observation/prediction/outcome glyph. Entry = pale circle at the honest
// fill; exit = square at the exit fill, green/red by realized optimistic points.
const EXECUTION_ENTRY_COLOR = '#e8f1f8';

export const normalizeExecutionMarkers = (
  openPositions: OpenPosition[],
  closed: ClosedExecution[],
  resolve: (iso: string) => UTCTimestamp | null,
): MarkerOverlay[] => {
  const markers: MarkerOverlay[] = [];
  const entrySeen = new Set<string>();
  const pushEntry = (predictionId: string, direction: string, entryTsUtc: string, entryPrice: number, open: boolean) => {
    if (entrySeen.has(predictionId)) return;
    entrySeen.add(predictionId);
    const time = resolve(entryTsUtc);
    if (time === null) return;
    markers.push({
      id: `exec-entry:${predictionId}`,
      time,
      position: 'atPriceMiddle',
      price: entryPrice,
      shape: 'circle',
      color: EXECUTION_ENTRY_COLOR,
      text: `${open ? 'open ' : ''}${direction} @ ${entryPrice.toFixed(2)}`,
    } satisfies MarkerOverlay);
  };
  const sortedOpen = [...openPositions].sort((a, b) => new Date(a.entryTsUtc).getTime() - new Date(b.entryTsUtc).getTime());
  const sortedClosed = [...closed].sort((a, b) => new Date(a.entryTsUtc).getTime() - new Date(b.entryTsUtc).getTime());
  // Closed executions first so a just-closed position keeps one entry marker
  // even if a stale open card briefly coexists in the stores.
  for (const execution of sortedClosed) pushEntry(execution.predictionId, execution.direction, execution.entryTsUtc, execution.entryPrice, false);
  for (const position of sortedOpen) pushEntry(position.predictionId, position.direction, position.entryTsUtc, position.entryPrice, true);
  for (const execution of sortedClosed) {
    const time = resolve(execution.exitTsUtc ?? execution.entryTsUtc);
    if (time === null) continue;
    const win = execution.points > 0;
    markers.push({
      id: `exec-exit:${execution.predictionId}`,
      time,
      position: 'atPriceMiddle',
      price: execution.exitPrice,
      shape: 'square',
      color: win ? '#36d399' : '#ff6b6b',
      text: `exit ${win ? '+' : ''}${execution.points.toFixed(2)} pts (${execution.reason.replaceAll('_', ' ')})`,
    } satisfies MarkerOverlay);
  }
  return markers;
};

export const combineMarkers = (touches: MarketTouch[], observations: Observation[], predictions: Prediction[], bars: ChartBar[], openPositions: OpenPosition[] = [], closedExecutions: ClosedExecution[] = []): MarkerOverlay[] => {
  const resolve = createChartTimeResolver(bars);
  const deduped = new Map<string, MarkerOverlay>();
  for (const marker of [
    ...normalizeTouchMarkers(touches, resolve),
    ...normalizeObservationMarkers(observations, resolve),
    ...normalizePredictionMarkers(predictions, resolve),
    ...normalizeOutcomeMarkers(predictions, resolve),
    ...normalizeExecutionMarkers(openPositions, closedExecutions, resolve),
  ]) deduped.set(marker.id, marker);
  return [...deduped.values()].sort((a, b) => Number(a.time) - Number(b.time));
};

const isFiniteBar = (bar: MarketBar) => [bar.openTicks, bar.highTicks, bar.lowTicks, bar.closeTicks].every(Number.isFinite);

const levelId = (level: MarketLevel) => `${level.kind}:${level.priceTicks}:${level.tradingDay}:${level.originSession ?? 'none'}`;

const touchMarkerKey = (touch: MarketTouch) => touch.id || `${touch.timeUtc}:${touch.levelKind}:${touch.priceTicks}`;

const formatLevelKind = (kind: string) => kind.replaceAll('_', ' ').toUpperCase();

// Predicted-class colouring is purely cosmetic: hold/neutral classes stay amber,
// directional bullish/bearish classes map to green/red. Unknown labels fall back
// to the neutral blue so future class maps still render.
const predictionColor = (predictedClass: string): string => {
  const label = predictedClass.toLowerCase();
  if (/(up|long|bull|win|favou?rable|positive)/.test(label)) return '#36d399';
  if (/(down|short|bear|loss|adverse|negative)/.test(label)) return '#ff6b6b';
  if (/(hold|neutral|none|chop|flat)/.test(label)) return '#f2b84b';
  return '#4ea1ff';
};

const levelFamily = (kind: string, originSession: string | null): { color: string } => {
  const source = `${kind} ${originSession ?? ''}`.toLowerCase();
  if (source.includes('pdh') || source.includes('pdl')) return { color: '#d7e3ee' };
  if (source.includes('asia')) return { color: '#a78bfa' };
  if (source.includes('london')) return { color: '#38bdf8' };
  if (source.includes('ny')) return { color: '#36d399' };
  return { color: '#f2b84b' };
};
