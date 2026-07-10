// Vitest coverage for the Performance page view-models (REPORT P4): stat cards
// (incl. the simulated-$ toggle), SVG chart geometry, and — most importantly —
// the trading-day calendar math. Day keys are TRADING days on the 18:00 ET roll
// (the backend's trading_day_for already mapped a Sunday 19:00 ET trade to
// Monday's key), so weeks here are Mon..Fri trading weeks, never calendar weeks.

import { describe, expect, it } from 'vitest';
import type { PerformanceDayDTO, PerformanceHeadlineDTO } from '../api/types';
import {
  DEFAULT_DOLLARS_PER_POINT,
  SIMULATED_DOLLARS_LABEL,
  buildCumulativeCurve,
  buildDailyBars,
  buildMonthCalendar,
  buildStatCards,
  calendarCellColor,
  formatMoney,
  formatPoints,
  formatRatio,
  monthsWithData,
  parseMonthKey,
  seriesByDay,
  shiftMonth,
} from './viewModels';

const day = (trading_day: string, net: number, over: Partial<PerformanceDayDTO> = {}): PerformanceDayDTO => ({
  trading_day,
  net_points: net,
  cumulative_net_points: net,
  resolved: 2,
  priced: 2,
  wins: net > 0 ? 2 : 0,
  losses: net > 0 ? 0 : 2,
  unpriced: 0,
  predictions: 3,
  eligible_predictions: 1,
  drops: 1,
  ...over,
});

const headline = (over: Partial<PerformanceHeadlineDTO> = {}): PerformanceHeadlineDTO => ({
  predictions: 10,
  eligible_predictions: 4,
  resolved_trades: 8,
  eligible_trades: 3,
  priced_trades: 8,
  unpriced_trades: 0,
  win_rate: { value: 0.625, numerator: 5, denominator: 8 },
  class_accuracy: { value: 0.5, numerator: 4, denominator: 8 },
  net_points: { value: 30, n_priced: 8 },
  gross_win_points: 75,
  gross_loss_points: 45,
  profit_factor: { value: 75 / 45, gross_win: 75, gross_loss: 45 },
  avg_win_pts: { value: 15, n: 5 },
  avg_loss_pts: { value: 15, n: 3 },
  avg_time_to_resolution_seconds: { value: 120, n: 8 },
  avg_bars_to_resolution: { value: 4, n: 8 },
  direction: {},
  day_win_pct: { value: 0.5, numerator: 1, denominator: 2 },
  mfe_pts: { mean: 10, median: 9, max: 20, n: 8 },
  mae_pts: { mean: 5, median: 5, max: 9, n: 8 },
  point_value: 20,
  ...over,
});

describe('formatting', () => {
  it('formats points with an explicit sign', () => {
    expect(formatPoints(15)).toBe('+15.0');
    expect(formatPoints(-7.25, 2)).toBe('-7.25');
    expect(formatPoints(null)).toBe('—');
  });

  it('formats simulated dollars at the configured $/pt', () => {
    expect(formatMoney(15, 20)).toBe('+$300');
    expect(formatMoney(-2.5, 20)).toBe('-$50');
    expect(formatMoney(0, 20)).toBe('$0');
  });

  it('always renders the denominator alongside a ratio', () => {
    expect(formatRatio({ value: 0.5, numerator: 1, denominator: 2 })).toBe('50.0% (1/2)');
    expect(formatRatio({ value: null, numerator: 0, denominator: 0 })).toBe('— (0/0)');
  });
});

describe('stat cards', () => {
  it('builds the six headline cards in points mode', () => {
    const cards = buildStatCards(headline(), { showDollars: false, dollarsPerPoint: 20 });
    expect(cards.map((card) => card.key)).toEqual([
      'net',
      'win_rate',
      'class_accuracy',
      'profit_factor',
      'avg_win_loss',
      'day_win_pct',
    ]);
    expect(cards[0].value).toBe('+30.0');
    expect(cards[0].tone).toBe('positive');
    expect(cards[1].detail).toBe('5/8 resolved');
  });

  it('the $ toggle converts at $/pt and carries the simulated label', () => {
    const cards = buildStatCards(headline(), { showDollars: true, dollarsPerPoint: 20 });
    expect(cards[0].value).toBe('+$600');
    expect(cards[0].label).toContain('$20/pt');
    expect(cards[0].detail).toContain(SIMULATED_DOLLARS_LABEL);
    expect(DEFAULT_DOLLARS_PER_POINT).toBe(20);
  });

  it('profit factor with no losses renders as em dash, not Infinity', () => {
    const cards = buildStatCards(
      headline({ profit_factor: { value: null, gross_win: 75, gross_loss: 0 } }),
      { showDollars: false, dollarsPerPoint: 20 },
    );
    const profitFactor = cards.find((card) => card.key === 'profit_factor')!;
    expect(profitFactor.value).toBe('—');
    expect(profitFactor.detail).toContain('no losses');
  });
});

describe('cumulative curve geometry', () => {
  it('maps days to evenly spaced x and cumulative values to y', () => {
    const series = [
      day('2026-06-15', 15, { cumulative_net_points: 15 }),
      day('2026-06-16', -15, { cumulative_net_points: 0 }),
      day('2026-06-17', 30, { cumulative_net_points: 30 }),
    ];
    const curve = buildCumulativeCurve(series, 640, 220, 8);
    expect(curve.points).toHaveLength(3);
    expect(curve.points[0].x).toBeCloseTo(8);
    expect(curve.points[2].x).toBeCloseTo(632);
    expect(curve.path.startsWith('M')).toBe(true);
    // y decreases (moves up) as cumulative rises
    expect(curve.points[2].y).toBeLessThan(curve.points[1].y);
    // zero line sits inside the plot and matches the day-2 cumulative of 0
    expect(curve.zeroY).not.toBeNull();
    expect(curve.points[1].y).toBeCloseTo(curve.zeroY!);
  });

  it('handles empty and single-day series', () => {
    expect(buildCumulativeCurve([], 640, 220).points).toHaveLength(0);
    const single = buildCumulativeCurve([day('2026-06-15', 15, { cumulative_net_points: 15 })], 640, 220);
    expect(single.points).toHaveLength(1);
    expect(single.points[0].x).toBeCloseTo(320);
  });
});

describe('daily bars geometry', () => {
  it('draws positive bars above the zero line and negative below', () => {
    const series = [day('2026-06-15', 15), day('2026-06-16', -30)];
    const { bars, zeroY } = buildDailyBars(series, 640, 220, 8);
    expect(bars).toHaveLength(2);
    const positive = bars[0];
    const negative = bars[1];
    expect(positive.positive).toBe(true);
    expect(positive.y + positive.height).toBeCloseTo(zeroY);
    expect(negative.positive).toBe(false);
    expect(negative.y).toBeCloseTo(zeroY);
    // the -30 bar is twice the |15| bar and spans the full half-height
    expect(negative.height).toBeCloseTo(positive.height * 2);
  });
});

describe('trading-day calendar math', () => {
  // June 2026 starts on a MONDAY; July 2026 starts on a WEDNESDAY.
  it('June 2026: full trading weeks, Mon..Fri only, with week subtotals', () => {
    const series = [
      day('2026-06-15', 15), // Monday
      day('2026-06-16', -30), // Tuesday
      day('2026-06-19', 45), // Friday
    ];
    const calendar = buildMonthCalendar(2026, 6, seriesByDay(series));
    expect(calendar.label).toBe('June 2026');
    // June 1..30 with June 1 a Monday: 5 trading weeks (22 weekdays)
    expect(calendar.weeks).toHaveLength(5);
    for (const week of calendar.weeks) {
      expect(week.cells).toHaveLength(5);
    }
    const week3 = calendar.weeks[2]; // June 15-19
    expect(week3.cells.every((cell) => cell !== null)).toBe(true);
    expect(week3.cells[0]!.iso).toBe('2026-06-15');
    expect(week3.cells[0]!.net).toBe(15);
    expect(week3.weekNet).toBe(15 - 30 + 45);
    expect(week3.weekResolved).toBe(6);
    expect(calendar.monthNet).toBe(30);
    expect(calendar.monthResolved).toBe(6);
    // weekend day keys never appear as cells
    const isos = calendar.weeks.flatMap((week) => week.cells.filter(Boolean).map((cell) => cell!.iso));
    expect(isos).not.toContain('2026-06-06');
    expect(isos).not.toContain('2026-06-07');
  });

  it('July 2026 starts mid-week: leading slots are null placeholders', () => {
    const calendar = buildMonthCalendar(2026, 7, seriesByDay([day('2026-07-01', 10)]));
    const week1 = calendar.weeks[0];
    // July 1 2026 is a Wednesday -> Mon+Tue empty, Wed holds day 1
    expect(week1.cells[0]).toBeNull();
    expect(week1.cells[1]).toBeNull();
    expect(week1.cells[2]!.dayOfMonth).toBe(1);
    expect(week1.cells[2]!.net).toBe(10);
  });

  it('a Sunday-dated trade key belongs to Monday upstream; a weekend key here is surfaced off-grid', () => {
    // The backend's 18:00 ET roll can never emit a weekend trading day — if one
    // ever appears it must be visible, not silently placed or dropped.
    const calendar = buildMonthCalendar(2026, 6, seriesByDay([day('2026-06-07', 5)])); // a Sunday
    const isos = calendar.weeks.flatMap((week) => week.cells.filter(Boolean).map((cell) => cell!.iso));
    expect(isos).not.toContain('2026-06-07');
    expect(calendar.offGrid).toEqual(['2026-06-07']);
    expect(calendar.monthNet).toBe(0);
  });

  it('scales cell intensity to the month max |net| and keeps zero-data cells neutral', () => {
    const series = [day('2026-06-15', 15), day('2026-06-16', -30)];
    const calendar = buildMonthCalendar(2026, 6, seriesByDay(series));
    const week3 = calendar.weeks[2];
    expect(week3.cells[0]!.intensity).toBeCloseTo(0.5); // 15 / 30
    expect(week3.cells[1]!.intensity).toBeCloseTo(-1); // -30 / 30
    expect(week3.cells[2]!.intensity).toBe(0); // no data
    expect(week3.cells[2]!.hasData).toBe(false);
  });

  it('cell colors: green positive, red negative, neutral otherwise', () => {
    expect(calendarCellColor(0.5, true)).toContain('54,211,153');
    expect(calendarCellColor(-0.5, true)).toContain('255,107,107');
    expect(calendarCellColor(0, true)).toContain('124,139,155');
    expect(calendarCellColor(0, false)).toBe('rgba(255,255,255,0.02)');
  });

  it('month navigation helpers cross year boundaries', () => {
    expect(shiftMonth('2026-01', -1)).toBe('2025-12');
    expect(shiftMonth('2025-12', 1)).toBe('2026-01');
    expect(parseMonthKey('2026-06')).toEqual({ year: 2026, month: 6 });
  });

  it('monthsWithData is sorted and unique', () => {
    const months = monthsWithData([
      day('2026-06-15', 1),
      day('2026-06-16', 1),
      day('2026-05-04', 1),
      day('2026-07-01', 1),
    ]);
    expect(months).toEqual(['2026-05', '2026-06', '2026-07']);
  });
});
