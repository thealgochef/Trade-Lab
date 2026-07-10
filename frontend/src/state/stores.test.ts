import { afterEach, describe, expect, it } from 'vitest';
import { addBlotterEvent, addDropped, addOpenPosition, addOutcome, addPrediction, blotterStore, clearExecutions, clearPredictions, closeOpenPosition, connectionStore, executionStore, intelligenceStore, marketStore, predictionStore, runtimeStore, setModelStatus, setOpenPositions } from './stores';
import { tapeCategory } from '../blotter/viewModels';
import type { ClosedExecution, DroppedPrediction, OpenPosition, Outcome, Prediction, TapeRow } from '../domain/models';

const resetStores = () => {
  runtimeStore.reset();
  connectionStore.reset();
  marketStore.reset();
  intelligenceStore.reset();
  blotterStore.reset();
  predictionStore.reset();
  executionStore.reset();
};

const makePrediction = (id: string): Prediction => ({
  id,
  touchId: 'touch-1',
  observationId: 'obs-1',
  timeUtc: '2026-05-21T14:02:00Z',
  predictedClass: 'continuation',
  probabilities: { continuation: 0.7, reversal: 0.3 },
  levelKind: 'pdh',
  levelPriceTicks: 76000,
  direction: 'long',
  session: 'ny',
  eligible: true,
  modelId: 'model-a',
  contractId: 'NQM6',
  nanCount: 0,
  outcome: null,
  dropped: null,
});

const makeOutcome = (predictionId: string): Outcome => ({
  id: `out-${predictionId}`,
  predictionId,
  touchId: 'touch-1',
  resolutionType: 'target',
  actualClass: 'continuation',
  predictedClass: 'continuation',
  correct: true,
  maxMfePts: 12.5,
  maxMaePts: 3.0,
  barsToResolution: 8,
  timeUtc: '2026-05-21T14:10:00Z',
  entryPrice: 19000.25,
});

const makeDropped = (predictionId: string): DroppedPrediction => ({
  predictionId,
  touchId: 'touch-1',
  reason: 'flatten',
  decisionTsUtc: '2026-05-21T20:41:00Z',
  entryPrice: null,
});

const tapeEvent = (message: string, tape?: TapeRow) => ({
  timeUtc: '2026-05-21T14:00:00Z',
  category: 'observation' as const,
  severity: 'info' as const,
  message,
  tape,
});

const predictionTape = (predictionId: string): TapeRow => ({ kind: 'prediction', predictionId, predictedClass: 'continuation', probability: 0.7, eligible: true, direction: 'long', session: 'ny' });
const outcomeTape = (predictionId: string): TapeRow => ({ kind: 'outcome', predictionId, resolutionType: 'target', correct: true, actualClass: 'continuation' });
const dropTape = (predictionId: string): TapeRow => ({ kind: 'drop', predictionId, reason: 'flatten' });
const openTape = (predictionId: string): TapeRow => ({ kind: 'position_open', predictionId, direction: 'long', entryPrice: 23000, entryPriceConservative: 23000.25, tpPrice: 23015, slPrice: 22970 });
const closeTape = (predictionId: string): TapeRow => ({ kind: 'position_close', predictionId, direction: 'long', reason: 'tp_hit', points: 15, pointsConservative: 14.75, exitPrice: 23015 });

describe('workstation stores', () => {
  afterEach(resetStores);

  it('keeps runtime, connection, market, intelligence, and blotter slices independent', () => {
    runtimeStore.setState({ apiOnline: true, feedReady: true });
    connectionStore.setState({ wsStatus: 'connected', lastSequence: 10 });
    marketStore.setState({ selectedTimeframe: 2000 });
    intelligenceStore.setState({ levels: [{ kind: 'pdh', priceTicks: 76000, tradingDay: '2026-05-21', originSession: 'ny', developing: false, eligible: true }] });

    expect(runtimeStore.getSnapshot()).toMatchObject({ apiOnline: true, feedReady: true });
    expect(connectionStore.getSnapshot()).toMatchObject({ wsStatus: 'connected', lastSequence: 10 });
    expect(marketStore.getSnapshot()).toMatchObject({ selectedTimeframe: 2000, currentBars: [] });
    expect(intelligenceStore.getSnapshot().levels).toHaveLength(1);
    expect(blotterStore.getSnapshot().events).toEqual([]);
  });

  it('bounds untyped blotter retention to the newest 60 events', () => {
    for (let index = 0; index < 65; index += 1) {
      addBlotterEvent({ timeUtc: `2026-05-21T14:${String(index % 60).padStart(2, '0')}:00Z`, category: 'system', severity: 'info', message: `event-${index}` });
    }

    const events = blotterStore.getSnapshot().events;
    expect(events).toHaveLength(60);
    expect(events[0].message).toBe('event-64');
    expect(events.at(-1)?.message).toBe('event-5');
  });

  it('keeps typed tape rows alive through a flood of 500 untyped events', () => {
    addBlotterEvent(tapeEvent('pred created', predictionTape('pred-1')));
    addBlotterEvent(tapeEvent('pred resolved', outcomeTape('pred-1')));
    addBlotterEvent(tapeEvent('pred dropped', dropTape('pred-2')));
    addBlotterEvent(tapeEvent('position opened', openTape('pred-3')));
    addBlotterEvent(tapeEvent('position closed', closeTape('pred-3')));

    for (let index = 0; index < 500; index += 1) {
      addBlotterEvent({ timeUtc: '2026-05-21T15:00:00Z', category: 'system', severity: 'info', message: `chatter-${index}` });
    }

    const events = blotterStore.getSnapshot().events;
    expect(events.filter((event) => tapeCategory(event) === 'untyped')).toHaveLength(60);
    expect(events.map((event) => event.message)).toEqual(expect.arrayContaining([
      'pred created', 'pred resolved', 'pred dropped', 'position opened', 'position closed',
    ]));
    expect(events.filter((event) => tapeCategory(event) === 'predictions')).toHaveLength(2);
    expect(events.filter((event) => tapeCategory(event) === 'executions')).toHaveLength(2);
    expect(events.filter((event) => tapeCategory(event) === 'drops')).toHaveLength(1);
  });

  it('caps each tape category independently at its own newest-first bound', () => {
    for (let index = 0; index < 70; index += 1) addBlotterEvent(tapeEvent(`pred-${index}`, predictionTape(`pred-${index}`)));
    for (let index = 0; index < 70; index += 1) addBlotterEvent(tapeEvent(`exec-${index}`, openTape(`pred-${index}`)));
    for (let index = 0; index < 50; index += 1) addBlotterEvent(tapeEvent(`drop-${index}`, dropTape(`pred-${index}`)));

    const events = blotterStore.getSnapshot().events;
    const predictions = events.filter((event) => tapeCategory(event) === 'predictions');
    const executions = events.filter((event) => tapeCategory(event) === 'executions');
    const drops = events.filter((event) => tapeCategory(event) === 'drops');
    expect(predictions).toHaveLength(60);
    expect(predictions[0].message).toBe('pred-69');
    expect(predictions.at(-1)?.message).toBe('pred-10');
    expect(executions).toHaveLength(60);
    expect(executions[0].message).toBe('exec-69');
    expect(drops).toHaveLength(40);
    expect(drops[0].message).toBe('drop-49');
    expect(drops.at(-1)?.message).toBe('drop-10');
  });

  it('assigns unique IDs to repeated identical blotter events', () => {
    const event = { timeUtc: '2026-05-21T14:00:00Z', category: 'system' as const, severity: 'info' as const, message: 'Heartbeat', sequence: 10 };

    addBlotterEvent(event);
    addBlotterEvent(event);

    const ids = blotterStore.getSnapshot().events.map((entry) => entry.id);
    expect(new Set(ids).size).toBe(2);
    expect(ids.every((id) => id.startsWith('ws-10-'))).toBe(true);
  });

  it('bounds prediction history to the newest 100 and de-dupes by id', () => {
    for (let index = 0; index < 105; index += 1) {
      addPrediction(makePrediction(`pred-${index}`));
    }

    const predictions = predictionStore.getSnapshot().predictions;
    expect(predictions).toHaveLength(100);
    expect(predictions[0]).toMatchObject({ id: 'pred-104' });
    expect(predictions.at(-1)).toMatchObject({ id: 'pred-5' });

    addPrediction(makePrediction('pred-104'));
    expect(predictionStore.getSnapshot().predictions.filter((entry) => entry.id === 'pred-104')).toHaveLength(1);
  });

  it('annotates the matching prediction when its outcome resolves', () => {
    addPrediction(makePrediction('pred-1'));
    addPrediction(makePrediction('pred-2'));

    addOutcome(makeOutcome('pred-1'));

    const state = predictionStore.getSnapshot();
    expect(state.outcomes[0]).toMatchObject({ predictionId: 'pred-1', correct: true });
    expect(state.predictions.find((entry) => entry.id === 'pred-1')?.outcome).toMatchObject({ id: 'out-pred-1' });
    expect(state.predictions.find((entry) => entry.id === 'pred-2')?.outcome).toBeNull();
  });

  it('annotates a prediction that arrives after its outcome', () => {
    addOutcome(makeOutcome('pred-9'));
    addPrediction(makePrediction('pred-9'));

    expect(predictionStore.getSnapshot().predictions.find((entry) => entry.id === 'pred-9')?.outcome).toMatchObject({ id: 'out-pred-9' });
  });

  it('bounds outcome history to the newest 100 and de-dupes by prediction id', () => {
    for (let index = 0; index < 105; index += 1) {
      addOutcome(makeOutcome(`pred-${index}`));
    }

    const outcomes = predictionStore.getSnapshot().outcomes;
    expect(outcomes).toHaveLength(100);
    expect(outcomes[0]).toMatchObject({ predictionId: 'pred-104' });

    addOutcome(makeOutcome('pred-104'));
    expect(predictionStore.getSnapshot().outcomes.filter((entry) => entry.predictionId === 'pred-104')).toHaveLength(1);
  });

  it('annotates the matching prediction when it is dropped, leaving outcome null', () => {
    addPrediction(makePrediction('pred-1'));
    addPrediction(makePrediction('pred-2'));

    addDropped(makeDropped('pred-1'));

    const state = predictionStore.getSnapshot();
    expect(state.dropped[0]).toMatchObject({ predictionId: 'pred-1', reason: 'flatten' });
    expect(state.predictions.find((entry) => entry.id === 'pred-1')?.dropped).toMatchObject({ reason: 'flatten' });
    expect(state.predictions.find((entry) => entry.id === 'pred-1')?.outcome).toBeNull();
    expect(state.predictions.find((entry) => entry.id === 'pred-2')?.dropped).toBeNull();
  });

  it('bounds dropped history to the newest 100 and de-dupes by prediction id', () => {
    for (let index = 0; index < 105; index += 1) {
      addDropped(makeDropped(`pred-${index}`));
    }

    const dropped = predictionStore.getSnapshot().dropped;
    expect(dropped).toHaveLength(100);
    expect(dropped[0]).toMatchObject({ predictionId: 'pred-104' });

    addDropped(makeDropped('pred-104'));
    expect(predictionStore.getSnapshot().dropped.filter((entry) => entry.predictionId === 'pred-104')).toHaveLength(1);
  });

  it('clears predictions, outcomes, and dropped while keeping model status', () => {
    addPrediction(makePrediction('pred-1'));
    addOutcome(makeOutcome('pred-1'));
    addDropped(makeDropped('pred-2'));
    setModelStatus({ loaded: true, modelId: 'model-a', strategyId: null, trainingMode: null, instrument: null, featureNames: [], classMap: {}, validationOk: true, validationDetail: null, confidenceGate: null, eligibleClass: null, eligibleSessions: null });

    clearPredictions();

    expect(predictionStore.getSnapshot().predictions).toEqual([]);
    expect(predictionStore.getSnapshot().outcomes).toEqual([]);
    expect(predictionStore.getSnapshot().dropped).toEqual([]);
    expect(predictionStore.getSnapshot().modelStatus).toMatchObject({ loaded: true, modelId: 'model-a' });
  });

  it('tracks paper executions: upsert opens, close moves to the bounded table, reset clears', () => {
    const position: OpenPosition = {
      predictionId: 'pred-1',
      touchId: 'touch-1',
      direction: 'long',
      contracts: 1,
      entryTsUtc: '2026-05-21T14:00:05Z',
      entryPrice: 23000,
      entryPriceConservative: 23000.25,
      tpPrice: 23015,
      slPrice: 22970,
      session: 'ny',
      levelKind: 'pdl',
      bundleId: 'bundle-a',
      mode: 'replay',
      pointValue: 20,
      lastPrice: null,
      unrealizedPoints: null,
      unrealizedPointsConservative: null,
    };
    addOpenPosition(position);
    addOpenPosition({ ...position, entryPrice: 23001 }); // upsert by prediction id
    expect(executionStore.getSnapshot().openPositions).toHaveLength(1);
    expect(executionStore.getSnapshot().openPositions[0].entryPrice).toBe(23001);

    const execution: ClosedExecution = {
      predictionId: 'pred-1',
      touchId: 'touch-1',
      direction: 'long',
      contracts: 1,
      entryTsUtc: '2026-05-21T14:00:05Z',
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
    };
    closeOpenPosition(execution);
    expect(executionStore.getSnapshot().openPositions).toEqual([]);
    expect(executionStore.getSnapshot().closed).toHaveLength(1);

    setOpenPositions([position]);
    expect(executionStore.getSnapshot().openPositions).toHaveLength(1);
    clearExecutions();
    expect(executionStore.getSnapshot()).toEqual({ openPositions: [], closed: [] });
  });

  it('stores backend offline state without requiring chart or intelligence data', () => {
    runtimeStore.setState((current) => ({ ...current, apiOnline: false, feedReady: false, lastError: 'Backend unavailable: connect ECONNREFUSED' }));
    connectionStore.setState({ wsStatus: 'offline', error: 'socket closed' });

    expect(runtimeStore.getSnapshot()).toMatchObject({ apiOnline: false, feedReady: false, lastError: expect.stringContaining('Backend unavailable') });
    expect(connectionStore.getSnapshot()).toMatchObject({ wsStatus: 'offline', error: 'socket closed' });
    expect(marketStore.getSnapshot().currentBars).toEqual([]);
    expect(intelligenceStore.getSnapshot().warnings).toEqual([]);
  });
});
