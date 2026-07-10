import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { ExecutionsPanel } from './ExecutionsPanel';
import { executionStore, marketStore } from '../state/stores';
import type { ClosedExecution, OpenPosition } from '../domain/models';

const openPosition = (overrides: Partial<OpenPosition> = {}): OpenPosition => ({
  predictionId: 'pred-1',
  touchId: 'touch-1',
  direction: 'long',
  contracts: 1,
  entryTsUtc: '2026-05-21T14:00:00Z',
  entryPrice: 23000,
  entryPriceConservative: 23000.25,
  tpPrice: 23015,
  slPrice: 22970,
  session: 'ny',
  levelKind: 'pdl',
  bundleId: 'bundle-a',
  mode: 'replay',
  pointValue: 20,
  lastPrice: 23000,
  unrealizedPoints: 0,
  unrealizedPointsConservative: -0.25,
  ...overrides,
});

const closedExecution = (overrides: Partial<ClosedExecution> = {}): ClosedExecution => ({
  predictionId: 'pred-2',
  touchId: 'touch-2',
  direction: 'long',
  contracts: 1,
  entryTsUtc: '2026-05-21T14:00:00Z',
  exitTsUtc: '2026-05-21T14:10:00Z',
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

describe('ExecutionsPanel', () => {
  beforeEach(() => {
    executionStore.reset();
    marketStore.reset();
  });

  afterEach(() => cleanup());

  it('shows honest empty states when nothing is tracked', () => {
    render(<ExecutionsPanel />);
    expect(screen.getByText('No open paper position.')).toBeInTheDocument();
    expect(screen.getByText('No closed paper executions this session.')).toBeInTheDocument();
  });

  it('renders an open-position card with side, entry, both unrealized columns, and age', () => {
    executionStore.setState({ openPositions: [openPosition()], closed: [] });
    marketStore.setState({
      currentBars: [
        {
          timeframe: 147,
          tradingDay: '2026-05-21',
          barIndex: 5,
          barId: '147t:2026-05-21:5',
          openTimeUtc: '2026-05-21T14:03:00Z',
          closeTimeUtc: '2026-05-21T14:03:20Z',
          openTicks: 92000,
          highTicks: 92012,
          lowTicks: 91998,
          closeTicks: 92010,
          volume: 10,
          tradeCount: 10,
          complete: false,
        },
      ],
    });
    render(<ExecutionsPanel />);

    expect(screen.getByText('long')).toBeInTheDocument();
    expect(screen.getByText('entry 23000.00')).toBeInTheDocument();
    // Unrealized recomputed against the local latest print 23002.5.
    expect(screen.getByText('+2.50 pts')).toBeInTheDocument();
    expect(screen.getByText('cons +2.25')).toBeInTheDocument();
    expect(screen.getByText(/tp 23015\.00 · sl 22970\.00 · mark 23002\.50 · age 3m 20s/)).toBeInTheDocument();
  });

  it('renders the closed-executions table with both columns and the close reason', () => {
    executionStore.setState({
      openPositions: [],
      closed: [closedExecution(), closedExecution({ predictionId: 'pred-3', reason: 'sl_hit', exitTsUtc: '2026-05-21T14:20:00Z', exitPrice: 22970, exitPriceConservative: 22969.75, points: -30, pointsConservative: -30.5, dollars: -600, dollarsConservative: -610 })],
    });
    render(<ExecutionsPanel />);

    expect(screen.getByText('tp hit')).toBeInTheDocument();
    expect(screen.getByText('sl hit')).toBeInTheDocument();
    expect(screen.getByText('+15.00')).toBeInTheDocument();
    expect(screen.getByText('+14.75')).toBeInTheDocument();
    expect(screen.getByText('-30.00')).toBeInTheDocument();
    expect(screen.getByText('-30.50')).toBeInTheDocument();
    expect(screen.getByText('14:10:00')).toBeInTheDocument();
    expect(screen.getByText('14:20:00')).toBeInTheDocument();
  });
});
