// COCKPIT P5: pure trade-tape helpers. No React, no IO.
import type { BlotterEvent, ClosedExecution, TapeRow } from '../domain/models';

export type TapeFilter = 'all' | 'predictions' | 'executions' | 'drops';

export const TAPE_FILTERS: TapeFilter[] = ['all', 'predictions', 'executions', 'drops'];

export type TapeCategory = 'predictions' | 'executions' | 'drops' | 'untyped';

/**
 * Category semantics: predictions = prediction + outcome rows; executions =
 * position open/close rows; drops = drop rows; untyped = everything without a
 * tape payload (system, feed, warnings, ...). The store's per-category
 * retention and the filter chips share this one classifier.
 */
export function tapeCategory(event: Pick<BlotterEvent, 'tape'>): TapeCategory {
  switch (event.tape?.kind) {
    case 'prediction':
    case 'outcome':
      return 'predictions';
    case 'position_open':
    case 'position_close':
      return 'executions';
    case 'drop':
      return 'drops';
    default:
      return 'untyped';
  }
}

// Untyped events surface under `all` only — 'untyped' never equals a chip value.
export function matchesTapeFilter(event: BlotterEvent, filter: TapeFilter): boolean {
  return filter === 'all' || tapeCategory(event) === filter;
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
