import { describe, expect, it } from 'vitest';
import { normalizeBar, normalizeLevel, normalizeModelBundle, normalizeModelStatus, normalizeOutcome, normalizePrediction, normalizeRuntimeStatus, normalizeWarning } from './normalize';
import type { ModelBundleDTO, RuntimeStatusDTO } from '../api/types';
import type { BarDTO, DisplayLevelDTO, ModelStatusDTO, OutcomeDTO, PredictionDTO } from '../realtime/types';

describe('normalizeRuntimeStatus', () => {
  it('keeps API DTOs out of component-facing runtime shape', () => {
    const dto: RuntimeStatusDTO = {
      service: 'trade-lab-backend',
      version: '0.1.0',
      runtime_mode: 'idle',
      requested_symbol: 'NQ.c.0',
      instrument_root: 'NQ',
      supported_tick_timeframes: [147, 987, 2000, 5],
      engine_ready: true,
      feed_ready: false,
      feed_state: 'disconnected',
      session: 'ny',
      trading_day: '2026-05-21',
      replay: {
        state: 'idle',
        events_processed: 0,
        warnings_recorded: 0,
        last_event_ts_utc: null,
        last_error: null,
        requested_symbol: null,
        schema: null,
      },
      live: {
        state: 'idle',
        requested_symbol: 'NQ.c.0',
        dataset: 'GLBX.MDP3',
        schemas: ['trades'],
        api_key_configured: false,
        enabled: false,
        events_processed: 0,
        last_event_ts_utc: null,
        last_error: null,
        started_at_utc: null,
        stopped_at_utc: null,
      },
    };

    expect(normalizeRuntimeStatus(dto)).toMatchObject({
      apiOnline: true,
      requestedSymbol: 'NQ.c.0',
      supportedTimeframes: [147, 987, 2000],
      replayState: 'idle',
      session: 'ny',
      tradingDay: '2026-05-21',
    });
  });

  it('defaults session and trading day to null when absent', () => {
    const dto = {
      service: 'trade-lab-backend',
      version: '0.1.0',
      runtime_mode: 'idle',
      requested_symbol: 'NQ.c.0',
      instrument_root: 'NQ',
      supported_tick_timeframes: [147, 987, 2000],
      engine_ready: true,
      feed_ready: false,
      feed_state: 'disconnected',
      replay: { state: 'idle', events_processed: 0, warnings_recorded: 0, last_event_ts_utc: null, last_error: null, requested_symbol: null, schema: null },
      live: { state: 'idle', requested_symbol: 'NQ.c.0', dataset: 'GLBX.MDP3', schemas: ['trades'], api_key_configured: false, enabled: false, events_processed: 0, last_event_ts_utc: null, last_error: null, started_at_utc: null, stopped_at_utc: null },
    } as RuntimeStatusDTO;

    expect(normalizeRuntimeStatus(dto)).toMatchObject({ session: null, tradingDay: null });
  });
});

describe('inference DTO normalization', () => {
  const predictionDto = (overrides: Partial<PredictionDTO> = {}): PredictionDTO => ({
    prediction_id: 'pred-1',
    touch_id: 'touch-1',
    observation_id: 'obs-1',
    event_ts_utc: '2026-05-21T14:02:00Z',
    predicted_class: 'continuation',
    probabilities: { continuation: 0.7, reversal: 0.3 },
    feature_values: { f0: 1.2, f1: -0.4 },
    level_kind: 'pdh',
    level_price_ticks: 76000,
    direction: 'long',
    session: 'ny',
    is_eligible: true,
    model_id: 'model-a',
    contract_id: 'NQM6',
    nan_count: 0,
    ...overrides,
  });

  it('maps predictions to domain shape and drops raw feature vectors', () => {
    const prediction = normalizePrediction(predictionDto());

    expect(prediction).toMatchObject({
      id: 'pred-1',
      touchId: 'touch-1',
      observationId: 'obs-1',
      predictedClass: 'continuation',
      probabilities: { continuation: 0.7, reversal: 0.3 },
      direction: 'long',
      modelId: 'model-a',
      contractId: 'NQM6',
      outcome: null,
    });
    expect('featureValues' in prediction).toBe(false);
    expect(JSON.stringify(prediction)).not.toContain('feature_values');
  });

  it('drops non-finite probability values', () => {
    const prediction = normalizePrediction(predictionDto({ probabilities: { up: 0.6, bad: Number.NaN } as Record<string, number> }));
    expect(prediction.probabilities).toEqual({ up: 0.6 });
  });

  it('maps outcomes to domain shape', () => {
    const dto: OutcomeDTO = {
      outcome_id: 'out-1',
      prediction_id: 'pred-1',
      touch_id: 'touch-1',
      resolution_type: 'target',
      actual_class: 'continuation',
      predicted_class: 'continuation',
      correct: true,
      max_mfe_pts: 12.5,
      max_mae_pts: 3.0,
      bars_to_resolution: 8,
      resolved_ts_utc: '2026-05-21T14:10:00Z',
    };

    expect(normalizeOutcome(dto)).toEqual({
      id: 'out-1',
      predictionId: 'pred-1',
      touchId: 'touch-1',
      resolutionType: 'target',
      actualClass: 'continuation',
      predictedClass: 'continuation',
      correct: true,
      maxMfePts: 12.5,
      maxMaePts: 3.0,
      barsToResolution: 8,
      timeUtc: '2026-05-21T14:10:00Z',
    });
  });

  it('maps model status without leaking any path or secret', () => {
    const dto: ModelStatusDTO = {
      loaded: true,
      model_id: 'model-a',
      strategy_id: 'strat-1',
      training_mode: 'offline',
      instrument: 'NQ',
      feature_names: ['f0', 'f1'],
      class_map: { '0': 'continuation', '1': 'reversal' },
      validation_ok: true,
      validation_detail: 'ok',
    };

    expect(normalizeModelStatus(dto)).toEqual({
      loaded: true,
      modelId: 'model-a',
      strategyId: 'strat-1',
      trainingMode: 'offline',
      instrument: 'NQ',
      featureNames: ['f0', 'f1'],
      classMap: { '0': 'continuation', '1': 'reversal' },
      validationOk: true,
      validationDetail: 'ok',
    });
  });

  it('maps model status with null optional fields when not loaded', () => {
    const dto: ModelStatusDTO = {
      loaded: false,
      model_id: null,
      strategy_id: null,
      training_mode: null,
      instrument: null,
      feature_names: [],
      class_map: {},
      validation_ok: false,
      validation_detail: null,
    };

    expect(normalizeModelStatus(dto)).toMatchObject({ loaded: false, modelId: null, strategyId: null, featureNames: [], classMap: {}, validationDetail: null });
  });

  it('maps model bundle descriptors without any path field', () => {
    const dto: ModelBundleDTO = {
      model_id: 'model-a',
      strategy_id: 'strat-1',
      training_mode: 'offline',
      instrument: 'NQ',
      feature_count: 12,
      class_map: { '0': 'continuation', '1': 'reversal' },
      has_checksum: true,
      validation_ok: true,
      validation_detail: 'ok',
    };

    const bundle = normalizeModelBundle(dto);
    expect(bundle).toEqual({
      modelId: 'model-a',
      strategyId: 'strat-1',
      trainingMode: 'offline',
      instrument: 'NQ',
      featureCount: 12,
      classMap: { '0': 'continuation', '1': 'reversal' },
      hasChecksum: true,
      validationOk: true,
      validationDetail: 'ok',
    });
    expect(JSON.stringify(bundle).toLowerCase()).not.toMatch(/path|[a-z]:\\|\/(?:[^\s/]+\/)/);
  });
});

describe('market DTO normalization', () => {
  it('normalizes tick bar payloads for chart state', () => {
    const dto: BarDTO = {
      timeframe_ticks: 987,
      trading_day: '2026-05-21',
      bar_index: 12,
      bar_id: '987t:2026-05-21:12',
      open_ts_utc: '2026-05-21T14:00:00Z',
      close_ts_utc: '2026-05-21T14:01:00Z',
      open_ticks: 76000,
      high_ticks: 76012,
      low_ticks: 75996,
      close_ticks: 76008,
      volume: 123,
      trade_count: 45,
      is_complete: false,
      is_partial: true,
      close_reason: null,
    };

    expect(normalizeBar(dto)).toEqual({
      timeframe: 987,
      tradingDay: '2026-05-21',
      barIndex: 12,
      barId: '987t:2026-05-21:12',
      openTimeUtc: '2026-05-21T14:00:00Z',
      closeTimeUtc: '2026-05-21T14:01:00Z',
      openTicks: 76000,
      highTicks: 76012,
      lowTicks: 75996,
      closeTicks: 76008,
      volume: 123,
      tradeCount: 45,
      complete: false,
    });
  });

  it('preserves display-only and eligible level flags', () => {
    const displayOnly: DisplayLevelDTO = { kind: 'asia_high', price_ticks: 76100, trading_day: '2026-05-21', origin_session: 'asia', is_developing: true, is_eligible: false };
    const eligible: DisplayLevelDTO = { ...displayOnly, kind: 'pdh', is_developing: false, is_eligible: true };

    expect(normalizeLevel(displayOnly)).toMatchObject({ kind: 'asia_high', developing: true, eligible: false });
    expect(normalizeLevel(eligible)).toMatchObject({ kind: 'pdh', developing: false, eligible: true });
  });

  it('normalizes warnings with only allowlisted safe metadata', () => {
    const warning = normalizeWarning({
      code: 'gap',
      message: 'Gap detected',
      severity: 'warning',
      source: null,
      event_ts_utc: null,
      metadata: { public: true, schema: 'mbp-1', detail: 'safe provider detail', dropped: 2, token: 'secret', path: 'C:\\secret\\file' },
    });

    expect(warning).toEqual({ code: 'gap', message: 'Gap detected', severity: 'warning', source: null, timeUtc: null, metadata: { schema: 'mbp-1', detail: 'safe provider detail', dropped: 2 } });
    expect(JSON.stringify(warning).toLowerCase()).not.toMatch(/api[_-]?key|secret|token|password|credential/);
  });

  it('drops allowlisted string metadata when values look like secrets or paths', () => {
    const warning = normalizeWarning({
      code: 'provider_error',
      message: 'Databento provider reported an error',
      severity: 'warning',
      source: 'databento',
      event_ts_utc: '2026-05-21T14:00:00Z',
      metadata: {
        schema: '18',
        detail: 'api_key=db-secret path=C:\\Users\\operator\\secret.txt',
        dropped_messages: Number.NaN,
        total_dropped_messages: 4,
        credentials: 'db-secret',
      },
    });

    expect(warning.metadata).toEqual({ schema: '18', total_dropped_messages: 4 });
    expect(JSON.stringify(warning)).not.toContain('db-secret');
    expect(JSON.stringify(warning)).not.toContain('C:\\Users');
  });
});
