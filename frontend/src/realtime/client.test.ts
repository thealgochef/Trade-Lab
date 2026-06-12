import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RealtimeClient } from './client';
import { blotterStore, connectionStore, intelligenceStore, liveStore, marketStore, predictionStore, replayStore, runtimeStore } from '../state/stores';
import type { BarDTO, DataQualityWarningDTO, DroppedPredictionDTO, Envelope, FeedStatusDTO, ModelStatusDTO, ObservationDTO, OutcomeDTO, PredictionDTO, SnapshotPayloadDTO, TouchDTO } from './types';
import { MAX_BARS_PER_TIMEFRAME } from '../chart/viewModels';
import { normalizeBar, normalizeWarning } from '../domain/normalize';

class FakeSocket {
  binaryType: BinaryType = 'blob';
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  close = vi.fn();

  open() {
    this.onopen?.(new Event('open'));
  }

  message(data: unknown) {
    this.onmessage?.({ data } as MessageEvent);
  }

  fail() {
    this.onerror?.(new Event('error'));
  }

  closed() {
    this.onclose?.(new CloseEvent('close'));
  }
}

const resetStores = () => {
  runtimeStore.reset();
  connectionStore.reset();
  marketStore.reset();
  intelligenceStore.reset();
  blotterStore.reset();
  replayStore.reset();
  liveStore.reset();
  predictionStore.reset();
};

const feedStatus = (state = 'connected'): FeedStatusDTO => ({
  state,
  mode: state === 'replaying' ? 'replay' : 'live',
  requested_symbol: 'NQ.c.0',
  raw_symbol: 'NQM6',
  dataset: 'GLBX.MDP3',
  schema: 'mbp-1',
  last_event_ts_utc: '2026-05-21T14:00:00Z',
  last_message: `Feed ${state}`,
  metadata: {},
});

const bar = (timeframe = 147, complete = false, overrides: Partial<BarDTO> = {}): BarDTO => {
  const tradingDay = overrides.trading_day ?? '2026-05-21';
  const openTs = overrides.open_ts_utc ?? '2026-05-21T14:00:00Z';
  const baseTs = Date.parse(`${tradingDay}T14:00:00Z`);
  const barIndex = overrides.bar_index ?? Math.max(0, Math.floor((Date.parse(openTs) - baseTs) / 1000));
  return {
    timeframe_ticks: timeframe,
    trading_day: tradingDay,
    bar_index: barIndex,
    bar_id: overrides.bar_id ?? `${timeframe}t:${tradingDay}:${barIndex}`,
    open_ts_utc: openTs,
    close_ts_utc: '2026-05-21T14:01:00Z',
    open_ticks: 76000,
    high_ticks: 76012,
    low_ticks: 75996,
    close_ticks: 76008,
    volume: 100,
    trade_count: 40,
    is_complete: complete,
    is_partial: !complete,
    close_reason: complete ? 'tick_count' : null,
    ...overrides,
  };
};

const warning = (index = 0): DataQualityWarningDTO => ({
  code: `gap-${index}`,
  message: `Gap ${index}`,
  severity: index % 2 === 0 ? 'warning' : 'error',
  source: 'replay',
  event_ts_utc: `2026-05-21T14:${String(index).padStart(2, '0')}:00Z`,
  metadata: {},
});

const touch: TouchDTO = {
  touch_id: 'touch-1',
  event_ts_utc: '2026-05-21T14:02:00Z',
  trading_day: '2026-05-21',
  session: 'ny',
  level_kind: 'pdh',
  level_price_ticks: 76000,
  trade_price_ticks: 76001,
  requested_symbol: 'NQ.c.0',
  raw_symbol: 'NQM6',
  instrument_id: 123,
  created_observation: true,
  sequence_in_session: 1,
};

const observation: ObservationDTO = {
  observation_id: 'obs-1',
  originating_touch_id: 'touch-1',
  start_ts_utc: '2026-05-21T14:02:00Z',
  scheduled_end_ts_utc: '2026-05-21T14:17:00Z',
  status: 'active',
  trading_day: '2026-05-21',
  session: 'ny',
  level_kind: 'pdh',
  level_price_ticks: 76000,
};

const prediction = (overrides: Partial<PredictionDTO> = {}): PredictionDTO => ({
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

const outcome = (overrides: Partial<OutcomeDTO> = {}): OutcomeDTO => ({
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
  entry_price: 19000.25,
  ...overrides,
});

const droppedDto = (overrides: Partial<DroppedPredictionDTO> = {}): DroppedPredictionDTO => ({
  prediction_id: 'pred-1',
  touch_id: 'touch-1',
  reason: 'flatten',
  decision_ts_utc: '2026-05-21T20:41:00Z',
  entry_price: null,
  ...overrides,
});

const modelStatus = (overrides: Partial<ModelStatusDTO> = {}): ModelStatusDTO => ({
  loaded: true,
  model_id: 'model-a',
  strategy_id: 'strat-1',
  training_mode: 'offline',
  instrument: 'NQ',
  feature_names: ['f0', 'f1'],
  class_map: { '0': 'continuation', '1': 'reversal' },
  validation_ok: true,
  validation_detail: 'ok',
  ...overrides,
});

let sequence = 1;
const envelope = <T,>(type: Envelope<T>['type'], payload: T): string =>
  JSON.stringify({ version: 'ws.v1', type, sequence: sequence++, server_time_utc: '2026-05-21T14:03:00Z', payload });

const rawEnvelope = (type: string, payload: unknown): string =>
  JSON.stringify({ version: 'ws.v1', type, sequence: sequence++, server_time_utc: '2026-05-21T14:03:00Z', payload });

const encode = (value: string): ArrayBuffer => new TextEncoder().encode(value).buffer;

describe('RealtimeClient', () => {
  let sockets: FakeSocket[];
  let client: RealtimeClient;

  beforeEach(() => {
    resetStores();
    sequence = 1;
    sockets = [];
    client = new RealtimeClient('ws://localhost:8001/ws/v1', (url) => {
      expect(url).toBe('ws://localhost:8001/ws/v1');
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket as unknown as WebSocket;
    });
  });

  afterEach(() => {
    client.stop();
    resetStores();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('parses versioned snapshots into runtime, chart, and intelligence state', () => {
    const snapshot: SnapshotPayloadDTO = {
      current_bars: [bar(147, false)],
      recent_closed_bars: [bar(987, true)],
      display_levels: [
        { kind: 'pdh', price_ticks: 76000, trading_day: '2026-05-21', origin_session: 'ny', is_developing: false, is_eligible: true },
        { kind: 'asia_high', price_ticks: 76100, trading_day: '2026-05-21', origin_session: 'asia', is_developing: true, is_eligible: false },
      ],
      active_observations: [observation],
      feed_status: feedStatus('connected'),
      warnings: [warning(0)],
      predictions: [prediction()],
      outcomes: [outcome()],
      model_status: modelStatus(),
      session: 'ny',
      trading_day: '2026-05-21',
    };

    client.start();
    expect(sockets[0].binaryType).toBe('arraybuffer');
    sockets[0].message(envelope('system.snapshot', snapshot));

    expect(connectionStore.getSnapshot()).toMatchObject({ lastSequence: 1, lastServerTimeUtc: '2026-05-21T14:03:00Z' });
    expect(runtimeStore.getSnapshot()).toMatchObject({ runtimeMode: 'live', requestedSymbol: 'NQ.c.0', feedReady: true, feedState: 'connected', session: 'ny', tradingDay: '2026-05-21' });
    expect(marketStore.getSnapshot().currentBars[0]).toMatchObject({ timeframe: 147, complete: false });
    expect(marketStore.getSnapshot().recentClosedBars[0]).toMatchObject({ timeframe: 987, complete: true });
    expect(intelligenceStore.getSnapshot().levels).toEqual([
      expect.objectContaining({ kind: 'pdh', eligible: true, developing: false }),
      expect.objectContaining({ kind: 'asia_high', eligible: false, developing: true }),
    ]);
    expect(intelligenceStore.getSnapshot().observations[0]).toMatchObject({ id: 'obs-1', status: 'active' });
    expect(intelligenceStore.getSnapshot().warnings[0]).toMatchObject({ code: 'gap-0' });
    expect(predictionStore.getSnapshot().predictions[0]).toMatchObject({ id: 'pred-1', predictedClass: 'continuation', outcome: expect.objectContaining({ id: 'out-1', correct: true }) });
    expect(predictionStore.getSnapshot().outcomes[0]).toMatchObject({ id: 'out-1', predictionId: 'pred-1' });
    expect(predictionStore.getSnapshot().modelStatus).toMatchObject({ loaded: true, modelId: 'model-a' });
    // Feature vectors stay at the transport boundary and never reach the store.
    expect(JSON.stringify(predictionStore.getSnapshot().predictions[0])).not.toContain('feature_values');
  });

  it('handles heartbeat, feed status, warnings, bars, levels, touches, and observations', () => {
    client.start();
    sockets[0].message(envelope('system.heartbeat', {}));
    sockets[0].message(envelope('feed.status', feedStatus('replaying')));
    sockets[0].message(envelope('data_quality.warning', warning(1)));
    sockets[0].message(envelope('market.bar.updated', { bars: [bar(147, false)] }));
    sockets[0].message(envelope('market.bar.closed', { bars: [bar(147, true)] }));
    sockets[0].message(envelope('levels.updated', { levels: [{ kind: 'pdl', price_ticks: 75900, trading_day: '2026-05-21', origin_session: 'ny', is_developing: false, is_eligible: true }] }));
    sockets[0].message(envelope('touch.detected', touch));
    sockets[0].message(envelope('observation.updated', observation));

    expect(connectionStore.getSnapshot()).toMatchObject({ lastSequence: 8, lastHeartbeatUtc: '2026-05-21T14:03:00Z' });
    expect(runtimeStore.getSnapshot()).toMatchObject({ runtimeMode: 'replay', feedReady: true, feedState: 'replaying' });
    expect(replayStore.getSnapshot().status).toMatchObject({ state: 'running', lastEventUtc: '2026-05-21T14:00:00Z' });
    expect(intelligenceStore.getSnapshot().warnings[0]).toMatchObject({ code: 'gap-1', severity: 'error' });
    expect(marketStore.getSnapshot().currentBars).toHaveLength(0);
    expect(marketStore.getSnapshot().recentClosedBars[0]).toMatchObject({ timeframe: 147, complete: true });
    expect(intelligenceStore.getSnapshot().levels[0]).toMatchObject({ kind: 'pdl', eligible: true });
    expect(intelligenceStore.getSnapshot().touches[0]).toMatchObject({ id: 'touch-1', createdObservation: true });
    expect(intelligenceStore.getSnapshot().observations[0]).toMatchObject({ id: 'obs-1', levelKind: 'pdh' });
  });

  it('routes prediction.created, prediction.resolved, and model.status deltas', () => {
    client.start();

    sockets[0].message(envelope('prediction.created', { prediction: prediction() }));
    expect(predictionStore.getSnapshot().predictions[0]).toMatchObject({ id: 'pred-1', predictedClass: 'continuation', outcome: null });

    sockets[0].message(envelope('prediction.resolved', { outcome: outcome() }));
    expect(predictionStore.getSnapshot().outcomes[0]).toMatchObject({ id: 'out-1', predictionId: 'pred-1', correct: true });
    expect(predictionStore.getSnapshot().predictions[0]).toMatchObject({ id: 'pred-1', outcome: expect.objectContaining({ id: 'out-1' }) });

    sockets[0].message(envelope('model.status', modelStatus({ model_id: 'model-b', loaded: true })));
    expect(predictionStore.getSnapshot().modelStatus).toMatchObject({ loaded: true, modelId: 'model-b' });

    expect(blotterStore.getSnapshot().events.some((event) => event.message === 'Prediction created')).toBe(true);
    expect(blotterStore.getSnapshot().events.some((event) => event.message === 'Prediction resolved')).toBe(true);
    expect(blotterStore.getSnapshot().events.some((event) => event.message === 'Model status updated')).toBe(true);
  });

  it('routes prediction.dropped deltas into the dropped ring and annotates the prediction', () => {
    client.start();

    sockets[0].message(envelope('prediction.created', { prediction: prediction() }));
    sockets[0].message(envelope('prediction.dropped', { dropped: droppedDto() }));

    expect(predictionStore.getSnapshot().dropped[0]).toMatchObject({ predictionId: 'pred-1', reason: 'flatten', entryPrice: null });
    expect(predictionStore.getSnapshot().predictions[0]).toMatchObject({ id: 'pred-1', outcome: null, dropped: expect.objectContaining({ reason: 'flatten' }) });
    expect(predictionStore.getSnapshot().outcomes).toHaveLength(0);
    expect(blotterStore.getSnapshot().events.some((event) => event.message === 'Prediction dropped (flatten)')).toBe(true);
  });

  it('bounds recent predictions and outcomes to the newest entries', () => {
    client.start();

    for (let index = 0; index < 105; index += 1) {
      sockets[0].message(envelope('prediction.created', { prediction: prediction({ prediction_id: `pred-${index}` }) }));
    }

    expect(predictionStore.getSnapshot().predictions).toHaveLength(100);
    expect(predictionStore.getSnapshot().predictions[0]).toMatchObject({ id: 'pred-104' });
    expect(predictionStore.getSnapshot().predictions.at(-1)).toMatchObject({ id: 'pred-5' });
  });

  it('clears predictions and outcomes when a typed model.reset frame arrives', () => {
    client.start();
    sockets[0].message(envelope('prediction.created', { prediction: prediction() }));
    sockets[0].message(envelope('prediction.resolved', { outcome: outcome() }));
    expect(predictionStore.getSnapshot().predictions).toHaveLength(1);

    sockets[0].message(envelope('model.reset', { reason: 'activation' }));

    expect(predictionStore.getSnapshot().predictions).toHaveLength(0);
    expect(predictionStore.getSnapshot().outcomes).toHaveLength(0);
    expect(predictionStore.getSnapshot().dropped).toHaveLength(0);
  });

  it('converts provider warning metadata into blotter code, source, and safe details only', () => {
    client.start();

    sockets[0].message(envelope('data_quality.warning', {
      code: 'provider_error',
      message: 'Databento provider reported an error',
      severity: 'warning',
      source: 'databento',
      event_ts_utc: null,
      metadata: {
        schema: 'mbp-1',
        detail: 'code=bad_request; message=<redacted> path=<path>',
        dropped: 1,
        token: 'db-secret',
        raw_record: { api_key: 'db-secret' },
      },
    }));

    expect(blotterStore.getSnapshot().events[0]).toMatchObject({
      category: 'warning',
      severity: 'warning',
      code: 'provider_error',
      source: 'databento',
      details: { schema: 'mbp-1', detail: 'code=bad_request; message=<redacted> path=<path>', dropped: 1 },
    });
    expect(JSON.stringify(blotterStore.getSnapshot().events[0])).not.toContain('db-secret');
    expect(JSON.stringify(blotterStore.getSnapshot().events[0])).not.toContain('raw_record');
  });

  it('maps replay feed status lifecycle messages into replay store state', () => {
    client.start();

    sockets[0].message(envelope('feed.status', { ...feedStatus('replaying'), last_message: 'historical replay paused' }));
    expect(replayStore.getSnapshot().status.state).toBe('paused');

    sockets[0].message(envelope('feed.status', { ...feedStatus('replaying'), last_message: 'historical replay resumed' }));
    expect(replayStore.getSnapshot().status.state).toBe('running');

    sockets[0].message(envelope('feed.status', { ...feedStatus('disconnected'), mode: 'replay', last_message: 'historical replay completed' }));
    expect(replayStore.getSnapshot().status.state).toBe('completed');

    sockets[0].message(envelope('feed.status', { ...feedStatus('disconnected'), mode: 'replay', last_message: 'historical replay failed' }));
    expect(replayStore.getSnapshot().status.state).toBe('failed');
  });

  it('maps live feed status messages without disrupting replay or chart state', () => {
    marketStore.setState({ currentBars: [normalizeBar(bar(147, false))], recentClosedBars: [normalizeBar(bar(147, true))] });
    replayStore.setState((current) => ({ ...current, status: { ...current.status, state: 'paused', lastEventUtc: '2026-05-21T13:59:00Z' } }));
    client.start();

    sockets[0].message(envelope('feed.status', { ...feedStatus('connected'), mode: 'live', schema: 'trades', metadata: { schemas: ['trades', 'mbp-1', 'definition'] }, last_message: 'live feed running' }));

    expect(runtimeStore.getSnapshot()).toMatchObject({ runtimeMode: 'live', feedReady: true, feedState: 'connected' });
    expect(liveStore.getSnapshot().status).toMatchObject({ state: 'running', requestedSymbol: 'NQ.c.0', dataset: 'GLBX.MDP3', schemas: ['trades', 'mbp-1', 'definition'], lastEventUtc: '2026-05-21T14:00:00Z' });
    expect(replayStore.getSnapshot().status).toMatchObject({ state: 'paused', lastEventUtc: '2026-05-21T13:59:00Z' });
    expect(marketStore.getSnapshot().currentBars).toHaveLength(1);
    expect(marketStore.getSnapshot().recentClosedBars).toHaveLength(1);
  });

  it('clears stale chart and intelligence data when a live model.reset frame arrives', () => {
    marketStore.setState({ currentBars: [normalizeBar(bar(147, false))], recentClosedBars: [normalizeBar(bar(147, true))] });
    intelligenceStore.setState({
      levels: [{ kind: 'pdh', priceTicks: 76000, tradingDay: '2026-05-21', originSession: 'ny', developing: false, eligible: true }],
      touches: [{ id: 'touch-1', timeUtc: '2026-05-21T14:02:00Z', session: 'ny', levelKind: 'pdh', priceTicks: 76000, createdObservation: true }],
      observations: [{ id: 'obs-1', status: 'active', session: 'ny', levelKind: 'pdh', startUtc: '2026-05-21T14:02:00Z', scheduledEndUtc: '2026-05-21T14:17:00Z' }],
      warnings: [normalizeWarning(warning(1))],
    });
    client.start();

    sockets[0].message(envelope('model.reset', { reason: 'live_reset' }));

    expect(marketStore.getSnapshot()).toMatchObject({ currentBars: [], recentClosedBars: [] });
    expect(intelligenceStore.getSnapshot()).toMatchObject({ levels: [], touches: [], observations: [] });
    expect(intelligenceStore.getSnapshot().warnings).toHaveLength(1);
  });

  it('clears stale chart and intelligence data when a replay model.reset frame arrives', () => {
    marketStore.setState({ currentBars: [normalizeBar(bar(147, false))], recentClosedBars: [normalizeBar(bar(147, true))] });
    intelligenceStore.setState({
      levels: [{ kind: 'pdh', priceTicks: 76000, tradingDay: '2026-05-21', originSession: 'ny', developing: false, eligible: true }],
      touches: [{ id: 'touch-1', timeUtc: '2026-05-21T14:02:00Z', session: 'ny', levelKind: 'pdh', priceTicks: 76000, createdObservation: true }],
      observations: [{ id: 'obs-1', status: 'active', session: 'ny', levelKind: 'pdh', startUtc: '2026-05-21T14:02:00Z', scheduledEndUtc: '2026-05-21T14:17:00Z' }],
      warnings: [normalizeWarning(warning(1))],
    });
    client.start();

    sockets[0].message(envelope('model.reset', { reason: 'replay_reset' }));

    expect(marketStore.getSnapshot()).toMatchObject({ currentBars: [], recentClosedBars: [] });
    expect(intelligenceStore.getSnapshot()).toMatchObject({ levels: [], touches: [], observations: [] });
    expect(intelligenceStore.getSnapshot().warnings).toHaveLength(1);
  });

  it('market.bar.updated replaces the in-progress candle by stable bar key', () => {
    client.start();
    sockets[0].message(envelope('market.bar.updated', { bars: [bar(147, false, { close_ticks: 76004 })] }));
    sockets[0].message(envelope('market.bar.updated', { bars: [bar(147, false, { close_ticks: 76012, volume: 140 })] }));

    expect(marketStore.getSnapshot().currentBars).toHaveLength(1);
    expect(marketStore.getSnapshot().currentBars[0]).toMatchObject({ closeTicks: 76012, volume: 140, complete: false });
  });

  it('market.bar.closed purges the matching in-progress current bar', () => {
    client.start();
    sockets[0].message(envelope('market.bar.updated', { bars: [bar(147, false, { close_ticks: 76004 })] }));
    expect(marketStore.getSnapshot().currentBars).toHaveLength(1);

    sockets[0].message(envelope('market.bar.closed', { bars: [bar(147, true, { close_ticks: 76008 })] }));

    expect(marketStore.getSnapshot().currentBars).toHaveLength(0);
    expect(marketStore.getSnapshot().recentClosedBars[0]).toMatchObject({ closeTicks: 76008, complete: true });
  });

  it('repeated partials for the same bar key do not grow currentBars', () => {
    client.start();

    for (let index = 0; index < 5; index += 1) {
      sockets[0].message(envelope('market.bar.updated', { bars: [bar(147, false, { close_ticks: 76000 + index, volume: 100 + index })] }));
    }

    expect(marketStore.getSnapshot().currentBars).toHaveLength(1);
    expect(marketStore.getSnapshot().currentBars[0]).toMatchObject({ closeTicks: 76004, volume: 104 });
  });

  it('keeps currentBars bounded to the latest incomplete bar per supported timeframe', () => {
    client.start();
    const baseTime = Date.parse('2026-05-21T14:00:00Z');
    const partials = Array.from({ length: 8 }, (_, index) => bar(147, false, {
      open_ts_utc: new Date(baseTime + index * 1000).toISOString(),
      close_ts_utc: new Date(baseTime + index * 1000 + 500).toISOString(),
      close_ticks: 76000 + index,
    }));

    sockets[0].message(envelope('market.bar.updated', { bars: [...partials, bar(987, false, { close_ticks: 77000 }), bar(2000, false, { close_ticks: 78000 })] }));

    const currentBars = marketStore.getSnapshot().currentBars;
    expect(currentBars).toHaveLength(3);
    expect(currentBars.filter((entry) => entry.timeframe === 147)).toHaveLength(1);
    expect(currentBars.find((entry) => entry.timeframe === 147)).toMatchObject({ openTimeUtc: partials.at(-1)?.open_ts_utc, closeTicks: 76007 });
  });

  it('market.bar.closed appends closed candles and dedupes repeated closes', () => {
    client.start();
    sockets[0].message(envelope('market.bar.closed', { bars: [bar(147, true, { close_ticks: 76004 })] }));
    sockets[0].message(envelope('market.bar.closed', { bars: [bar(147, true, { close_ticks: 76008 })] }));
    sockets[0].message(envelope('market.bar.closed', { bars: [bar(147, true, { open_ts_utc: '2026-05-21T14:01:00Z', close_ts_utc: '2026-05-21T14:02:00Z', close_ticks: 76016 })] }));

    expect(marketStore.getSnapshot().recentClosedBars).toHaveLength(2);
    expect(marketStore.getSnapshot().recentClosedBars.map((entry) => entry.closeTicks)).toEqual([76008, 76016]);
  });

  it('retains non-selected timeframe bars and bounds closed-bar history per timeframe', () => {
    client.start();
    const baseTime = Date.parse('2026-05-21T14:00:00Z');
    const many147Bars = Array.from({ length: MAX_BARS_PER_TIMEFRAME + 3 }, (_, index) => bar(147, true, {
      open_ts_utc: new Date(baseTime + index * 1000).toISOString(),
      close_ts_utc: new Date(baseTime + index * 1000 + 500).toISOString(),
      close_ticks: 76000 + index,
    }));

    sockets[0].message(envelope('market.bar.closed', { bars: [...many147Bars, bar(987, true, { close_ticks: 77000 })] }));

    const closedBars = marketStore.getSnapshot().recentClosedBars;
    expect(closedBars.filter((entry) => entry.timeframe === 147)).toHaveLength(MAX_BARS_PER_TIMEFRAME);
    expect(closedBars.filter((entry) => entry.timeframe === 987)).toHaveLength(1);
    expect(closedBars.find((entry) => entry.timeframe === 147)?.openTimeUtc).toBe(many147Bars[3].open_ts_utc);
    expect(closedBars.at(-1)).toMatchObject({ timeframe: 987, closeTicks: 77000 });
  });

  it('ignores malformed messages and unexpected versions without crashing', () => {
    client.start();

    expect(() => sockets[0].message('{not-json')).not.toThrow();
    expect(() => sockets[0].message(JSON.stringify({ version: 'ws.v1', type: 'system.heartbeat' }))).not.toThrow();
    expect(() => sockets[0].message(JSON.stringify({ version: 'ws.v2', type: 'system.heartbeat', sequence: 10, server_time_utc: '2026-05-21T14:00:00Z', payload: {} }))).not.toThrow();

    expect(connectionStore.getSnapshot()).toMatchObject({ lastSequence: 10, lastHeartbeatUtc: '2026-05-21T14:00:00Z' });
    expect(blotterStore.getSnapshot().events.some((event) => event.message.includes('Unexpected WS version ws.v2'))).toBe(true);
    expect(blotterStore.getSnapshot().events.some((event) => event.severity === 'warning')).toBe(true);
  });

  it('handles ArrayBuffer WebSocket payloads carrying valid envelopes', async () => {
    client.start();

    sockets[0].message(encode(envelope('system.heartbeat', {})));

    await vi.waitFor(() => expect(connectionStore.getSnapshot()).toMatchObject({ lastSequence: 1, lastHeartbeatUtc: '2026-05-21T14:03:00Z' }));
    expect(blotterStore.getSnapshot().events[0]).toMatchObject({ message: 'Heartbeat' });
  });

  it('handles Blob WebSocket payloads carrying valid envelopes', async () => {
    client.start();

    sockets[0].message(new Blob([envelope('system.heartbeat', {})], { type: 'application/json' }));

    await vi.waitFor(() => expect(connectionStore.getSnapshot()).toMatchObject({ lastSequence: 1, lastHeartbeatUtc: '2026-05-21T14:03:00Z' }));
    expect(blotterStore.getSnapshot().events[0]).toMatchObject({ message: 'Heartbeat' });
  });

  it('records malformed binary payloads without crashing', async () => {
    client.start();

    expect(() => sockets[0].message(encode('{not-json'))).not.toThrow();
    expect(() => sockets[0].message(new Uint8Array([0xff, 0xfe, 0xfd]).buffer)).not.toThrow();

    await vi.waitFor(() => expect(blotterStore.getSnapshot().events.some((event) => event.severity === 'warning')).toBe(true));
    expect(connectionStore.getSnapshot().lastSequence).toBeNull();
  });

  it('records unhandled WebSocket message types as warnings', () => {
    client.start();

    sockets[0].message(rawEnvelope('strategy.future', { enabled: true }));

    expect(connectionStore.getSnapshot()).toMatchObject({ lastSequence: 1, lastServerTimeUtc: '2026-05-21T14:03:00Z' });
    expect(blotterStore.getSnapshot().events[0]).toMatchObject({ severity: 'warning', message: 'Unhandled WS message type: strategy.future' });
  });

  it('bounds warning and event retention while preserving newest entries', () => {
    client.start();

    for (let index = 0; index < 105; index += 1) {
      sockets[0].message(envelope('data_quality.warning', warning(index)));
    }

    expect(intelligenceStore.getSnapshot().warnings).toHaveLength(100);
    expect(intelligenceStore.getSnapshot().warnings[0]).toMatchObject({ code: 'gap-104' });
    expect(intelligenceStore.getSnapshot().warnings.at(-1)).toMatchObject({ code: 'gap-5' });
    expect(blotterStore.getSnapshot().events.length).toBeLessThanOrEqual(200);
  });

  it('uses bounded reconnect backoff with fake timers', () => {
    vi.useFakeTimers();
    const setTimeoutSpy = vi.spyOn(window, 'setTimeout');

    client.start();
    sockets[0].closed();

    expect(connectionStore.getSnapshot()).toMatchObject({ wsStatus: 'reconnecting', reconnectAttempt: 1, error: 'Reconnecting in 1s' });
    expect(setTimeoutSpy).toHaveBeenLastCalledWith(expect.any(Function), 750);

    for (let index = 0; index < 8; index += 1) {
      vi.runOnlyPendingTimers();
      sockets.at(-1)?.closed();
    }

    const scheduledDelays = setTimeoutSpy.mock.calls.map(([, delay]) => delay as number);
    expect(Math.max(...scheduledDelays)).toBe(15_000);
    expect(connectionStore.getSnapshot().reconnectAttempt).toBe(9);
  });

  it('cleanup closes the socket and clears owned timers/listeners', () => {
    vi.useFakeTimers();
    const clearTimeoutSpy = vi.spyOn(window, 'clearTimeout');

    client.start();
    const socket = sockets[0];
    socket.open();
    client.stop();

    expect(socket.close).toHaveBeenCalledOnce();
    expect(socket.onopen).toBeNull();
    expect(socket.onmessage).toBeNull();
    expect(socket.onerror).toBeNull();
    expect(socket.onclose).toBeNull();
    expect(connectionStore.getSnapshot().wsStatus).toBe('offline');

    client.start();
    sockets[1].closed();
    client.stop();
    expect(clearTimeoutSpy).toHaveBeenCalled();
  });
});
