import { createStore, useStore } from './createStore';
import type { BlotterEvent, DroppedPrediction, LiveStatus, MarketBar, MarketLevel, MarketTouch, ModelBundle, ModelStatus, Observation, Outcome, Prediction, ReplaySource, ReplayStatus, RuntimeSummary, Timeframe, Warning } from '../domain/models';

const MAX_PREDICTIONS = 100;
const MAX_OUTCOMES = 100;
const MAX_DROPPED = 100;

export type PredictionState = {
  predictions: Prediction[];
  outcomes: Outcome[];
  dropped: DroppedPrediction[];
  modelStatus: ModelStatus | null;
  bundles: ModelBundle[];
};

export type ConnectionState = {
  wsStatus: 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'offline';
  lastHeartbeatUtc: string | null;
  lastServerTimeUtc: string | null;
  lastSequence: number | null;
  reconnectAttempt: number;
  error: string | null;
};

export type MarketState = {
  selectedTimeframe: Timeframe;
  currentBars: MarketBar[];
  recentClosedBars: MarketBar[];
};

export const runtimeStore = createStore<RuntimeSummary>({
  apiOnline: false,
  backendVersion: null,
  runtimeMode: 'unknown',
  requestedSymbol: 'NQ.c.0',
  instrumentRoot: 'NQ',
  supportedTimeframes: [147, 987, 2000],
  engineReady: false,
  feedReady: false,
  feedState: 'unknown',
  replayState: 'unknown',
  session: null,
  tradingDay: null,
  lastError: null,
});

export const connectionStore = createStore<ConnectionState>({
  wsStatus: 'idle',
  lastHeartbeatUtc: null,
  lastServerTimeUtc: null,
  lastSequence: null,
  reconnectAttempt: 0,
  error: null,
});

export const marketStore = createStore<MarketState>({
  selectedTimeframe: 147,
  currentBars: [],
  recentClosedBars: [],
});

export const intelligenceStore = createStore({
  levels: [] as MarketLevel[],
  touches: [] as MarketTouch[],
  observations: [] as Observation[],
  warnings: [] as Warning[],
});

export const blotterStore = createStore({ events: [] as BlotterEvent[] });

export const replayStore = createStore({
  sources: [] as ReplaySource[],
  selectedSourceId: 'synthetic:nq-demo',
  historical: null as { available: boolean; status: string; diagnostics?: Record<string, boolean | number | string> } | null,
  status: {
    state: 'unknown',
    sourceId: null,
    sourceLabel: null,
    eventsProcessed: 0,
    warningsRecorded: 0,
    lastEventUtc: null,
    lastError: null,
    startedAtUtc: null,
    completedAtUtc: null,
    failedAtUtc: null,
  } as ReplayStatus,
  loading: false,
  error: null as string | null,
});

export const liveStore = createStore({
  status: {
    state: 'idle',
    requestedSymbol: 'NQ.c.0',
    dataset: 'GLBX.MDP3',
    schemas: ['trades', 'mbp-1'],
    apiKeyConfigured: false,
    enabled: false,
    sdkAvailable: null,
    subscriptionReady: false,
    eventsProcessed: 0,
    lastEventUtc: null,
    lastError: null,
    startedAtUtc: null,
    stoppedAtUtc: null,
  } as LiveStatus,
  loading: false,
  error: null as string | null,
});

export const predictionStore = createStore<PredictionState>({
  predictions: [],
  outcomes: [],
  dropped: [],
  modelStatus: null,
  bundles: [],
});

// Newest-first, bounded prediction history; the matching prediction is annotated
// with its outcome (or drop) by prediction_id when one arrives so the UI can
// render the resolved/dropped state without a second lookup.
export const addPrediction = (prediction: Prediction) => {
  predictionStore.setState((current) => {
    const existingOutcome = current.outcomes.find((outcome) => outcome.predictionId === prediction.id) ?? null;
    const existingDrop = current.dropped.find((drop) => drop.predictionId === prediction.id) ?? null;
    const annotated = { ...prediction, outcome: existingOutcome, dropped: existingDrop };
    const without = current.predictions.filter((entry) => entry.id !== prediction.id);
    return { ...current, predictions: [annotated, ...without].slice(0, MAX_PREDICTIONS) };
  });
};

export const addOutcome = (outcome: Outcome) => {
  predictionStore.setState((current) => {
    const withoutOutcome = current.outcomes.filter((entry) => entry.predictionId !== outcome.predictionId);
    const predictions = current.predictions.map((prediction) =>
      prediction.id === outcome.predictionId ? { ...prediction, outcome } : prediction,
    );
    return { ...current, outcomes: [outcome, ...withoutOutcome].slice(0, MAX_OUTCOMES), predictions };
  });
};

// Drops mirror outcomes (newest-first, capped, de-duped by prediction id) but stay
// out of `prediction.outcome` so they can never produce a chart outcome marker.
export const addDropped = (dropped: DroppedPrediction) => {
  predictionStore.setState((current) => {
    const withoutDrop = current.dropped.filter((entry) => entry.predictionId !== dropped.predictionId);
    const predictions = current.predictions.map((prediction) =>
      prediction.id === dropped.predictionId ? { ...prediction, dropped } : prediction,
    );
    return { ...current, dropped: [dropped, ...withoutDrop].slice(0, MAX_DROPPED), predictions };
  });
};

export const setModelStatus = (modelStatus: ModelStatus) => {
  predictionStore.setState((current) => ({ ...current, modelStatus }));
};

export const setModelBundles = (bundles: ModelBundle[]) => {
  predictionStore.setState((current) => ({ ...current, bundles }));
};

export const clearPredictions = () => {
  predictionStore.setState((current) => ({ ...current, predictions: [], outcomes: [], dropped: [] }));
};

export const useRuntime = <T = RuntimeSummary>(selector?: (state: RuntimeSummary) => T) => useStore(runtimeStore, selector ?? ((state) => state as T));
export const useConnection = <T = ConnectionState>(selector?: (state: ConnectionState) => T) => useStore(connectionStore, selector ?? ((state) => state as T));
export const useMarket = <T = MarketState>(selector?: (state: MarketState) => T) => useStore(marketStore, selector ?? ((state) => state as T));
export const useIntelligence = <T = ReturnType<typeof intelligenceStore.getSnapshot>>(selector?: (state: ReturnType<typeof intelligenceStore.getSnapshot>) => T) => useStore(intelligenceStore, selector ?? ((state) => state as T));
export const useBlotter = <T = ReturnType<typeof blotterStore.getSnapshot>>(selector?: (state: ReturnType<typeof blotterStore.getSnapshot>) => T) => useStore(blotterStore, selector ?? ((state) => state as T));
export const useReplay = <T = ReturnType<typeof replayStore.getSnapshot>>(selector?: (state: ReturnType<typeof replayStore.getSnapshot>) => T) => useStore(replayStore, selector ?? ((state) => state as T));
export const useLive = <T = ReturnType<typeof liveStore.getSnapshot>>(selector?: (state: ReturnType<typeof liveStore.getSnapshot>) => T) => useStore(liveStore, selector ?? ((state) => state as T));
export const usePredictions = <T = Prediction[]>(selector?: (predictions: Prediction[]) => T) => useStore(predictionStore, (state) => (selector ? selector(state.predictions) : (state.predictions as T)));
export const useOutcomes = <T = Outcome[]>(selector?: (outcomes: Outcome[]) => T) => useStore(predictionStore, (state) => (selector ? selector(state.outcomes) : (state.outcomes as T)));
export const useDropped = <T = DroppedPrediction[]>(selector?: (dropped: DroppedPrediction[]) => T) => useStore(predictionStore, (state) => (selector ? selector(state.dropped) : (state.dropped as T)));
export const useModelStatus = <T = ModelStatus | null>(selector?: (modelStatus: ModelStatus | null) => T) => useStore(predictionStore, (state) => (selector ? selector(state.modelStatus) : (state.modelStatus as T)));
export const useBundles = <T = ModelBundle[]>(selector?: (bundles: ModelBundle[]) => T) => useStore(predictionStore, (state) => (selector ? selector(state.bundles) : (state.bundles as T)));

let blotterEventCounter = 0;

export const addBlotterEvent = (event: Omit<BlotterEvent, 'id'> & { sequence?: number }) => {
  blotterEventCounter += 1;
  const sequencePart = event.sequence === undefined ? 'local' : `ws-${event.sequence}`;
  blotterStore.setState((current) => ({
    events: [{ ...event, id: `${sequencePart}-${blotterEventCounter}-${event.timeUtc}-${event.category}-${event.message}` }, ...current.events].slice(0, 200),
  }));
};
