import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { App } from './App';
import { blotterStore, connectionStore, intelligenceStore, liveStore, marketStore, replayStore, runtimeStore } from './state/stores';

const mocks = vi.hoisted(() => ({
  health: vi.fn(),
  status: vi.fn(),
  replaySources: vi.fn(),
  replayStatus: vi.fn(),
  liveStatus: vi.fn(),
  listModels: vi.fn(),
  activeModel: vi.fn(),
  start: vi.fn(),
  stop: vi.fn(),
  chartRemove: vi.fn(),
  setData: vi.fn(),
  update: vi.fn(),
  createPriceLine: vi.fn(() => ({ id: 'line' })),
  removePriceLine: vi.fn(),
  setMarkers: vi.fn(),
  removeMarkers: vi.fn(),
}));

vi.mock('lightweight-charts', () => ({
  CandlestickSeries: 'CandlestickSeries',
  ColorType: { Solid: 'solid' },
  CrosshairMode: { Normal: 1 },
  createChart: vi.fn(() => ({ addSeries: vi.fn(() => ({ setData: mocks.setData, update: mocks.update, createPriceLine: mocks.createPriceLine, removePriceLine: mocks.removePriceLine })), remove: mocks.chartRemove })),
  createSeriesMarkers: vi.fn(() => ({ setMarkers: mocks.setMarkers, remove: mocks.removeMarkers })),
}));

vi.mock('./api/client', () => ({
  apiClient: {
    health: mocks.health,
    status: mocks.status,
    replaySources: mocks.replaySources,
    replayStatus: mocks.replayStatus,
    liveStatus: mocks.liveStatus,
    listModels: mocks.listModels,
    activeModel: mocks.activeModel,
  },
}));

vi.mock('./realtime/client', () => ({
  realtimeClient: { start: mocks.start, stop: mocks.stop },
}));

const resetStores = () => {
  runtimeStore.reset();
  connectionStore.reset();
  marketStore.reset();
  intelligenceStore.reset();
  blotterStore.reset();
  replayStore.reset();
  liveStore.reset();
  mocks.replaySources.mockResolvedValue({ ok: true, data: { sources: [] } });
  mocks.replayStatus.mockResolvedValue({ ok: true, data: { state: 'idle', events_processed: 0, warnings_recorded: 0, last_event_ts_utc: null, last_error: null, requested_symbol: null, schema: null } });
  mocks.liveStatus.mockResolvedValue({ ok: true, data: { state: 'idle', requested_symbol: 'NQ.c.0', dataset: 'GLBX.MDP3', schemas: ['trades'], api_key_configured: false, enabled: false, events_processed: 0, last_event_ts_utc: null, last_error: null, started_at_utc: null, stopped_at_utc: null } });
  mocks.listModels.mockResolvedValue({ ok: true, data: { models: [] } });
  mocks.activeModel.mockResolvedValue({ ok: true, data: { loaded: false, model_id: null, strategy_id: null, training_mode: null, instrument: null, feature_names: [], class_map: {}, validation_ok: false, validation_detail: null } });
};

describe('App workstation UI', () => {
  beforeEach(() => resetStores());

  afterEach(() => {
    cleanup();
    resetStores();
    vi.clearAllMocks();
  });

  it('renders the status bar, chart workspace, intelligence panel, and event blotter shell', async () => {
    mocks.health.mockResolvedValue({ ok: true, data: { ok: true, service: 'trade-lab-backend', version: '0.1.0' } });
    mocks.status.mockResolvedValue({
      ok: true,
      data: {
        service: 'trade-lab-backend',
        version: '0.1.0',
        runtime_mode: 'live',
        requested_symbol: 'NQ.c.0',
        instrument_root: 'NQ',
        supported_tick_timeframes: [147, 987, 2000],
        engine_ready: true,
        feed_ready: false,
        feed_state: 'disconnected',
        replay: { state: 'idle', events_processed: 0, warnings_recorded: 0, last_event_ts_utc: null, last_error: null, requested_symbol: null, schema: null },
        live: { state: 'idle', requested_symbol: 'NQ.c.0', dataset: 'GLBX.MDP3', schemas: ['trades'], api_key_configured: false, enabled: false, events_processed: 0, last_event_ts_utc: null, last_error: null, started_at_utc: null, stopped_at_utc: null },
      },
    });

    render(<App />);

    expect(screen.getByText('Trade-Lab')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'NQ Tick-Bar Analytics' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Market Structure' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Runtime Events' })).toBeInTheDocument();
    expect(mocks.start).toHaveBeenCalledOnce();

    await waitFor(() => expect(screen.getByText('live')).toBeInTheDocument());
  });

  it('shows 147t, 987t, and 2000t timeframe choices and updates selected timeframe', async () => {
    mocks.health.mockResolvedValue({ ok: false, error: 'Backend unavailable: offline' });
    mocks.status.mockResolvedValue({ ok: false, error: 'Backend unavailable: offline' });

    render(<App />);

    const selector = screen.getByLabelText('Tick timeframe');
    expect(selector).toHaveTextContent('147t');
    expect(selector).toHaveTextContent('987t');
    expect(selector).toHaveTextContent('2000t');
    expect(screen.getByText('Timeframe').closest('.status-pill')).toHaveTextContent('147t');
    await waitFor(() => expect(screen.getByText('Backend unavailable: offline')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: '987t' }));

    expect(marketStore.getSnapshot().selectedTimeframe).toBe(987);
    expect(screen.getByText('Timeframe').closest('.status-pill')).toHaveTextContent('987t');
  });

  it('makes offline backend and WebSocket state visible without crashing', async () => {
    mocks.health.mockResolvedValue({ ok: false, error: 'Backend unavailable: connect ECONNREFUSED' });
    mocks.status.mockResolvedValue({ ok: false, error: 'Backend unavailable: connect ECONNREFUSED' });
    connectionStore.setState({ wsStatus: 'offline', error: 'socket closed' });

    render(<App />);

    expect(screen.getByText('Runtime snapshot idle')).toBeInTheDocument();
    expect(screen.getByText(/Backend offline · WebSocket offline/)).toBeInTheDocument();
    expect(screen.getAllByText('offline').length).toBeGreaterThanOrEqual(2);
    await waitFor(() => expect(screen.getByText('Backend unavailable: connect ECONNREFUSED')).toBeInTheDocument());
  });

  it('does not present level origin as the current session', async () => {
    mocks.health.mockResolvedValue({ ok: false, error: 'Backend unavailable: offline' });
    mocks.status.mockResolvedValue({ ok: false, error: 'Backend unavailable: offline' });
    intelligenceStore.setState({ levels: [{ kind: 'pdh', priceTicks: 76000, tradingDay: '2026-05-21', originSession: 'ny', developing: false, eligible: true }] });

    render(<App />);

    expect(screen.getAllByText('Session').find((element) => element.closest('.status-pill'))?.closest('.status-pill')).toHaveTextContent('unavailable');
    expect(screen.getByText('Level origin').closest('.key-value')).toHaveTextContent('ny');
    await waitFor(() => expect(screen.getByText('Backend unavailable: offline')).toBeInTheDocument());
  });
});
