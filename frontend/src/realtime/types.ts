export type MessageType =
  | 'system.snapshot'
  | 'system.heartbeat'
  | 'feed.status'
  | 'data_quality.warning'
  | 'market.bar.updated'
  | 'market.bar.closed'
  | 'levels.updated'
  | 'touch.detected'
  | 'observation.updated'
  | 'prediction.created'
  | 'prediction.resolved'
  | 'model.status';

export type BarDTO = {
  timeframe_ticks: number;
  trading_day: string;
  bar_index?: number;
  bar_id?: string;
  open_ts_utc: string;
  close_ts_utc: string;
  open_ticks: number;
  high_ticks: number;
  low_ticks: number;
  close_ticks: number;
  volume: number;
  trade_count: number;
  is_complete: boolean;
  is_partial: boolean;
  close_reason: string | null;
};

export type DisplayLevelDTO = {
  kind: string;
  price_ticks: number;
  trading_day: string;
  origin_session: string | null;
  is_developing: boolean;
  is_eligible: boolean;
};

export type FeedStatusDTO = {
  state: string;
  mode: string;
  requested_symbol: string | null;
  raw_symbol: string | null;
  dataset: string | null;
  schema: string | null;
  last_event_ts_utc: string | null;
  last_message: string | null;
  metadata: Record<string, unknown>;
};

export type DataQualityWarningDTO = {
  code: string;
  message: string;
  severity: string;
  source: string | null;
  event_ts_utc: string | null;
  metadata: Record<string, unknown>;
};

export type TouchDTO = {
  touch_id: string;
  event_ts_utc: string;
  trading_day: string;
  session: string;
  level_kind: string;
  level_price_ticks: number;
  trade_price_ticks: number;
  requested_symbol: string;
  raw_symbol: string | null;
  instrument_id: number | null;
  created_observation: boolean;
  sequence_in_session: number;
};

export type ObservationDTO = {
  observation_id: string;
  originating_touch_id: string;
  start_ts_utc: string;
  scheduled_end_ts_utc: string;
  status: string;
  trading_day: string;
  session: string;
  level_kind: string;
  level_price_ticks: number;
};

export type PredictionDTO = {
  prediction_id: string;
  touch_id: string;
  observation_id: string;
  event_ts_utc: string;
  predicted_class: string;
  probabilities: Record<string, number>;
  feature_values: Record<string, number>;
  level_kind: string;
  level_price_ticks: number;
  direction: string;
  session: string;
  is_eligible: boolean;
  model_id: string;
  contract_id: string;
  nan_count: number;
};

export type OutcomeDTO = {
  outcome_id: string;
  prediction_id: string;
  touch_id: string;
  resolution_type: string;
  actual_class: string;
  predicted_class: string;
  correct: boolean;
  max_mfe_pts: number;
  max_mae_pts: number;
  bars_to_resolution: number;
  resolved_ts_utc: string;
};

export type ModelStatusDTO = {
  loaded: boolean;
  model_id: string | null;
  strategy_id: string | null;
  training_mode: string | null;
  instrument: string | null;
  feature_names: string[];
  class_map: Record<string, string>;
  validation_ok: boolean;
  validation_detail: string | null;
};

export type SnapshotPayloadDTO = {
  current_bars: BarDTO[];
  recent_closed_bars: BarDTO[];
  display_levels: DisplayLevelDTO[];
  active_observations: ObservationDTO[];
  feed_status: FeedStatusDTO;
  warnings: DataQualityWarningDTO[];
  predictions: PredictionDTO[];
  outcomes: OutcomeDTO[];
  model_status: ModelStatusDTO;
  session: string | null;
  trading_day: string | null;
};

export type Envelope<T = unknown> = {
  version: string;
  type: MessageType;
  sequence: number;
  server_time_utc: string;
  payload: T;
};
