// COCKPIT-FIX F4: display-only axis time. The chart's time coordinate is
// SYNTHETIC — trading-day midnight UTC + bar_index seconds (chartTimestamp in
// viewModels.ts) — so axis tick and crosshair labels resolve each tick back to
// its bar's real wall-clock open before formatting in America/Chicago. This
// module never touches the coordinate itself: bars, markers, resolvers, and
// session bands all keep living on the synthetic axis.
import type { TickMarkType, Time } from 'lightweight-charts';
import type { ChartBar } from './viewModels';

export const CT_TIME_ZONE = 'America/Chicago';

// TickMarkType is imported type-only (component tests mock the module), so the
// lightweight-charts 5.x numeric contract is restated here: Year=0, Month=1,
// DayOfMonth=2, Time=3, TimeWithSeconds=4.
const YEAR = 0;
const MONTH = 1;
const DAY_OF_MONTH = 2;
const TIME_WITH_SECONDS = 4;

// Chart-time (synthetic seconds) -> real epoch ms of the bar's open. Bars whose
// time fell back to a real epoch (no bar_index) land in the map identically, so
// lookups never need to know which branch produced the coordinate.
export const buildAxisTimeIndex = (bars: ChartBar[]): Map<number, number> => {
  const index = new Map<number, number>();
  for (const bar of bars) {
    const at = Date.parse(bar.openTimeUtc);
    if (Number.isFinite(at)) index.set(Number(bar.time), at);
  }
  return index;
};

// True when a key the previous index knew about now resolves differently
// (remapped or gone). lightweight-charts memoizes tick labels by time key and
// only flushes that cache on applyOptions — never on setData — and synthetic
// keys collide across timeframes (same trading-day midnight + bar_index), so
// the caller must flush when this returns true or the axis keeps rendering the
// previous mapping's wall clock. Pure appends (the live hot path) return false.
export const axisIndexInvalidated = (previous: Map<number, number>, next: Map<number, number>): boolean => {
  for (const [key, at] of previous) {
    if (next.get(key) !== at) return true;
  }
  return false;
};

const CT_TIME = new Intl.DateTimeFormat('en-US', { timeZone: CT_TIME_ZONE, hour: 'numeric', minute: '2-digit', hour12: true });
const CT_TIME_SECONDS = new Intl.DateTimeFormat('en-US', { timeZone: CT_TIME_ZONE, hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: true });
const CT_DATE = new Intl.DateTimeFormat('en-US', { timeZone: CT_TIME_ZONE, month: 'short', day: 'numeric' });
const CT_MONTH = new Intl.DateTimeFormat('en-US', { timeZone: CT_TIME_ZONE, month: 'short', year: 'numeric' });
const CT_YEAR = new Intl.DateTimeFormat('en-US', { timeZone: CT_TIME_ZONE, year: 'numeric' });

const partsOf = (formatter: Intl.DateTimeFormat, epochMs: number): Partial<Record<Intl.DateTimeFormatPartTypes, string>> => {
  const out: Partial<Record<Intl.DateTimeFormatPartTypes, string>> = {};
  for (const part of formatter.formatToParts(epochMs)) out[part.type] = part.value;
  return out;
};

export const formatCtTime = (epochMs: number, withSeconds = false): string => {
  const parts = partsOf(withSeconds ? CT_TIME_SECONDS : CT_TIME, epochMs);
  const clock = withSeconds ? `${parts.hour}:${parts.minute}:${parts.second}` : `${parts.hour}:${parts.minute}`;
  return `${clock} ${(parts.dayPeriod ?? '').toLowerCase()}`;
};

export const formatCtDate = (epochMs: number): string => {
  const parts = partsOf(CT_DATE, epochMs);
  return `${parts.month} ${parts.day}`;
};

// Axis tick label: intraday ticks show 12-hour CT wall time; day-boundary and
// coarser ticks show a date label instead. Ticks with no loaded bar (whitespace
// extrapolated past the data) render empty. TimeWithSeconds ticks — emitted
// only when tick spacing drops below a minute — keep their seconds, otherwise
// adjacent same-minute tick bars all render identical labels.
export const formatTickMarkCt = (index: Map<number, number>, time: Time, tickMarkType: TickMarkType): string => {
  const at = index.get(Number(time));
  if (at === undefined) return '';
  if (tickMarkType === YEAR) return partsOf(CT_YEAR, at).year ?? '';
  if (tickMarkType === MONTH) {
    const parts = partsOf(CT_MONTH, at);
    return `${parts.month} ${parts.year}`;
  }
  if (tickMarkType === DAY_OF_MONTH) return formatCtDate(at);
  return formatCtTime(at, tickMarkType === TIME_WITH_SECONDS);
};

// Crosshair label carries the date plus a seconds-precision clock — tick bars
// can share a wall-clock second, so the label is honest about the bar's real
// open instant while bar identity stays on the synthetic coordinate.
export const formatCrosshairCt = (index: Map<number, number>, time: Time): string => {
  const at = index.get(Number(time));
  if (at === undefined) return '';
  return `${formatCtDate(at)} · ${formatCtTime(at, true)}`;
};
