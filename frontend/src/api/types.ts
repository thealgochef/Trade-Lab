export type HealthDTO = {
  ok: boolean;
  service: string;
  version: string;
};

export type ReplayStatusDTO = {
  state: string;
  events_processed: number;
  warnings_recorded: number;
  last_event_ts_utc: string | null;
  last_error: string | null;
  requested_symbol: string | null;
  schema: string | null;
  source_id?: string | null;
  source_label?: string | null;
  started_at_utc?: string | null;
  completed_at_utc?: string | null;
  failed_at_utc?: string | null;
};

export type ReplaySourceDTO = {
  source_id: string;
  label: string;
  requested_symbol: string;
  schema: string;
  kind?: 'synthetic' | 'historical' | string;
  session_label?: string | null;
  availability?: string | null;
};

export type ReplaySourcesResponseDTO = {
  sources: ReplaySourceDTO[];
  historical?: {
    available: boolean;
    status: string;
    diagnostics?: Record<string, boolean | number | string>;
  };
};

export type ReplayStartRequestDTO = {
  source_id: string;
  speed?: number;
  max_events?: number;
};

export type RuntimeStatusDTO = {
  service: string;
  version: string;
  runtime_mode: string;
  requested_symbol: string;
  instrument_root: string;
  supported_tick_timeframes: number[];
  engine_ready: boolean;
  feed_ready: boolean;
  feed_state: string;
  replay: ReplayStatusDTO;
  live: LiveStatusDTO;
};

export type LiveStatusDTO = {
  state: string;
  requested_symbol: string;
  dataset: string;
  schemas: string[];
  api_key_configured: boolean;
  enabled: boolean;
  sdk_available?: boolean | null;
  subscription_ready?: boolean;
  events_processed: number;
  last_event_ts_utc: string | null;
  last_error: string | null;
  started_at_utc: string | null;
  stopped_at_utc: string | null;
};

export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string; status?: number };
