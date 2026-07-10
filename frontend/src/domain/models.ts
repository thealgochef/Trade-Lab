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
  // COCKPIT P3c: fields for the active-setup card. originatingTouchId joins the
  // touches stream for the touch price; levelPriceTicks anchors the setup;
  // direction is the AUTHORITATIVE carried touch direction (audit #NN-1 — never
  // re-derived from levelKind), null for legacy observations.
  originatingTouchId: string | null;
  levelPriceTicks: number | null;
  direction: string | null;
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
  category: 'system' | 'feed' | 'warning' | 'market' | 'level' | 'touch' | 'observation' | 'execution' | 'replay' | 'live';
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
  dropped: DroppedPrediction | null;
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
  entryPrice: number;
};

export type DroppedPrediction = {
  predictionId: string;
  touchId: string;
  reason: string;
  decisionTsUtc: string;
  entryPrice: number | null;
};

// EXEC P3: paper-execution positions derived by the backend observer tracker.
// Both P&L columns ride every position: optimistic (exact anchor/barriers) and
// conservative (1-tick-adverse entry; 1-tick-adverse sl exit).
export type OpenPosition = {
  predictionId: string;
  touchId: string;
  direction: string;
  contracts: number;
  entryTsUtc: string;
  entryPrice: number;
  entryPriceConservative: number;
  tpPrice: number;
  slPrice: number;
  session: string;
  levelKind: string;
  bundleId: string;
  mode: string;
  pointValue: number;
  lastPrice: number | null;
  unrealizedPoints: number | null;
  unrealizedPointsConservative: number | null;
};

export type ClosedExecution = {
  predictionId: string;
  touchId: string;
  direction: string;
  contracts: number;
  entryTsUtc: string;
  exitTsUtc: string | null;
  reason: string;
  entryPrice: number;
  entryPriceConservative: number;
  exitPrice: number;
  exitPriceConservative: number;
  points: number;
  pointsConservative: number;
  dollars: number;
  dollarsConservative: number;
  pointValue: number;
  session: string;
  levelKind: string;
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
  // COCKPIT P1/P3: serving-gate parameters for the gate-math card. Null when no
  // model is loaded (or an older backend omits them). eligibleSessions is the
  // runtime session vocabulary, comparable to prediction.session directly.
  confidenceGate: number | null;
  eligibleClass: string | null;
  eligibleSessions: string[] | null;
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
