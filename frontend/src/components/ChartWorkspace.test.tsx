import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render } from '@testing-library/react';
import { ChartWorkspace } from './ChartWorkspace';
import { RealtimeClient } from '../realtime/client';
import { blotterStore, connectionStore, intelligenceStore, marketStore, predictionStore, runtimeStore } from '../state/stores';
import type { BarDTO, DisplayLevelDTO, Envelope, FeedStatusDTO, ObservationDTO, OutcomeDTO, PredictionDTO, TouchDTO } from '../realtime/types';
import type { TradingChart } from './TradingChart';

const chartMock = vi.hoisted(() => ({
  render: vi.fn(() => <div data-testid="mock-trading-chart" />),
}));

vi.mock('./TradingChart', () => ({
  TradingChart: chartMock.render,
}));

class FakeSocket {
  binaryType: BinaryType = 'blob';
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  close = vi.fn();

  message(data: unknown) {
    this.onmessage?.({ data } as MessageEvent);
  }
}

const resetStores = () => {
  runtimeStore.reset();
  connectionStore.reset();
  marketStore.reset();
  intelligenceStore.reset();
  blotterStore.reset();
  predictionStore.reset();
};

const bar = (timeframe = 147, complete = false, overrides: Partial<BarDTO> = {}): BarDTO => ({
  timeframe_ticks: timeframe,
  trading_day: '2026-05-21',
  open_ts_utc: '2026-05-21T14:00:00Z',
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
});

const level: DisplayLevelDTO = {
  kind: 'pdh',
  price_ticks: 76000,
  trading_day: '2026-05-21',
  origin_session: 'ny',
  is_developing: false,
  is_eligible: true,
};

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
  start_ts_utc: '2026-05-21T14:03:00Z',
  scheduled_end_ts_utc: '2026-05-21T14:18:00Z',
  status: 'active',
  trading_day: '2026-05-21',
  session: 'ny',
  level_kind: 'pdh',
  level_price_ticks: 76000,
};

const predictionDto: PredictionDTO = {
  prediction_id: 'pred-1',
  touch_id: 'touch-1',
  observation_id: 'obs-1',
  event_ts_utc: '2026-05-21T14:00:30Z',
  predicted_class: 'up',
  probabilities: { down: 0.1, hold: 0.3, up: 0.6 },
  feature_values: { dist_to_level: 1.2 },
  level_kind: 'pdh',
  level_price_ticks: 76000,
  direction: 'long',
  session: 'ny',
  is_eligible: true,
  model_id: 'model-alpha',
  contract_id: 'contract-1',
  nan_count: 0,
};

const outcomeDto: OutcomeDTO = {
  outcome_id: 'outcome-1',
  prediction_id: 'pred-1',
  touch_id: 'touch-1',
  resolution_type: 'mae_first',
  actual_class: 'up',
  predicted_class: 'up',
  correct: true,
  max_mfe_pts: 12.5,
  max_mae_pts: 3.25,
  bars_to_resolution: 8,
  resolved_ts_utc: '2026-05-21T14:05:00Z',
  entry_price: 19000.25,
};

const feedStatus = (state = 'connected'): FeedStatusDTO => ({
  state,
  mode: 'live',
  requested_symbol: 'NQ.c.0',
  raw_symbol: 'NQM6',
  dataset: 'GLBX.MDP3',
  schema: 'mbp-1',
  last_event_ts_utc: '2026-05-21T14:00:00Z',
  last_message: `Feed ${state}`,
  metadata: {},
});

let sequence = 1;
const envelope = <T,>(type: Envelope<T>['type'], payload: T): string => JSON.stringify({ version: 'ws.v1', type, sequence: sequence++, server_time_utc: '2026-05-21T14:05:00Z', payload });

type TradingChartProps = React.ComponentProps<typeof TradingChart>;

const lastChartProps = () => {
  const calls = chartMock.render.mock.calls as unknown as Array<[TradingChartProps]>;
  return calls.at(-1)?.[0] as TradingChartProps;
};

describe('ChartWorkspace realtime chart dataflow', () => {
  let sockets: FakeSocket[];
  let client: RealtimeClient;

  beforeEach(() => {
    resetStores();
    chartMock.render.mockClear();
    sequence = 1;
    sockets = [];
    client = new RealtimeClient('ws://localhost:8001/ws/v1', () => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket as unknown as WebSocket;
    });
  });

  afterEach(() => {
    cleanup();
    client.stop();
    resetStores();
  });

  it('renders current selected-timeframe candle updates from market.bar.updated', () => {
    render(<ChartWorkspace />);
    act(() => client.start());

    act(() => sockets[0].message(envelope('market.bar.updated', { bars: [bar(147, false, { close_ticks: 76004 })] })));
    expect(lastChartProps().bars).toEqual([expect.objectContaining({ key: '147:2026-05-21:2026-05-21T14:00:00Z', close: 19001, complete: false })]);

    act(() => sockets[0].message(envelope('market.bar.updated', { bars: [bar(147, false, { close_ticks: 76012 })] })));
    expect(lastChartProps().bars).toEqual([expect.objectContaining({ close: 19003, complete: false })]);
  });

  it('appends and dedupes closed bars while preserving non-selected timeframes until selected', () => {
    render(<ChartWorkspace />);
    act(() => client.start());

    act(() => sockets[0].message(envelope('market.bar.closed', { bars: [bar(987, true, { close_ticks: 76004 })] })));

    expect(marketStore.getSnapshot().recentClosedBars).toHaveLength(1);
    expect(lastChartProps()).toMatchObject({ timeframe: 147, bars: [] });

    act(() => marketStore.setState({ selectedTimeframe: 987 }));
    expect(lastChartProps().bars).toEqual([expect.objectContaining({ timeframe: 987, close: 19001, complete: true })]);

    act(() => sockets[0].message(envelope('market.bar.closed', { bars: [bar(987, true, { close_ticks: 76008 })] })));
    expect(marketStore.getSnapshot().recentClosedBars).toHaveLength(1);
    expect(lastChartProps().bars).toEqual([expect.objectContaining({ close: 19002 })]);
  });

  it('updates level, touch, and observation overlays from realtime messages', () => {
    render(<ChartWorkspace />);
    act(() => client.start());

    act(() => sockets[0].message(envelope('levels.updated', { levels: [level] })));
    expect(lastChartProps().levels).toEqual([expect.objectContaining({ id: 'pdh:76000:2026-05-21:ny', price: 19000, eligible: true })]);

    // Markers anchor onto the bar containing their event, so a bar must exist first.
    act(() => sockets[0].message(envelope('market.bar.updated', { bars: [bar(147, false)] })));
    act(() => sockets[0].message(envelope('touch.detected', touch)));
    act(() => sockets[0].message(envelope('observation.updated', observation)));

    expect(lastChartProps().markers.map((marker) => marker.id)).toEqual(['touch:touch-1', 'observation:obs-1']);
  });

  it('adds prediction and outcome markers anchored to the touch bar', () => {
    render(<ChartWorkspace />);
    act(() => client.start());

    act(() => sockets[0].message(envelope('market.bar.updated', { bars: [bar(147, false)] })));
    act(() => sockets[0].message(envelope('prediction.created', { prediction: predictionDto })));

    expect(lastChartProps().markers.map((marker) => marker.id)).toContain('prediction:pred-1');

    act(() => sockets[0].message(envelope('prediction.resolved', { outcome: outcomeDto })));

    const ids = lastChartProps().markers.map((marker) => marker.id);
    expect(ids).toContain('prediction:pred-1');
    expect(ids).toContain('outcome:outcome-1');
  });

  it('does not change chart bars or overlays on heartbeat and feed-status messages', () => {
    render(<ChartWorkspace />);
    act(() => client.start());
    act(() => sockets[0].message(envelope('market.bar.updated', { bars: [bar(147, false)] })));
    act(() => sockets[0].message(envelope('levels.updated', { levels: [level] })));
    const before = lastChartProps();

    act(() => sockets[0].message(envelope('system.heartbeat', {})));
    act(() => sockets[0].message(envelope('feed.status', feedStatus('connected'))));
    const after = lastChartProps();

    expect(after.bars).toBe(before.bars);
    expect(after.levels).toBe(before.levels);
    expect(after.markers).toBe(before.markers);
  });
});
