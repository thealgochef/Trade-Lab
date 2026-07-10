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
  session?: string | null;
  trading_day?: string | null;
  replay: ReplayStatusDTO;
  live: LiveStatusDTO;
};

export type ModelBundleDTO = {
  model_id: string;
  strategy_id: string;
  training_mode: string;
  instrument: string;
  feature_count: number;
  class_map: Record<string, string>;
  has_checksum: boolean;
  validation_ok: boolean;
  validation_detail: string;
};

export type ModelsResponseDTO = {
  models: ModelBundleDTO[];
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

// --- GET /api/v1/performance (REPORT P3) -----------------------------------
// Every rate carries numerator/denominator and every mean carries its n — the
// backend never emits a bare percentage.

export type RatioDTO = { value: number | null; numerator: number; denominator: number };
export type MeanStatDTO = { value: number | null; n: number };
export type DistStatDTO = { mean: number | null; median: number | null; max: number | null; n: number };

export type PerformanceDayDTO = {
  trading_day: string;
  net_points: number;
  cumulative_net_points: number;
  resolved: number;
  priced: number;
  wins: number;
  losses: number;
  unpriced: number;
  predictions: number;
  eligible_predictions: number;
  drops: number;
};

export type PerformanceDirectionDTO = {
  resolved: number;
  wins: number;
  priced: number;
  net_points: number;
  win_rate: RatioDTO;
  expectancy_pts: MeanStatDTO;
};

export type PerformanceHeadlineDTO = {
  predictions: number;
  eligible_predictions: number;
  resolved_trades: number;
  eligible_trades: number;
  priced_trades: number;
  unpriced_trades: number;
  win_rate: RatioDTO;
  class_accuracy: RatioDTO;
  net_points: { value: number; n_priced: number };
  gross_win_points: number;
  gross_loss_points: number;
  profit_factor: { value: number | null; gross_win: number; gross_loss: number };
  avg_win_pts: MeanStatDTO;
  avg_loss_pts: MeanStatDTO;
  avg_time_to_resolution_seconds: MeanStatDTO;
  avg_bars_to_resolution: MeanStatDTO;
  direction: Record<string, PerformanceDirectionDTO>;
  day_win_pct: RatioDTO;
  mfe_pts: DistStatDTO;
  mae_pts: DistStatDTO;
  point_value: number | null;
};

export type PerformanceClassRowDTO = {
  predicted: number;
  actual: number;
  precision: RatioDTO;
  recall: RatioDTO;
  win_rate: RatioDTO;
  net_points: { value: number; n_priced: number };
};

export type PerformanceKeyRowDTO = {
  resolved: number;
  wins: number;
  priced: number;
  net_points: number;
  predictions: number;
  win_rate: RatioDTO;
};

export type PerformanceBreakdownsDTO = {
  per_class: Record<string, PerformanceClassRowDTO>;
  per_level_kind: Record<string, PerformanceKeyRowDTO>;
  per_session: Record<string, PerformanceKeyRowDTO>;
  drop_reasons: Record<string, number>;
};

export type PerformanceFunnelDTO = {
  note: string;
  predictions: number;
  eligible: number;
  resolved: number;
  dropped: number;
  pending: number;
  priced: number;
};

export type PerformanceAnomaliesDTO = {
  note: string;
  files_scanned: number;
  lines_total: number;
  malformed_lines: number;
  unknown_type_rows: number;
  rows_missing_ids: number;
  undated_rows: number;
  duplicate_prediction_rows: number;
  duplicate_outcome_rows: number;
  duplicate_drop_rows: number;
  orphan_outcomes: number;
  orphan_drops: number;
  outcome_drop_conflicts: number;
  scoped: {
    unknown_resolution_trades: number;
    unpriced_trades: number;
    excluded_unknown_session: number;
    excluded_unknown_eligibility: number;
  };
};

export type PerformanceOOSGatedDTO = {
  trade_count: number | null;
  precision: number | null;
  expectancy_pts: number | null;
  profit_factor: number | null;
  coverage: number | null;
  confidence_gate: number | null;
  eligible_sessions: string[] | null;
  n_samples: number | null;
  pricing_rule: string;
};

export type PerformanceOOSComparisonDTO = {
  bundle_id: string;
  oos: {
    available: boolean;
    gated: PerformanceOOSGatedDTO | null;
    quality_gates: {
      all_passed: boolean | null;
      gates: Record<string, { passed: boolean | null; value: number | null; threshold: number | null }>;
    } | null;
    class_distribution: {
      actual: Record<string, number> | null;
      predicted: Record<string, number> | null;
      n?: number;
    } | null;
    mfe_mae: null;
    mfe_mae_note: string;
  };
  journal: {
    resolved_trades: number;
    eligible_trades: number;
    gated_hit_rate: RatioDTO & { definition: string };
    gated_win_rate: RatioDTO;
    gated_expectancy_pts: { value: number | null; n: number; pricing_rule: string };
    class_distribution: { predicted: Record<string, number>; actual: Record<string, number>; n: number };
    mfe_mae: { mfe_pts: DistStatDTO; mae_pts: DistStatDTO };
    priced_trades: number;
  };
};

export type PerformanceReportDTO = {
  applied_filters: {
    mode: string;
    from: string | null;
    to: string | null;
    bundle_id: string | null;
    session: string | null;
    eligibility: string;
  };
  headline: PerformanceHeadlineDTO;
  series: PerformanceDayDTO[];
  breakdowns: PerformanceBreakdownsDTO;
  funnel: PerformanceFunnelDTO;
  anomalies: PerformanceAnomaliesDTO;
  pricing: {
    rule: string;
    sources: Record<string, number>;
    fallback_contract: { tp_points: number; sl_points: number; point_value: number | null } | null;
  };
  oos_comparison: PerformanceOOSComparisonDTO | null;
};

export type PerformanceQuery = {
  mode?: string;
  from?: string;
  to?: string;
  bundle?: string;
};
