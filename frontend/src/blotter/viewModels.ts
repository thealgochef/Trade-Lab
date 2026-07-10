// COCKPIT P5: pure trade-tape helpers. No React, no IO.
import type { BlotterEvent, ClosedExecution, TapeRow } from '../domain/models';

export type TapeFilter = 'all' | 'predictions' | 'executions' | 'drops';

export const TAPE_FILTERS: TapeFilter[] = ['all', 'predictions', 'executions', 'drops'];

/**
 * Chip semantics: predictions = prediction + outcome rows; executions =
 * position open/close rows; drops = drop rows. Untyped events (system, feed,
 * warnings, ...) surface under `all` only.
 */
export function matchesTapeFilter(event: BlotterEvent, filter: TapeFilter): boolean {
  if (filter === 'all') return true;
  const kind = event.tape?.kind;
  if (kind === undefined) return false;
  switch (filter) {
    case 'predictions':
      return kind === 'prediction' || kind === 'outcome';
    case 'executions':
      return kind === 'position_open' || kind === 'position_close';
    case 'drops':
      return kind === 'drop';
  }
}

export type RealizedPoints = { points: number; pointsConservative: number };

/**
 * Realized points for an outcome row, joined at render time from the
 * executions store (the tracker's close for the same prediction). Null while
 * no matching close exists — ineligible predictions are never tracked, and the
 * close frame may simply not have arrived yet.
 */
export function realizedPointsFor(tape: TapeRow, closed: ClosedExecution[]): RealizedPoints | null {
  if (tape.kind !== 'outcome') return null;
  const match = closed.find((execution) => execution.predictionId === tape.predictionId);
  if (!match) return null;
  return { points: match.points, pointsConservative: match.pointsConservative };
}

export const formatTapePoints = (points: number): string => `${points > 0 ? '+' : ''}${points.toFixed(2)}`;

export const formatTapeBracket = (points: number, pointsConservative: number): string =>
  `${formatTapePoints(points)} / ${formatTapePoints(pointsConservative)} pts`;
