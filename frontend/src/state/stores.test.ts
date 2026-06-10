import { afterEach, describe, expect, it } from 'vitest';
import { addBlotterEvent, addDropped, addOutcome, addPrediction, blotterStore, clearPredictions, connectionStore, intelligenceStore, marketStore, predictionStore, runtimeStore, setModelStatus } from './stores';
import type { DroppedPrediction, Outcome, Prediction } from '../domain/models';

const resetStores = () => {
  runtimeStore.reset();
  connectionStore.reset();
  marketStore.reset();
  intelligenceStore.reset();
  blotterStore.reset();
  predictionStore.reset();
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

  it('bounds event blotter retention to the newest 200 events', () => {
    for (let index = 0; index < 205; index += 1) {
      addBlotterEvent({ timeUtc: `2026-05-21T14:${String(index).padStart(2, '0')}:00Z`, category: 'system', severity: 'info', message: `event-${index}` });
    }

    const events = blotterStore.getSnapshot().events;
    expect(events).toHaveLength(200);
    expect(events[0].message).toBe('event-204');
    expect(events.at(-1)?.message).toBe('event-5');
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
    setModelStatus({ loaded: true, modelId: 'model-a', strategyId: null, trainingMode: null, instrument: null, featureNames: [], classMap: {}, validationOk: true, validationDetail: null });

    clearPredictions();

    expect(predictionStore.getSnapshot().predictions).toEqual([]);
    expect(predictionStore.getSnapshot().outcomes).toEqual([]);
    expect(predictionStore.getSnapshot().dropped).toEqual([]);
    expect(predictionStore.getSnapshot().modelStatus).toMatchObject({ loaded: true, modelId: 'model-a' });
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
