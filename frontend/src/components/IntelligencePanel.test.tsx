import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { IntelligencePanel } from './IntelligencePanel';
import { intelligenceStore, predictionStore, runtimeStore } from '../state/stores';
import type { Outcome, Prediction } from '../domain/models';

const prediction = (overrides: Partial<Prediction> = {}): Prediction => ({
  id: 'pred-1',
  touchId: 'touch-1',
  observationId: 'obs-1',
  timeUtc: '2026-05-21T14:00:10Z',
  predictedClass: 'up',
  probabilities: { down: 0.1, hold: 0.3, up: 0.6 },
  levelKind: 'pdh',
  levelPriceTicks: 76000,
  direction: 'long',
  session: 'ny',
  eligible: true,
  modelId: 'model-alpha',
  contractId: 'contract-1',
  nanCount: 0,
  outcome: null,
  ...overrides,
});

const outcome = (overrides: Partial<Outcome> = {}): Outcome => ({
  id: 'outcome-1',
  predictionId: 'pred-1',
  touchId: 'touch-1',
  resolutionType: 'mae_first',
  actualClass: 'up',
  predictedClass: 'up',
  correct: true,
  maxMfePts: 12.5,
  maxMaePts: 3.25,
  barsToResolution: 8,
  timeUtc: '2026-05-21T14:05:00Z',
  ...overrides,
});

describe('IntelligencePanel', () => {
  beforeEach(() => {
    intelligenceStore.reset();
    predictionStore.reset();
    runtimeStore.reset();
  });

  afterEach(() => cleanup());

  it('renders the real runtime session and trading day from the store', () => {
    runtimeStore.setState({ session: 'ny', tradingDay: '2026-05-21', engineReady: true });
    render(<IntelligencePanel />);

    expect(screen.getByText('ny')).toBeInTheDocument();
    expect(screen.getByText('2026-05-21')).toBeInTheDocument();
    expect(screen.getByText('engine ready')).toBeInTheDocument();
    expect(screen.queryByText('unavailable')).not.toBeInTheDocument();
  });

  it('falls back to em-dash and unavailable when session/trading day are absent', () => {
    render(<IntelligencePanel />);

    expect(screen.getByText('unavailable')).toBeInTheDocument();
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('renders a prediction row with predicted class, probabilities, eligibility, and level meta', () => {
    predictionStore.setState({ predictions: [prediction()] });
    render(<IntelligencePanel />);

    expect(screen.getByText('up')).toBeInTheDocument();
    expect(screen.getByText('eligible')).toBeInTheDocument();
    expect(screen.getByText('long')).toBeInTheDocument();
    expect(screen.getByText('up 60%')).toBeInTheDocument();
    expect(screen.getByText('hold 30%')).toBeInTheDocument();
    expect(screen.getByText(/pdh @ 19000.00 · ny/)).toBeInTheDocument();
  });

  it('renders the resolved outcome with correctness badge and MFE/MAE points', () => {
    predictionStore.setState({ predictions: [prediction({ outcome: outcome() })] });
    render(<IntelligencePanel />);

    expect(screen.getByText('correct')).toBeInTheDocument();
    expect(screen.getByText('actual up')).toBeInTheDocument();
    expect(screen.getByText('MFE 12.50 pts')).toBeInTheDocument();
    expect(screen.getByText('MAE 3.25 pts')).toBeInTheDocument();
    expect(screen.getByText('mae first')).toBeInTheDocument();
  });

  it('marks an ineligible prediction and an incorrect resolved outcome', () => {
    predictionStore.setState({
      predictions: [prediction({ eligible: false, outcome: outcome({ correct: false, actualClass: 'down' }) })],
    });
    render(<IntelligencePanel />);

    expect(screen.getByText('ineligible')).toBeInTheDocument();
    expect(screen.getByText('incorrect')).toBeInTheDocument();
    expect(screen.getByText('actual down')).toBeInTheDocument();
  });
});
