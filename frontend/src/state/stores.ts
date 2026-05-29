import { createStore, useStore } from './createStore';
import type { BlotterEvent, LiveStatus, MarketBar, MarketLevel, MarketTouch, Observation, ReplaySource, ReplayStatus, RuntimeSummary, Timeframe, Warning } from '../domain/models';

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

export const useRuntime = <T = RuntimeSummary>(selector?: (state: RuntimeSummary) => T) => useStore(runtimeStore, selector ?? ((state) => state as T));
export const useConnection = <T = ConnectionState>(selector?: (state: ConnectionState) => T) => useStore(connectionStore, selector ?? ((state) => state as T));
export const useMarket = <T = MarketState>(selector?: (state: MarketState) => T) => useStore(marketStore, selector ?? ((state) => state as T));
export const useIntelligence = <T = ReturnType<typeof intelligenceStore.getSnapshot>>(selector?: (state: ReturnType<typeof intelligenceStore.getSnapshot>) => T) => useStore(intelligenceStore, selector ?? ((state) => state as T));
export const useBlotter = <T = ReturnType<typeof blotterStore.getSnapshot>>(selector?: (state: ReturnType<typeof blotterStore.getSnapshot>) => T) => useStore(blotterStore, selector ?? ((state) => state as T));
export const useReplay = <T = ReturnType<typeof replayStore.getSnapshot>>(selector?: (state: ReturnType<typeof replayStore.getSnapshot>) => T) => useStore(replayStore, selector ?? ((state) => state as T));
export const useLive = <T = ReturnType<typeof liveStore.getSnapshot>>(selector?: (state: ReturnType<typeof liveStore.getSnapshot>) => T) => useStore(liveStore, selector ?? ((state) => state as T));

let blotterEventCounter = 0;

export const addBlotterEvent = (event: Omit<BlotterEvent, 'id'> & { sequence?: number }) => {
  blotterEventCounter += 1;
  const sequencePart = event.sequence === undefined ? 'local' : `ws-${event.sequence}`;
  blotterStore.setState((current) => ({
    events: [{ ...event, id: `${sequencePart}-${blotterEventCounter}-${event.timeUtc}-${event.category}-${event.message}` }, ...current.events].slice(0, 200),
  }));
};
