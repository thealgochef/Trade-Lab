import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { EventBlotter } from './EventBlotter';
import { addBlotterEvent, blotterStore, executionStore } from '../state/stores';
import type { BlotterEvent, ClosedExecution } from '../domain/models';

let eventCounter = 0;

const tapeEvent = (tape: NonNullable<BlotterEvent['tape']>, overrides: Partial<BlotterEvent> = {}): BlotterEvent => {
  eventCounter += 1;
  return {
    id: `event-${eventCounter}`,
    timeUtc: '2026-05-21T14:03:00Z',
    category: 'observation',
    severity: 'info',
    message: 'typed row',
    tape,
    ...overrides,
  };
};

const closedExecution = (overrides: Partial<ClosedExecution> = {}): ClosedExecution => ({
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

describe('EventBlotter', () => {
  beforeEach(() => {
    blotterStore.reset();
    executionStore.reset();
  });

  afterEach(() => {
    cleanup();
    blotterStore.reset();
    executionStore.reset();
  });

  it('renders compact warning rows with expandable safe provider details only', () => {
    blotterStore.setState({
      events: [{
        id: 'event-1',
        timeUtc: '2026-05-21T14:03:00Z',
        category: 'warning',
        severity: 'warning',
        message: 'Databento provider reported an error',
        code: 'provider_error',
        source: 'databento',
        details: {
          schema: 'mbp-1',
          detail: 'code=bad_request; message=<redacted> path=<path>',
          dropped: 2,
          token: 'db-secret',
          raw_record: { api_key: 'db-secret' },
        } as never,
      }],
    });

    render(<EventBlotter />);

    const row = screen.getByText('Databento provider reported an error').closest('details');
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getAllByText('provider_error').length).toBeGreaterThanOrEqual(1);

    fireEvent.click(within(row as HTMLElement).getByText('Databento provider reported an error'));

    expect(within(row as HTMLElement).getByText('Timestamp')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('2026-05-21T14:03:00Z')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('Code')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('Source')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('Schema')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('Detail')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('mbp-1')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('code=bad_request; message=<redacted> path=<path>')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('Dropped')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('2')).toBeInTheDocument();
    expect(row as HTMLElement).not.toHaveTextContent('token');
    expect(row as HTMLElement).not.toHaveTextContent('raw_record');
    expect(row as HTMLElement).not.toHaveTextContent('db-secret');
    expect(row as HTMLElement).not.toHaveTextContent('[object Object]');
  });

  it('renders typed tape rows: prediction, outcome with executed points, drop, open, close', () => {
    executionStore.setState({ openPositions: [], closed: [closedExecution()] });
    blotterStore.setState({
      events: [
        tapeEvent({ kind: 'position_close', predictionId: 'pred-1', direction: 'long', reason: 'tp_hit', points: 15, pointsConservative: 14.75, exitPrice: 23015 }),
        tapeEvent({ kind: 'position_open', predictionId: 'pred-1', direction: 'long', entryPrice: 23000, entryPriceConservative: 23000.25, tpPrice: 23015, slPrice: 22970 }),
        tapeEvent({ kind: 'outcome', predictionId: 'pred-1', resolutionType: 'tp_hit', correct: true, actualClass: 'up' }),
        tapeEvent({ kind: 'drop', predictionId: 'pred-2', reason: 'no_fill' }),
        tapeEvent({ kind: 'prediction', predictionId: 'pred-1', predictedClass: 'up', probability: 0.72, eligible: true, direction: 'long', session: 'ny' }),
      ],
    });

    render(<EventBlotter />);

    // Prediction: class, probability, gate verdict.
    expect(screen.getByText('pred')).toBeInTheDocument();
    expect(screen.getByText('up')).toBeInTheDocument();
    expect(screen.getByText('p 0.72')).toBeInTheDocument();
    expect(screen.getByText('eligible')).toBeInTheDocument();
    // Outcome: resolution + realized points joined from the executions store.
    expect(screen.getByText('outcome')).toBeInTheDocument();
    expect(screen.getByText('correct')).toBeInTheDocument();
    expect(screen.getAllByText('+15.00 / +14.75 pts').length).toBe(2); // outcome row + close row
    // Drop: reason.
    expect(screen.getByText('drop')).toBeInTheDocument();
    expect(screen.getByText('no fill')).toBeInTheDocument();
    // Open: both entry columns + barriers; close: reason + both point columns.
    expect(screen.getByText('long @ 23000.00 / 23000.25')).toBeInTheDocument();
    expect(screen.getByText('tp 23015.00 · sl 22970.00')).toBeInTheDocument();
    expect(screen.getByText('close')).toBeInTheDocument();
  });

  it('leaves outcome points blank when no execution close exists for the prediction', () => {
    blotterStore.setState({
      events: [tapeEvent({ kind: 'outcome', predictionId: 'pred-untracked', resolutionType: 'sl_hit', correct: false, actualClass: 'down' })],
    });

    render(<EventBlotter />);

    expect(screen.getByText('miss')).toBeInTheDocument();
    expect(screen.getByText('sl hit')).toBeInTheDocument();
    expect(screen.queryByText(/pts/)).not.toBeInTheDocument();
  });

  it('filters the tape with chips and keeps untyped rows under All only', () => {
    blotterStore.setState({
      events: [
        tapeEvent({ kind: 'prediction', predictionId: 'pred-1', predictedClass: 'up', probability: 0.72, eligible: true, direction: 'long', session: 'ny' }),
        tapeEvent({ kind: 'drop', predictionId: 'pred-2', reason: 'no_fill' }),
        tapeEvent({ kind: 'position_open', predictionId: 'pred-1', direction: 'long', entryPrice: 23000, entryPriceConservative: 23000.25, tpPrice: 23015, slPrice: 22970 }),
        { id: 'plain-1', timeUtc: '2026-05-21T14:03:00Z', category: 'system', severity: 'info', message: 'Heartbeat' },
      ],
    });

    render(<EventBlotter />);
    expect(screen.getByText('Heartbeat')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Predictions' }));
    expect(screen.getByText('pred')).toBeInTheDocument();
    expect(screen.queryByText('Heartbeat')).not.toBeInTheDocument();
    expect(screen.queryByText('drop')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Drops' }));
    expect(screen.getByText('no fill')).toBeInTheDocument();
    expect(screen.queryByText('pred')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Executions' }));
    expect(screen.getByText('open')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'All' }));
    expect(screen.getByText('Heartbeat')).toBeInTheDocument();
  });

  it('flashes the newest row and keeps the render bounded at 80 rows', () => {
    // Mixed categories keep the store above the 80-row render cap under the
    // per-category retention bounds (50 predictions + 40 untyped = 90 retained);
    // the untyped batch goes last so the newest row renders its plain message.
    for (let index = 0; index < 90; index += 1) {
      addBlotterEvent(index < 50
        ? { timeUtc: '2026-05-21T14:03:00Z', category: 'observation', severity: 'info', message: `row ${index}`, tape: { kind: 'prediction', predictionId: `pred-${index}`, predictedClass: 'up', probability: 0.7, eligible: true, direction: 'long', session: 'ny' } }
        : { timeUtc: '2026-05-21T14:03:00Z', category: 'system', severity: 'info', message: `row ${index}` });
    }

    render(<EventBlotter />);

    const rows = document.querySelectorAll('.blotter-row');
    expect(rows).toHaveLength(80);
    expect(rows[0].className).toContain('tape-newest');
    expect(rows[0]).toHaveTextContent('row 89'); // newest-first
    expect(rows[1].className).not.toContain('tape-newest');
  });
});
