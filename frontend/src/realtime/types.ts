export type MessageType =
  | 'system.snapshot'
  | 'system.heartbeat'
  | 'feed.status'
  | 'data_quality.warning'
  | 'market.bar.updated'
  | 'market.bar.closed'
  | 'levels.updated'
  | 'touch.detected'
  | 'observation.updated';

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

export type SnapshotPayloadDTO = {
  current_bars: BarDTO[];
  recent_closed_bars: BarDTO[];
  display_levels: DisplayLevelDTO[];
  active_observations: ObservationDTO[];
  feed_status: FeedStatusDTO;
  warnings: DataQualityWarningDTO[];
};

export type Envelope<T = unknown> = {
  version: string;
  type: MessageType;
  sequence: number;
  server_time_utc: string;
  payload: T;
};
