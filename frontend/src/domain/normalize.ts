import type { LiveStatusDTO, ReplaySourceDTO, ReplayStatusDTO, RuntimeStatusDTO } from '../api/types';
import type { BarDTO, DataQualityWarningDTO, DisplayLevelDTO, ObservationDTO, TouchDTO } from '../realtime/types';
import type { LiveStatus, MarketBar, MarketLevel, MarketTouch, Observation, ReplaySource, ReplayStatus, RuntimeSummary, Timeframe, Warning, WarningMetadata } from './models';

// API DTOs stay at the transport boundary; components consume these narrower
// workstation models so future backend contract changes do not leak everywhere.
export const normalizeRuntimeStatus = (dto: RuntimeStatusDTO): RuntimeSummary => ({
  apiOnline: true,
  backendVersion: dto.version,
  runtimeMode: dto.runtime_mode,
  requestedSymbol: dto.requested_symbol,
  instrumentRoot: dto.instrument_root,
  supportedTimeframes: dto.supported_tick_timeframes.filter(isTimeframe),
  engineReady: dto.engine_ready,
  feedReady: dto.feed_ready,
  feedState: dto.feed_state,
  replayState: dto.replay.state,
  tradingDay: null,
  lastError: dto.replay.last_error,
});

export const normalizeReplayStatus = (dto: ReplayStatusDTO): ReplayStatus => ({
  state: dto.state,
  sourceId: dto.source_id ?? null,
  sourceLabel: dto.source_label ?? null,
  eventsProcessed: dto.events_processed,
  warningsRecorded: dto.warnings_recorded,
  lastEventUtc: dto.last_event_ts_utc,
  lastError: dto.last_error,
  startedAtUtc: dto.started_at_utc ?? null,
  completedAtUtc: dto.completed_at_utc ?? null,
  failedAtUtc: dto.failed_at_utc ?? null,
});

export const normalizeReplaySource = (dto: ReplaySourceDTO): ReplaySource => ({
  id: dto.source_id,
  label: dto.label,
  requestedSymbol: dto.requested_symbol,
  schema: dto.schema,
  kind: dto.kind === 'historical' ? 'historical' : 'synthetic',
  sessionLabel: dto.session_label ?? null,
  availability: dto.availability ?? null,
});

export const normalizeLiveStatus = (dto: LiveStatusDTO): LiveStatus => ({
  state: dto.state,
  requestedSymbol: dto.requested_symbol,
  dataset: dto.dataset,
  schemas: dto.schemas,
  apiKeyConfigured: dto.api_key_configured,
  enabled: dto.enabled,
  sdkAvailable: dto.sdk_available ?? null,
  subscriptionReady: dto.subscription_ready ?? (dto.enabled && dto.api_key_configured),
  eventsProcessed: dto.events_processed,
  lastEventUtc: dto.last_event_ts_utc,
  lastError: dto.last_error,
  startedAtUtc: dto.started_at_utc,
  stoppedAtUtc: dto.stopped_at_utc,
});

export const normalizeBar = (dto: BarDTO): MarketBar => ({
  timeframe: dto.timeframe_ticks,
  tradingDay: dto.trading_day,
  barIndex: typeof dto.bar_index === 'number' ? dto.bar_index : null,
  barId: typeof dto.bar_id === 'string' ? dto.bar_id : null,
  openTimeUtc: dto.open_ts_utc,
  closeTimeUtc: dto.close_ts_utc,
  openTicks: dto.open_ticks,
  highTicks: dto.high_ticks,
  lowTicks: dto.low_ticks,
  closeTicks: dto.close_ticks,
  volume: dto.volume,
  tradeCount: dto.trade_count,
  complete: dto.is_complete,
});

export const normalizeLevel = (dto: DisplayLevelDTO): MarketLevel => ({
  kind: dto.kind,
  priceTicks: dto.price_ticks,
  tradingDay: dto.trading_day,
  originSession: dto.origin_session,
  developing: dto.is_developing,
  eligible: dto.is_eligible,
});

export const normalizeTouch = (dto: TouchDTO): MarketTouch => ({
  id: dto.touch_id,
  timeUtc: dto.event_ts_utc,
  session: dto.session,
  levelKind: dto.level_kind,
  priceTicks: dto.trade_price_ticks,
  createdObservation: dto.created_observation,
});

export const normalizeObservation = (dto: ObservationDTO): Observation => ({
  id: dto.observation_id,
  status: dto.status,
  session: dto.session,
  levelKind: dto.level_kind,
  startUtc: dto.start_ts_utc,
  scheduledEndUtc: dto.scheduled_end_ts_utc,
});

export const normalizeWarning = (dto: DataQualityWarningDTO): Warning => ({
  code: dto.code,
  message: dto.message,
  severity: dto.severity,
  source: dto.source,
  timeUtc: dto.event_ts_utc,
  metadata: safeWarningMetadata(dto.metadata),
});

const isTimeframe = (value: number): value is Timeframe => [147, 987, 2000].includes(value);

const safeStringKeys = new Set(['schema', 'detail']);
const safeNumberKeys = new Set(['dropped', 'dropped_messages', 'client_dropped_messages', 'total_dropped_messages']);

const safeWarningMetadata = (metadata: Record<string, unknown>): WarningMetadata => {
  const safe: WarningMetadata = {};
  for (const [key, value] of Object.entries(metadata ?? {})) {
    if (safeStringKeys.has(key) && typeof value === 'string' && !containsSecretLikeText(value)) {
      safe[key as 'schema' | 'detail'] = value.slice(0, 500);
    } else if (safeNumberKeys.has(key) && typeof value === 'number' && Number.isFinite(value)) {
      safe[key as 'dropped' | 'dropped_messages' | 'client_dropped_messages' | 'total_dropped_messages'] = value;
    }
  }
  return safe;
};

const containsSecretLikeText = (value: string) => /api[_-]?key|secret|token|password|credential|[A-Za-z]:\\|\/(?:[^\s/]+\/)+/i.test(value);
