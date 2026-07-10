// Pure view-model math for the Performance page (REPORT P4). No React, no IO —
// everything here is exercised directly by vitest.
//
// Trading-day semantics: day keys arriving from the backend are TRADING days on
// the 18:00 ET roll (a Sunday 19:00 ET trade already belongs to Monday's key by
// the time it reaches us). Calendar weeks are therefore trading weeks — Monday
// through Friday only; weekend keys cannot legitimately occur and are surfaced
// via `offGrid` instead of being silently placed or dropped.

import type {
  MeanStatDTO,
  PerformanceDayDTO,
  PerformanceExecutionsDTO,
  PerformanceHeadlineDTO,
  RatioDTO,
} from '../api/types';

export const DEFAULT_DOLLARS_PER_POINT = 20;
export const SIMULATED_DOLLARS_LABEL = 'simulated, 1-contract, no costs';

// --- formatting -------------------------------------------------------------

export function formatPoints(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(digits)}`;
}

export function formatMoney(points: number | null | undefined, dollarsPerPoint: number): string {
  if (points === null || points === undefined || Number.isNaN(points)) return '—';
  const dollars = points * dollarsPerPoint;
  const sign = dollars > 0 ? '+' : dollars < 0 ? '-' : '';
  return `${sign}$${Math.abs(dollars).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
}

export function formatRatio(ratio: RatioDTO | null | undefined): string {
  if (!ratio || ratio.value === null) return `— (0/${ratio?.denominator ?? 0})`;
  return `${(ratio.value * 100).toFixed(1)}% (${ratio.numerator}/${ratio.denominator})`;
}

export function formatMean(stat: MeanStatDTO | null | undefined, digits = 1): string {
  if (!stat || stat.value === null) return '—';
  return `${stat.value.toFixed(digits)} (n=${stat.n})`;
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null || Number.isNaN(seconds)) return '—';
  if (seconds < 90) return `${Math.round(seconds)}s`;
  return `${(seconds / 60).toFixed(1)}m`;
}

// --- headline cards ----------------------------------------------------------

export type StatCard = {
  key: string;
  label: string;
  value: string;
  detail: string;
  tone: 'positive' | 'negative' | 'neutral';
};

export function buildStatCards(
  headline: PerformanceHeadlineDTO,
  options: { showDollars: boolean; dollarsPerPoint: number },
): StatCard[] {
  const net = headline.net_points.value;
  const netTone = net > 0 ? 'positive' : net < 0 ? 'negative' : 'neutral';
  const netCard: StatCard = options.showDollars
    ? {
        key: 'net',
        label: `Net ($${options.dollarsPerPoint}/pt)`,
        value: formatMoney(net, options.dollarsPerPoint),
        detail: `${SIMULATED_DOLLARS_LABEL} · ${headline.net_points.n_priced} priced trades`,
        tone: netTone,
      }
    : {
        key: 'net',
        label: 'Net points',
        value: formatPoints(net),
        detail: `proxy over ${headline.net_points.n_priced} priced trades`,
        tone: netTone,
      };
  const profitFactor = headline.profit_factor.value;
  return [
    netCard,
    {
      key: 'win_rate',
      label: 'Win rate (tp before sl)',
      value: headline.win_rate.value === null ? '—' : `${(headline.win_rate.value * 100).toFixed(1)}%`,
      detail: `${headline.win_rate.numerator}/${headline.win_rate.denominator} resolved`,
      tone: 'neutral',
    },
    {
      key: 'class_accuracy',
      label: 'Class accuracy',
      value:
        headline.class_accuracy.value === null
          ? '—'
          : `${(headline.class_accuracy.value * 100).toFixed(1)}%`,
      detail: `${headline.class_accuracy.numerator}/${headline.class_accuracy.denominator} resolved`,
      tone: 'neutral',
    },
    {
      key: 'profit_factor',
      label: 'Profit factor',
      value: profitFactor === null ? '—' : profitFactor.toFixed(2),
      detail:
        profitFactor === null
          ? `no losses (${formatPoints(headline.profit_factor.gross_win)} gross win)`
          : `${formatPoints(headline.profit_factor.gross_win)} / ${formatPoints(-headline.profit_factor.gross_loss)}`,
      tone: profitFactor === null ? 'neutral' : profitFactor >= 1 ? 'positive' : 'negative',
    },
    {
      key: 'avg_win_loss',
      label: 'Avg win / loss (pts)',
      value: `${headline.avg_win_pts.value === null ? '—' : headline.avg_win_pts.value.toFixed(1)} / ${headline.avg_loss_pts.value === null ? '—' : headline.avg_loss_pts.value.toFixed(1)}`,
      detail: `${headline.avg_win_pts.n} wins · ${headline.avg_loss_pts.n} losses`,
      tone: 'neutral',
    },
    {
      key: 'day_win_pct',
      label: 'Day win %',
      value:
        headline.day_win_pct.value === null
          ? '—'
          : `${(headline.day_win_pct.value * 100).toFixed(0)}%`,
      detail: `${headline.day_win_pct.numerator}/${headline.day_win_pct.denominator} trading days`,
      tone: 'neutral',
    },
  ];
}

// --- paper-execution summary card (EXEC P3d) ---------------------------------

export type ExecutionSummaryVM = {
  show: boolean;
  realizedLabel: string;
  conservativeLabel: string;
  countLabel: string;
  reasons: { reason: string; count: number; points: string; pointsConservative: string }[];
  anomalyNotes: string[];
};

// The card renders only when execution FILES exist — an executions directory
// the tracker has never written to keeps the surface dark, and a summary with
// zero closes still shows (honest "no realized executions yet" state).
export function buildExecutionSummary(
  executions: PerformanceExecutionsDTO | null | undefined,
  options: { showDollars: boolean; dollarsPerPoint: number },
): ExecutionSummaryVM {
  if (!executions || executions.files_scanned === 0) {
    return { show: false, realizedLabel: '—', conservativeLabel: '—', countLabel: '', reasons: [], anomalyNotes: [] };
  }
  const realized = executions.realized;
  const realizedLabel = options.showDollars
    ? formatMoney(realized.points, options.dollarsPerPoint)
    : `${formatPoints(realized.points)} pts`;
  const conservativeLabel = options.showDollars
    ? formatMoney(realized.points_conservative, options.dollarsPerPoint)
    : `${formatPoints(realized.points_conservative)} pts`;
  const reasons = Object.entries(realized.by_reason)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([reason, bucket]) => ({
      reason: reason.replaceAll('_', ' '),
      count: bucket.count,
      points: formatPoints(bucket.points),
      pointsConservative: formatPoints(bucket.points_conservative),
    }));
  const anomalyNotes: string[] = [];
  const flag = (label: string, value: number) => {
    if (value > 0) anomalyNotes.push(`${label}: ${value}`);
  };
  flag('unreadable files', executions.unreadable_files);
  flag('decode-error files', executions.decode_error_files);
  flag('malformed lines', executions.malformed_lines);
  flag('unknown rows', executions.unknown_type_rows);
  flag('undated closes', executions.undated_close_rows);
  flag('closes missing P&L', executions.closes_missing_pnl);
  flag('outside filters', executions.closes_outside_filters);
  return {
    show: true,
    realizedLabel,
    conservativeLabel,
    countLabel: `${realized.count} closed · ${realized.wins}W/${realized.losses}L · ${executions.opens_total} opens · ${executions.resets_total} resets (${executions.reset_cleared_positions} cleared)`,
    reasons,
    anomalyNotes,
  };
}

// --- charts (SVG geometry) ----------------------------------------------------

export type CurvePoint = { x: number; y: number; day: string; cumulative: number };
export type CumulativeCurve = {
  path: string;
  points: CurvePoint[];
  zeroY: number | null;
  min: number;
  max: number;
};

// Days are plotted ordinally (one slot per trading day, gaps not to scale) —
// standard for trading-day equity curves.
export function buildCumulativeCurve(
  series: PerformanceDayDTO[],
  width: number,
  height: number,
  pad = 8,
): CumulativeCurve {
  if (series.length === 0) return { path: '', points: [], zeroY: null, min: 0, max: 0 };
  const values = series.map((row) => row.cumulative_net_points);
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const span = max - min || 1;
  const innerW = width - pad * 2;
  const innerH = height - pad * 2;
  const step = series.length === 1 ? 0 : innerW / (series.length - 1);
  const points = series.map((row, index) => ({
    x: pad + (series.length === 1 ? innerW / 2 : step * index),
    y: pad + innerH * (1 - (row.cumulative_net_points - min) / span),
    day: row.trading_day,
    cumulative: row.cumulative_net_points,
  }));
  const path = points
    .map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x.toFixed(2)},${point.y.toFixed(2)}`)
    .join(' ');
  const zeroY = pad + innerH * (1 - (0 - min) / span);
  return { path, points, zeroY, min, max };
}

export type DailyBar = {
  x: number;
  y: number;
  width: number;
  height: number;
  day: string;
  net: number;
  positive: boolean;
};
export type DailyBars = { bars: DailyBar[]; zeroY: number };

export function buildDailyBars(
  series: PerformanceDayDTO[],
  width: number,
  height: number,
  pad = 8,
): DailyBars {
  const innerH = height - pad * 2;
  if (series.length === 0) return { bars: [], zeroY: pad + innerH / 2 };
  const maxAbs = Math.max(...series.map((row) => Math.abs(row.net_points)), 1e-9);
  const innerW = width - pad * 2;
  const slot = innerW / series.length;
  const barWidth = Math.max(2, Math.min(28, slot * 0.7));
  const zeroY = pad + innerH / 2;
  const bars = series.map((row, index) => {
    const magnitude = (Math.abs(row.net_points) / maxAbs) * (innerH / 2);
    const positive = row.net_points >= 0;
    return {
      x: pad + slot * index + (slot - barWidth) / 2,
      y: positive ? zeroY - magnitude : zeroY,
      width: barWidth,
      height: magnitude,
      day: row.trading_day,
      net: row.net_points,
      positive,
    };
  });
  return { bars, zeroY };
}

// --- trading-day calendar ------------------------------------------------------

export type CalendarCell = {
  iso: string;
  dayOfMonth: number;
  hasData: boolean;
  net: number;
  resolved: number;
  priced: number;
  predictions: number;
  drops: number;
  // -1..1, |net| scaled to the month's max |net|; drives green/red intensity.
  intensity: number;
};

export type CalendarWeek = {
  cells: (CalendarCell | null)[]; // exactly 5 slots, Monday..Friday
  weekNet: number;
  weekResolved: number;
  weekPriced: number;
  hasData: boolean;
};

export type MonthCalendar = {
  year: number;
  month: number; // 1-12
  label: string;
  weeks: CalendarWeek[];
  monthNet: number;
  monthResolved: number;
  // Day keys that cannot sit on a trading-week grid (weekend-dated keys should
  // never come out of the 18:00 ET roll; surfaced, never silently dropped).
  offGrid: string[];
};

const MONTH_LABELS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

function isoDate(year: number, month: number, day: number): string {
  return `${year.toString().padStart(4, '0')}-${month.toString().padStart(2, '0')}-${day.toString().padStart(2, '0')}`;
}

// UTC-anchored so the developer's local timezone can never shift a trading-day
// key onto a different weekday.
function weekdayOf(iso: string): number {
  return new Date(`${iso}T00:00:00Z`).getUTCDay(); // 0=Sun .. 6=Sat
}

function daysInMonth(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

export function buildMonthCalendar(
  year: number,
  month: number,
  days: Map<string, PerformanceDayDTO>,
): MonthCalendar {
  const totalDays = daysInMonth(year, month);
  let maxAbs = 0;
  for (let day = 1; day <= totalDays; day += 1) {
    const row = days.get(isoDate(year, month, day));
    if (row) maxAbs = Math.max(maxAbs, Math.abs(row.net_points));
  }

  const offGrid: string[] = [];
  for (const iso of days.keys()) {
    if (!iso.startsWith(`${isoDate(year, month, 1).slice(0, 8)}`)) continue;
    const weekday = weekdayOf(iso);
    if (weekday === 0 || weekday === 6) offGrid.push(iso);
  }

  const weeks: CalendarWeek[] = [];
  let current: (CalendarCell | null)[] = [];
  let monthNet = 0;
  let monthResolved = 0;

  const pushWeek = () => {
    if (current.length === 0) return;
    while (current.length < 5) current.push(null);
    const filled = current.filter((cell): cell is CalendarCell => cell !== null);
    weeks.push({
      cells: current,
      weekNet: filled.reduce((sum, cell) => sum + cell.net, 0),
      weekResolved: filled.reduce((sum, cell) => sum + cell.resolved, 0),
      weekPriced: filled.reduce((sum, cell) => sum + cell.priced, 0),
      hasData: filled.some((cell) => cell.hasData),
    });
    current = [];
  };

  for (let day = 1; day <= totalDays; day += 1) {
    const iso = isoDate(year, month, day);
    const weekday = weekdayOf(iso);
    if (weekday === 0 || weekday === 6) continue; // trading weeks are Mon..Fri
    const slot = weekday - 1; // 0=Mon .. 4=Fri
    if (day === 1 || weekday === 1) {
      pushWeek();
      // Leading placeholders when the month starts mid-week.
      while (current.length < slot) current.push(null);
    }
    const row = days.get(iso) ?? null;
    const net = row?.net_points ?? 0;
    if (row) {
      monthNet += net;
      monthResolved += row.resolved;
    }
    current.push({
      iso,
      dayOfMonth: day,
      hasData: row !== null,
      net,
      resolved: row?.resolved ?? 0,
      priced: row?.priced ?? 0,
      predictions: row?.predictions ?? 0,
      drops: row?.drops ?? 0,
      intensity: row === null || maxAbs === 0 ? 0 : net / maxAbs,
    });
    if (weekday === 5) pushWeek();
  }
  pushWeek();

  return {
    year,
    month,
    label: `${MONTH_LABELS[month - 1]} ${year}`,
    weeks,
    monthNet,
    monthResolved,
    offGrid,
  };
}

export function seriesByDay(series: PerformanceDayDTO[]): Map<string, PerformanceDayDTO> {
  return new Map(series.map((row) => [row.trading_day, row]));
}

export function monthsWithData(series: PerformanceDayDTO[]): string[] {
  const months = new Set(series.map((row) => row.trading_day.slice(0, 7)));
  return [...months].sort();
}

export function shiftMonth(monthKey: string, delta: number): string {
  const [year, month] = monthKey.split('-').map(Number);
  const index = year * 12 + (month - 1) + delta;
  const newYear = Math.floor(index / 12);
  const newMonth = (index % 12) + 1;
  return `${newYear.toString().padStart(4, '0')}-${newMonth.toString().padStart(2, '0')}`;
}

export function parseMonthKey(monthKey: string): { year: number; month: number } {
  const [year, month] = monthKey.split('-').map(Number);
  return { year, month };
}

// Cell background for the calendar heat: the app's semantic green/red as the
// diverging poles around a neutral panel tint (validated on the dark surface —
// deutan ΔE 22 with sign + printed value as secondary encodings).
export function calendarCellColor(intensity: number, hasData: boolean): string {
  if (!hasData) return 'rgba(255,255,255,0.02)';
  if (intensity === 0) return 'rgba(124,139,155,0.10)';
  const alpha = 0.10 + Math.min(1, Math.abs(intensity)) * 0.45;
  return intensity > 0
    ? `rgba(54,211,153,${alpha.toFixed(3)})`
    : `rgba(255,107,107,${alpha.toFixed(3)})`;
}
