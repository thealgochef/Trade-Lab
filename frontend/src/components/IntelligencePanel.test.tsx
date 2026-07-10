import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { IntelligencePanel } from './IntelligencePanel';
import { intelligenceStore, marketStore, predictionStore, runtimeStore } from '../state/stores';
import type { MarketBar, MarketLevel, ModelStatus, Observation, Outcome, Prediction } from '../domain/models';

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
  dropped: null,
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
  entryPrice: 19000.25,
  ...overrides,
});

const level = (overrides: Partial<MarketLevel>): MarketLevel => ({
  kind: 'pdh',
  priceTicks: 76_000,
  tradingDay: '2026-05-21',
  originSession: null,
  developing: false,
  eligible: true,
  ...overrides,
});

const marketBar = (overrides: Partial<MarketBar>): MarketBar => ({
  timeframe: 147,
  tradingDay: '2026-05-21',
  barIndex: 3,
  barId: null,
  openTimeUtc: '2026-05-21T14:00:00Z',
  closeTimeUtc: '2026-05-21T14:00:10Z',
  openTicks: 75_990,
  highTicks: 76_012,
  lowTicks: 75_988,
  closeTicks: 76_000,
  volume: 5,
  tradeCount: 5,
  complete: false,
  ...overrides,
});

const activeModelStatus = (overrides: Partial<ModelStatus> = {}): ModelStatus => ({
  loaded: true,
  modelId: 'model-alpha',
  strategyId: 'strategy-1',
  trainingMode: 'mae_first',
  instrument: 'NQ',
  featureNames: [],
  classMap: {},
  validationOk: true,
  validationDetail: null,
  confidenceGate: 0.7,
  eligibleClass: 'up',
  eligibleSessions: ['ny'],
  ...overrides,
});

const activeObservation = (overrides: Partial<Observation> = {}): Observation => ({
  id: 'obs-1',
  status: 'active',
  session: 'ny',
  levelKind: 'pdh',
  startUtc: '2026-05-21T14:00:00Z',
  scheduledEndUtc: '2026-05-21T14:05:00Z',
  originatingTouchId: 'touch-1',
  levelPriceTicks: 76_000,
  direction: 'short',
  ...overrides,
});

describe('IntelligencePanel', () => {
  beforeEach(() => {
    intelligenceStore.reset();
    predictionStore.reset();
    runtimeStore.reset();
    marketStore.reset();
  });

  afterEach(() => cleanup());

  it('sorts levels by distance from the last print with the nearest highlighted', () => {
    marketStore.setState({ currentBars: [marketBar({})], recentClosedBars: [] });
    intelligenceStore.setState({
      levels: [
        level({ kind: 'asia_low', priceTicks: 75_600 }),
        level({ kind: 'pdh', priceTicks: 76_020 }),
        level({ kind: 'pdl', priceTicks: 75_900 }),
      ],
    });
    render(<IntelligencePanel />);

    const rows = document.querySelectorAll('.level-row');
    expect([...rows].map((row) => row.querySelector('span')?.textContent)).toEqual(['pdh', 'pdl', 'asia low']);
    expect(rows[0].className).toContain('nearest');
    expect(rows[0]).toHaveTextContent('+5.00 pts (+20t)');
    expect(rows[0]).toHaveTextContent('19005.00');
    expect(rows[1]).toHaveTextContent('−25.00 pts (−100t)');
  });

  it('labels the nearest PDH/PDL level origin as prior day, not unknown', () => {
    marketStore.setState({ currentBars: [marketBar({})], recentClosedBars: [] });
    intelligenceStore.setState({ levels: [level({ kind: 'pdh', originSession: null })] });
    render(<IntelligencePanel />);

    expect(screen.getByText('Level origin').closest('.key-value')).toHaveTextContent('prior day');
  });

  it('renders the gate math with a fill bar and no reason for an eligible prediction', () => {
    predictionStore.setState({
      predictions: [prediction({ probabilities: { up: 0.82, hold: 0.18 } })],
      modelStatus: activeModelStatus(),
      outcomes: [],
      dropped: [],
      bundles: [],
    });
    render(<IntelligencePanel />);

    expect(screen.getByTestId('gate-math')).toHaveTextContent('up 0.82 / 0.70');
    expect(screen.queryByText(/why:/)).not.toBeInTheDocument();
  });

  it('states WHY an ineligible prediction failed, in priority order', () => {
    predictionStore.setState({
      predictions: [prediction({ eligible: false, probabilities: { up: 0.69, hold: 0.31 } })],
      modelStatus: activeModelStatus(),
      outcomes: [],
      dropped: [],
      bundles: [],
    });
    render(<IntelligencePanel />);
    expect(screen.getByText('why: gate — 0.69 below 0.70')).toBeInTheDocument();

    cleanup();
    predictionStore.setState({
      predictions: [prediction({ eligible: false, predictedClass: 'hold', session: 'asia' })],
      modelStatus: activeModelStatus(),
      outcomes: [],
      dropped: [],
      bundles: [],
    });
    render(<IntelligencePanel />);
    expect(screen.getByText('why: class — predicted hold, eligible up')).toBeInTheDocument();
  });

  it('hides the gate math when the prediction predates the active model', () => {
    predictionStore.setState({
      predictions: [prediction({ modelId: 'older-model' })],
      modelStatus: activeModelStatus(),
      outcomes: [],
      dropped: [],
      bundles: [],
    });
    render(<IntelligencePanel />);

    expect(screen.queryByTestId('gate-math')).not.toBeInTheDocument();
  });

  it('shows an active-setup card with level, touch price, direction, and event-clock countdown', () => {
    marketStore.setState({ currentBars: [marketBar({})], recentClosedBars: [] });
    intelligenceStore.setState({
      observations: [activeObservation()],
      touches: [{ id: 'touch-1', timeUtc: '2026-05-21T14:00:00Z', session: 'ny', levelKind: 'pdh', priceTicks: 76_004, createdObservation: true }],
    });
    render(<IntelligencePanel />);

    const card = screen.getByTestId('active-setup-card');
    expect(card).toHaveTextContent('pdh');
    expect(card).toHaveTextContent('@ 19000.00');
    expect(card).toHaveTextContent('short');
    expect(card).toHaveTextContent('Touch 19001.00');
    // Event clock = latest bar close 14:00:10; window ends 14:05:00 → 4:50.
    expect(card).toHaveTextContent('ends in 4:50');
  });

  it('renders no setup card when no observation is active', () => {
    intelligenceStore.setState({ observations: [activeObservation({ status: 'expired' })] });
    render(<IntelligencePanel />);

    expect(screen.queryByTestId('active-setup-card')).not.toBeInTheDocument();
  });

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

  it('renders a dropped prediction with a distinct dropped badge and its reason', () => {
    predictionStore.setState({
      predictions: [
        prediction({
          dropped: { predictionId: 'pred-1', touchId: 'touch-1', reason: 'no_fill', decisionTsUtc: '2026-05-21T14:05:00Z', entryPrice: null },
        }),
      ],
    });
    render(<IntelligencePanel />);

    expect(screen.getByText('dropped')).toBeInTheDocument();
    expect(screen.getByText('no fill')).toBeInTheDocument();
    // A drop is not a resolution: no correctness badge renders.
    expect(screen.queryByText('correct')).not.toBeInTheDocument();
    expect(screen.queryByText('incorrect')).not.toBeInTheDocument();
  });
});
