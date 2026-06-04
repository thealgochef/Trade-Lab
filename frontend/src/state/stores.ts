import { createStore, useStore } from './createStore';
import type { BlotterEvent, LiveStatus, MarketBar, MarketLevel, MarketTouch, ModelBundle, ModelStatus, Observation, Outcome, Prediction, ReplaySource, ReplayStatus, RuntimeSummary, Timeframe, Warning } from '../domain/models';

const MAX_PREDICTIONS = 100;
const MAX_OUTCOMES = 100;

export type PredictionState = {
  predictions: Prediction[];
  outcomes: Outcome[];
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
  modelStatus: null,
  bundles: [],
});

// Newest-first, bounded prediction history; the matching prediction is annotated
// with its outcome by prediction_id when one resolves so the UI can render the
// resolved state without a second lookup.
export const addPrediction = (prediction: Prediction) => {
  predictionStore.setState((current) => {
    const existingOutcome = current.outcomes.find((outcome) => outcome.predictionId === prediction.id) ?? null;
    const annotated = existingOutcome ? { ...prediction, outcome: existingOutcome } : prediction;
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

export const setModelStatus = (modelStatus: ModelStatus) => {
  predictionStore.setState((current) => ({ ...current, modelStatus }));
};

export const setModelBundles = (bundles: ModelBundle[]) => {
  predictionStore.setState((current) => ({ ...current, bundles }));
};

export const clearPredictions = () => {
  predictionStore.setState((current) => ({ ...current, predictions: [], outcomes: [] }));
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
