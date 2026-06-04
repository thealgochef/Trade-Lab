export type Timeframe = 147 | 987 | 2000;

export type RuntimeSummary = {
  apiOnline: boolean;
  backendVersion: string | null;
  runtimeMode: string;
  requestedSymbol: string;
  instrumentRoot: string;
  supportedTimeframes: Timeframe[];
  engineReady: boolean;
  feedReady: boolean;
  feedState: string;
  replayState: string;
  session: string | null;
  tradingDay: string | null;
  lastError: string | null;
};

export type MarketBar = {
  timeframe: Timeframe | number;
  tradingDay: string;
  barIndex: number | null;
  barId: string | null;
  openTimeUtc: string;
  closeTimeUtc: string;
  openTicks: number;
  highTicks: number;
  lowTicks: number;
  closeTicks: number;
  volume: number;
  tradeCount: number;
  complete: boolean;
};

export type MarketLevel = {
  kind: string;
  priceTicks: number;
  tradingDay: string;
  originSession: string | null;
  developing: boolean;
  eligible: boolean;
};

export type MarketTouch = {
  id: string;
  timeUtc: string;
  session: string;
  levelKind: string;
  priceTicks: number;
  createdObservation: boolean;
};

export type Observation = {
  id: string;
  status: string;
  session: string;
  levelKind: string;
  startUtc: string;
  scheduledEndUtc: string;
};

export type Warning = {
  code: string;
  message: string;
  severity: string;
  source: string | null;
  timeUtc: string | null;
  metadata: WarningMetadata;
};

export type WarningMetadata = {
  schema?: string;
  detail?: string;
  dropped?: number;
  dropped_messages?: number;
  client_dropped_messages?: number;
  total_dropped_messages?: number;
};

export type BlotterEvent = {
  id: string;
  timeUtc: string;
  category: 'system' | 'feed' | 'warning' | 'market' | 'level' | 'touch' | 'observation' | 'replay' | 'live';
  severity: 'info' | 'warning' | 'error';
  message: string;
  code?: string;
  source?: string | null;
  details?: WarningMetadata;
};

export type LiveStatus = {
  state: string;
  requestedSymbol: string;
  dataset: string;
  schemas: string[];
  apiKeyConfigured: boolean;
  enabled: boolean;
  sdkAvailable: boolean | null;
  subscriptionReady: boolean;
  eventsProcessed: number;
  lastEventUtc: string | null;
  lastError: string | null;
  startedAtUtc: string | null;
  stoppedAtUtc: string | null;
};

export type ReplaySource = {
  id: string;
  label: string;
  requestedSymbol: string;
  schema: string;
  kind: string;
  sessionLabel: string | null;
  availability: string | null;
};

export type Prediction = {
  id: string;
  touchId: string;
  observationId: string;
  timeUtc: string;
  predictedClass: string;
  probabilities: Record<string, number>;
  levelKind: string;
  levelPriceTicks: number;
  direction: string;
  session: string;
  eligible: boolean;
  modelId: string;
  contractId: string;
  nanCount: number;
  outcome: Outcome | null;
};

export type Outcome = {
  id: string;
  predictionId: string;
  touchId: string;
  resolutionType: string;
  actualClass: string;
  predictedClass: string;
  correct: boolean;
  maxMfePts: number;
  maxMaePts: number;
  barsToResolution: number;
  timeUtc: string;
};

export type ModelStatus = {
  loaded: boolean;
  modelId: string | null;
  strategyId: string | null;
  trainingMode: string | null;
  instrument: string | null;
  featureNames: string[];
  classMap: Record<string, string>;
  validationOk: boolean;
  validationDetail: string | null;
};

export type ModelBundle = {
  modelId: string;
  strategyId: string;
  trainingMode: string;
  instrument: string;
  featureCount: number;
  classMap: Record<string, string>;
  hasChecksum: boolean;
  validationOk: boolean;
  validationDetail: string;
};

export type ReplayStatus = {
  state: string;
  sourceId: string | null;
  sourceLabel: string | null;
  eventsProcessed: number;
  warningsRecorded: number;
  lastEventUtc: string | null;
  lastError: string | null;
  startedAtUtc: string | null;
  completedAtUtc: string | null;
  failedAtUtc: string | null;
};
