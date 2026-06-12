import { config } from '../config';
import { MAX_BARS_PER_TIMEFRAME, barKey, isSupportedTimeframe } from '../chart/viewModels';
import { addBlotterEvent, addDropped, addOutcome, addPrediction, clearPredictions, connectionStore, intelligenceStore, liveStore, marketStore, predictionStore, replayStore, runtimeStore, setModelStatus } from '../state/stores';
import { normalizeBar, normalizeDropped, normalizeLevel, normalizeModelStatus, normalizeObservation, normalizeOutcome, normalizePrediction, normalizeTouch, normalizeWarning } from '../domain/normalize';
import type { MarketBar } from '../domain/models';
import type { BarDTO, DataQualityWarningDTO, DisplayLevelDTO, DroppedPredictionDTO, Envelope, FeedStatusDTO, ModelStatusDTO, ObservationDTO, OutcomeDTO, PredictionDTO, SnapshotPayloadDTO, TouchDTO } from './types';

type WebSocketFactory = (url: string) => WebSocket;

const MAX_BACKOFF_MS = 15_000;
const BASE_BACKOFF_MS = 750;

export class RealtimeClient {
  private socket: WebSocket | null = null;
  private reconnectTimer: number | null = null;
  private stopped = true;
  private attempt = 0;

  constructor(
    private readonly wsUrl = config.wsUrl,
    private readonly socketFactory: WebSocketFactory = (url) => new WebSocket(url),
  ) {}

  start() {
    if (!this.stopped) return;
    this.stopped = false;
    this.connect('connecting');
  }

  stop() {
    this.stopped = true;
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    if (this.socket) {
      this.socket.onopen = null;
      this.socket.onmessage = null;
      this.socket.onerror = null;
      this.socket.onclose = null;
      this.socket.close();
    }
    this.socket = null;
    connectionStore.setState({ wsStatus: 'offline' });
  }

  private connect(status: 'connecting' | 'reconnecting') {
    connectionStore.setState({ wsStatus: status, reconnectAttempt: this.attempt, error: null });
    const socket = this.socketFactory(this.wsUrl);
    socket.binaryType = 'arraybuffer';
    this.socket = socket;
    socket.onopen = () => {
      this.attempt = 0;
      connectionStore.setState({ wsStatus: 'connected', reconnectAttempt: 0, error: null });
      addBlotterEvent({ timeUtc: new Date().toISOString(), category: 'system', severity: 'info', message: 'WebSocket connected' });
    };
    socket.onmessage = (event) => {
      void this.handleMessage(event.data);
    };
    socket.onerror = () => connectionStore.setState({ error: 'WebSocket transport error' });
    socket.onclose = () => {
      if (this.socket === socket) this.socket = null;
      if (!this.stopped) this.scheduleReconnect();
    };
  }

  private scheduleReconnect() {
    this.attempt += 1;
    const delay = Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * 2 ** Math.min(this.attempt - 1, 5));
    connectionStore.setState({ wsStatus: 'reconnecting', reconnectAttempt: this.attempt, error: `Reconnecting in ${Math.round(delay / 1000)}s` });
    // Bounded backoff and one owned timer prevent leaked listeners/timers during offline operation.
    this.reconnectTimer = window.setTimeout(() => this.connect('reconnecting'), delay);
  }

  private async handleMessage(data: unknown) {
    try {
      const decoded = decodeMessageData(data);
      const text = typeof decoded === 'string' ? decoded : await decoded;
      const envelope = JSON.parse(text) as Envelope;
      if (!isEnvelope(envelope)) throw new Error('Invalid WebSocket envelope');
      // Versioned envelopes make contract drift visible instead of silently mixing schemas.
      if (envelope.version !== 'ws.v1') {
        addBlotterEvent({ timeUtc: envelope.server_time_utc, category: 'warning', severity: 'warning', message: `Unexpected WS version ${envelope.version}`, sequence: envelope.sequence });
      }
      connectionStore.setState({ lastSequence: envelope.sequence, lastServerTimeUtc: envelope.server_time_utc });
      this.route(envelope);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Invalid WebSocket message';
      addBlotterEvent({ timeUtc: new Date().toISOString(), category: 'warning', severity: 'warning', message });
    }
  }

  private route(envelope: Envelope) {
    switch (envelope.type) {
      case 'system.snapshot':
        this.applySnapshot(envelope.payload as SnapshotPayloadDTO);
        addBlotterEvent({ timeUtc: envelope.server_time_utc, category: 'system', severity: 'info', message: 'Runtime snapshot received', sequence: envelope.sequence });
        break;
      case 'system.heartbeat':
        connectionStore.setState({ lastHeartbeatUtc: envelope.server_time_utc });
        addBlotterEvent({ timeUtc: envelope.server_time_utc, category: 'system', severity: 'info', message: 'Heartbeat', sequence: envelope.sequence });
        break;
      case 'feed.status':
        this.applyFeedStatus(envelope.payload as FeedStatusDTO);
        break;
      case 'data_quality.warning':
        this.applyWarning(envelope.payload as DataQualityWarningDTO, envelope.server_time_utc);
        break;
      case 'market.bar.updated':
        marketStore.setState((current) => ({ ...current, currentBars: mergeCurrentBars(current.currentBars, ((envelope.payload as { bars: BarDTO[] }).bars ?? []).map(normalizeBar)) }));
        break;
      case 'market.bar.closed':
        marketStore.setState((current) => {
          const closedBars = ((envelope.payload as { bars: BarDTO[] }).bars ?? []).map(normalizeBar);
          return {
            ...current,
            recentClosedBars: mergeBars(current.recentClosedBars, closedBars, true),
            currentBars: removeClosedCurrentBars(current.currentBars, closedBars),
          };
        });
        addBlotterEvent({ timeUtc: envelope.server_time_utc, category: 'market', severity: 'info', message: 'Tick bar closed', sequence: envelope.sequence });
        break;
      case 'levels.updated':
        intelligenceStore.setState({ levels: ((envelope.payload as { levels: DisplayLevelDTO[] }).levels ?? []).map(normalizeLevel) });
        addBlotterEvent({ timeUtc: envelope.server_time_utc, category: 'level', severity: 'info', message: 'Levels updated', sequence: envelope.sequence });
        break;
      case 'touch.detected':
        intelligenceStore.setState((current) => ({ ...current, touches: [normalizeTouch(envelope.payload as TouchDTO), ...current.touches].slice(0, 100) }));
        addBlotterEvent({ timeUtc: envelope.server_time_utc, category: 'touch', severity: 'info', message: 'Level touch detected', sequence: envelope.sequence });
        break;
      case 'observation.updated':
        intelligenceStore.setState((current) => ({ ...current, observations: upsertById(current.observations, normalizeObservation(envelope.payload as ObservationDTO), 'id') }));
        addBlotterEvent({ timeUtc: envelope.server_time_utc, category: 'observation', severity: 'info', message: 'Observation updated', sequence: envelope.sequence });
        break;
      case 'prediction.created':
        addPrediction(normalizePrediction((envelope.payload as { prediction: PredictionDTO }).prediction));
        addBlotterEvent({ timeUtc: envelope.server_time_utc, category: 'observation', severity: 'info', message: 'Prediction created', sequence: envelope.sequence });
        break;
      case 'prediction.resolved':
        addOutcome(normalizeOutcome((envelope.payload as { outcome: OutcomeDTO }).outcome));
        addBlotterEvent({ timeUtc: envelope.server_time_utc, category: 'observation', severity: 'info', message: 'Prediction resolved', sequence: envelope.sequence });
        break;
      case 'prediction.dropped': {
        const dropped = normalizeDropped((envelope.payload as { dropped: DroppedPredictionDTO }).dropped);
        addDropped(dropped);
        addBlotterEvent({ timeUtc: envelope.server_time_utc, category: 'observation', severity: 'info', message: `Prediction dropped (${dropped.reason})`, sequence: envelope.sequence });
        break;
      }
      case 'model.status':
        setModelStatus(normalizeModelStatus(envelope.payload as ModelStatusDTO));
        addBlotterEvent({ timeUtc: envelope.server_time_utc, category: 'system', severity: 'info', message: 'Model status updated', sequence: envelope.sequence });
        break;
      case 'model.reset': {
        // W2 P2c: typed reset frame replaces the 'runtime reset' feed-message
        // substring trigger. Activation swaps the model only, so prediction and
        // outcome panes clear; a runtime reset (replay/live) also clears the
        // chart and intelligence panes the old substring trigger cleared.
        const reason = (envelope.payload as { reason?: string }).reason ?? 'unknown';
        if (reason !== 'activation') {
          marketStore.setState({ currentBars: [], recentClosedBars: [] });
          intelligenceStore.setState({ levels: [], touches: [], observations: [] });
        }
        clearPredictions();
        addBlotterEvent({ timeUtc: envelope.server_time_utc, category: 'system', severity: 'info', message: `Model reset (${reason})`, sequence: envelope.sequence });
        break;
      }
      default:
        addBlotterEvent({ timeUtc: envelope.server_time_utc, category: 'warning', severity: 'warning', message: `Unhandled WS message type: ${envelope.type}`, sequence: envelope.sequence });
        break;
    }
  }

  private applySnapshot(payload: SnapshotPayloadDTO) {
    marketStore.setState({ currentBars: mergeCurrentBars([], payload.current_bars.map(normalizeBar)), recentClosedBars: mergeBars([], payload.recent_closed_bars.map(normalizeBar), true) });
    intelligenceStore.setState({
      levels: payload.display_levels.map(normalizeLevel),
      observations: payload.active_observations.map(normalizeObservation).slice(0, 100),
      warnings: payload.warnings.map(normalizeWarning).slice(0, 100),
      touches: [],
    });
    // Seed predictions/outcomes/drops newest-first and annotate each prediction with
    // any already-resolved outcome (or drop) so the snapshot matches the running
    // delta state. Drops never enter `prediction.outcome`, so no chart marker.
    const outcomes = (payload.outcomes ?? []).map(normalizeOutcome).slice(0, 100);
    const dropped = (payload.dropped ?? []).map(normalizeDropped).slice(0, 100);
    const outcomeByPrediction = new Map(outcomes.map((outcome) => [outcome.predictionId, outcome]));
    const droppedByPrediction = new Map(dropped.map((drop) => [drop.predictionId, drop]));
    const predictions = (payload.predictions ?? []).map((dto) => {
      const prediction = normalizePrediction(dto);
      return {
        ...prediction,
        outcome: outcomeByPrediction.get(prediction.id) ?? null,
        dropped: droppedByPrediction.get(prediction.id) ?? null,
      };
    }).slice(0, 100);
    predictionStore.setState((current) => ({
      ...current,
      predictions,
      outcomes,
      dropped,
      modelStatus: payload.model_status ? normalizeModelStatus(payload.model_status) : current.modelStatus,
    }));
    runtimeStore.setState((current) => ({ ...current, session: payload.session ?? null, tradingDay: payload.trading_day ?? null }));
    this.applyFeedStatus(payload.feed_status);
  }

  private applyFeedStatus(status: FeedStatusDTO) {
    const replayState = replayStateFromFeedMessage(status.last_message, status.state, runtimeStore.getSnapshot().replayState);
    runtimeStore.setState((current) => ({ ...current, runtimeMode: status.mode, requestedSymbol: status.requested_symbol ?? current.requestedSymbol, feedReady: ['connected', 'replaying'].includes(status.state), feedState: status.state, replayState: status.mode === 'replay' ? replayState : current.replayState }));
    if (status.mode === 'replay') {
      replayStore.setState((current) => ({ ...current, status: { ...current.status, state: replayState, lastEventUtc: status.last_event_ts_utc ?? current.status.lastEventUtc } }));
    }
    if (status.mode === 'live') {
      liveStore.setState((current) => ({
        ...current,
        status: {
          ...current.status,
          state: liveStateFromFeedStatus(status.state, status.last_message, current.status.state),
          requestedSymbol: status.requested_symbol ?? current.status.requestedSymbol,
          dataset: status.dataset ?? current.status.dataset,
          schemas: Array.isArray(status.metadata.schemas) ? status.metadata.schemas.filter((schema): schema is string => typeof schema === 'string') : current.status.schemas,
          lastEventUtc: status.last_event_ts_utc ?? current.status.lastEventUtc,
          lastError: (status.last_message ?? '').toLowerCase().includes('failed') ? status.last_message : current.status.lastError,
        },
      }));
    }
    addBlotterEvent({ timeUtc: status.last_event_ts_utc ?? new Date().toISOString(), category: 'feed', severity: status.state === 'disconnected' ? 'warning' : 'info', message: status.last_message ?? `Feed ${status.state}` });
  }

  private applyWarning(warningDto: DataQualityWarningDTO, serverTimeUtc: string) {
    const warning = normalizeWarning(warningDto);
    intelligenceStore.setState((current) => ({ ...current, warnings: [warning, ...current.warnings].slice(0, 100) }));
    addBlotterEvent({
      timeUtc: warning.timeUtc ?? serverTimeUtc,
      category: 'warning',
      severity: warning.severity === 'error' ? 'error' : 'warning',
      message: warning.message,
      code: warning.code,
      source: warning.source,
      details: warning.metadata,
    });
  }
}

const isEnvelope = (value: unknown): value is Envelope => {
  if (!value || typeof value !== 'object') return false;
  const record = value as Record<string, unknown>;
  return typeof record.version === 'string' && typeof record.type === 'string' && typeof record.sequence === 'number' && typeof record.server_time_utc === 'string' && 'payload' in record;
};

const decodeMessageData = (data: unknown): string | Promise<string> => {
  if (typeof data === 'string') return data;
  if (isArrayBufferLike(data)) return new TextDecoder().decode(data);
  if (ArrayBuffer.isView(data)) return new TextDecoder().decode(data);
  if (isBlobLike(data)) {
    if (typeof data.text === 'function') return data.text();
    if (typeof data.arrayBuffer === 'function') return data.arrayBuffer().then((buffer) => new TextDecoder().decode(buffer));
    return readBlobWithFileReader(data);
  }
  throw new Error('Unsupported WebSocket message payload');
};

const isArrayBufferLike = (data: unknown): data is ArrayBuffer =>
  data instanceof ArrayBuffer || Object.prototype.toString.call(data) === '[object ArrayBuffer]';

const isBlobLike = (data: unknown): data is Blob => {
  if (!data || typeof data !== 'object') return false;
  const candidate = data as { text?: unknown; arrayBuffer?: unknown };
  return typeof candidate.text === 'function' || typeof candidate.arrayBuffer === 'function' || Object.prototype.toString.call(data) === '[object Blob]';
};

const readBlobWithFileReader = (blob: Blob): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('Unable to read Blob WebSocket payload'));
    reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : new TextDecoder().decode(reader.result as ArrayBuffer));
    reader.readAsText(blob);
  });

const upsertById = <T extends Record<K, string>, K extends keyof T>(items: T[], item: T, key: K): T[] => {
  const without = items.filter((candidate) => candidate[key] !== item[key]);
  return [item, ...without].slice(0, 100);
};

const mergeBars = (existing: MarketBar[], incoming: MarketBar[], boundHistory: boolean): MarketBar[] => {
  const byKey = new Map<string, MarketBar>();
  for (const bar of existing) if (isSupportedTimeframe(Number(bar.timeframe))) byKey.set(barKey(bar), bar);
  for (const bar of incoming) if (isSupportedTimeframe(Number(bar.timeframe))) byKey.set(barKey(bar), bar);
  const sorted = [...byKey.values()].sort(compareMarketBars);
  if (!boundHistory) return sorted;
  const perTimeframe = new Map<number, MarketBar[]>();
  for (const bar of sorted) perTimeframe.set(Number(bar.timeframe), [...(perTimeframe.get(Number(bar.timeframe)) ?? []), bar]);
  return [...perTimeframe.values()].flatMap((bars) => bars.slice(-MAX_BARS_PER_TIMEFRAME));
};

const mergeCurrentBars = (existing: MarketBar[], incoming: MarketBar[]): MarketBar[] => {
  const byKey = new Map<string, MarketBar>();
  for (const bar of existing) if (isActiveCurrentBar(bar)) byKey.set(barKey(bar), bar);
  for (const bar of incoming) if (isActiveCurrentBar(bar)) byKey.set(barKey(bar), bar);

  const sorted = [...byKey.values()].sort(compareMarketBars);
  const perTimeframe = new Map<number, MarketBar[]>();
  for (const bar of sorted) perTimeframe.set(Number(bar.timeframe), [...(perTimeframe.get(Number(bar.timeframe)) ?? []), bar]);

  // At most one tick bar should be active per supported timeframe. Keeping only
  // the latest incomplete bar prevents stale partials from accumulating if a
  // close message is delayed or missed, while still de-duping repeated updates by
  // stable bar key.
  return [...perTimeframe.values()].flatMap((bars) => bars.slice(-1));
};

const removeClosedCurrentBars = (currentBars: MarketBar[], closedBars: MarketBar[]): MarketBar[] => {
  if (closedBars.length === 0) return currentBars;
  const closedKeys = new Set(closedBars.map(barKey));
  return currentBars.filter((bar) => !closedKeys.has(barKey(bar)) && isActiveCurrentBar(bar));
};

const isActiveCurrentBar = (bar: MarketBar) => isSupportedTimeframe(Number(bar.timeframe)) && !bar.complete;

const compareMarketBars = (a: MarketBar, b: MarketBar): number => {
  if (a.tradingDay !== b.tradingDay) return a.tradingDay.localeCompare(b.tradingDay);
  if (typeof a.barIndex === 'number' && typeof b.barIndex === 'number' && a.barIndex !== b.barIndex) return a.barIndex - b.barIndex;
  return new Date(a.openTimeUtc).getTime() - new Date(b.openTimeUtc).getTime();
};

export const realtimeClient = new RealtimeClient();

const replayStateFromFeedMessage = (message: string | null, feedState: string, fallback: string): string => {
  const text = (message ?? '').toLowerCase();
  if (text.includes('paused')) return 'paused';
  if (text.includes('resumed') || feedState === 'replaying') return 'running';
  if (text.includes('completed')) return 'completed';
  if (text.includes('failed')) return 'failed';
  if (text.includes('stopped')) return 'stopped';
  if (text.includes('cancelled')) return 'cancelled';
  return fallback;
};

const liveStateFromFeedStatus = (feedState: string, message: string | null, fallback: string): string => {
  const text = (message ?? '').toLowerCase();
  if (text.includes('connecting')) return 'connecting';
  if (text.includes('running') || feedState === 'connected') return 'running';
  if (text.includes('failed')) return 'failed';
  if (text.includes('stopped')) return 'stopped';
  if (feedState === 'disconnected') return 'disconnected';
  return fallback;
};
