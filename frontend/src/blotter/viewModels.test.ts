import { describe, expect, it } from 'vitest';
import type { BlotterEvent, ClosedExecution, TapeRow } from '../domain/models';
import { formatTapeBracket, matchesTapeFilter, realizedPointsFor } from './viewModels';

const event = (tape?: TapeRow): BlotterEvent => ({
  id: 'e1',
  timeUtc: '2026-05-21T14:03:00Z',
  category: 'observation',
  severity: 'info',
  message: 'row',
  tape,
});

const closed = (overrides: Partial<ClosedExecution> = {}): ClosedExecution => ({
  predictionId: 'pred-1',
  touchId: 'touch-1',
  direction: 'long',
  contracts: 1,
  entryTsUtc: '2026-05-21T14:00:05Z',
  exitTsUtc: '2026-05-21T14:01:05Z',
  reason: 'tp_hit',
  entryPrice: 23000,
  entryPriceConservative: 23000.25,
  exitPrice: 23015,
  exitPriceConservative: 23015,
  points: 15,
  pointsConservative: 14.75,
  dollars: 300,
  dollarsConservative: 295,
  pointValue: 20,
  session: 'ny',
  levelKind: 'pdl',
  ...overrides,
});

describe('matchesTapeFilter', () => {
  const prediction: TapeRow = { kind: 'prediction', predictionId: 'p', predictedClass: 'up', probability: 0.7, eligible: true, direction: 'long', session: 'ny' };
  const outcome: TapeRow = { kind: 'outcome', predictionId: 'p', resolutionType: 'tp_hit', correct: true, actualClass: 'up' };
  const drop: TapeRow = { kind: 'drop', predictionId: 'p', reason: 'no_fill' };
  const open: TapeRow = { kind: 'position_open', predictionId: 'p', direction: 'long', entryPrice: 1, entryPriceConservative: 1, tpPrice: 2, slPrice: 0 };
  const close: TapeRow = { kind: 'position_close', predictionId: 'p', direction: 'long', reason: 'tp_hit', points: 1, pointsConservative: 1, exitPrice: 2 };

  it('groups prediction+outcome, open+close, and drops; untyped rows are All-only', () => {
    expect(matchesTapeFilter(event(prediction), 'predictions')).toBe(true);
    expect(matchesTapeFilter(event(outcome), 'predictions')).toBe(true);
    expect(matchesTapeFilter(event(open), 'executions')).toBe(true);
    expect(matchesTapeFilter(event(close), 'executions')).toBe(true);
    expect(matchesTapeFilter(event(drop), 'drops')).toBe(true);
    expect(matchesTapeFilter(event(drop), 'predictions')).toBe(false);
    expect(matchesTapeFilter(event(prediction), 'executions')).toBe(false);
    const untyped = event(undefined);
    expect(matchesTapeFilter(untyped, 'all')).toBe(true);
    expect(matchesTapeFilter(untyped, 'predictions')).toBe(false);
    expect(matchesTapeFilter(untyped, 'executions')).toBe(false);
    expect(matchesTapeFilter(untyped, 'drops')).toBe(false);
  });
});

describe('realizedPointsFor', () => {
  it('joins outcome rows to the executions store by prediction id', () => {
    const tape: TapeRow = { kind: 'outcome', predictionId: 'pred-1', resolutionType: 'tp_hit', correct: true, actualClass: 'up' };
    expect(realizedPointsFor(tape, [closed()])).toEqual({ points: 15, pointsConservative: 14.75 });
    expect(realizedPointsFor(tape, [closed({ predictionId: 'other' })])).toBeNull();
    expect(realizedPointsFor({ kind: 'drop', predictionId: 'pred-1', reason: 'no_fill' }, [closed()])).toBeNull();
  });
});

describe('formatTapeBracket', () => {
  it('formats both P&L columns with signs', () => {
    expect(formatTapeBracket(15, 14.75)).toBe('+15.00 / +14.75 pts');
    expect(formatTapeBracket(-30, -30.5)).toBe('-30.00 / -30.50 pts');
    expect(formatTapeBracket(0, -0.25)).toBe('0.00 / -0.25 pts');
  });
});
