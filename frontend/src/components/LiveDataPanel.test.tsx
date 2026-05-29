import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { LiveDataPanel } from './LiveDataPanel';
import { blotterStore, liveStore, runtimeStore } from '../state/stores';

const mocks = vi.hoisted(() => ({
  liveStatus: vi.fn(),
  startLive: vi.fn(),
  stopLive: vi.fn(),
}));

vi.mock('../api/client', () => ({ apiClient: mocks }));

const liveStatus = (state: string, key = true, enabled = true) => ({
  state,
  requested_symbol: 'NQ.c.0',
  dataset: 'GLBX.MDP3',
  schemas: ['trades', 'mbp-1', 'definition'],
  api_key_configured: key,
  enabled,
  sdk_available: true,
  subscription_ready: key && enabled,
  events_processed: 7,
  last_event_ts_utc: '2026-01-05T14:30:00Z',
  last_error: null,
  started_at_utc: null,
  stopped_at_utc: null,
});

describe('LiveDataPanel', () => {
  beforeEach(() => {
    liveStore.reset();
    runtimeStore.reset();
    blotterStore.reset();
    runtimeStore.setState({ apiOnline: true });
    mocks.liveStatus.mockResolvedValue({ ok: true, data: liveStatus('idle') });
    mocks.startLive.mockResolvedValue({ ok: true, data: liveStatus('running') });
    mocks.stopLive.mockResolvedValue({ ok: true, data: liveStatus('stopped') });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('renders safe live status without API key input or secret text', async () => {
    render(<LiveDataPanel />);

    await waitFor(() => expect(screen.getByText('GLBX.MDP3')).toBeInTheDocument());
    expect(screen.getByText('NQ.c.0')).toBeInTheDocument();
    expect(screen.getByText(/trades, mbp-1/)).toBeInTheDocument();
    expect(screen.getAllByText('Yes').length).toBeGreaterThanOrEqual(3);
    expect(screen.getByText('SDK available')).toBeInTheDocument();
    expect(screen.getByText('Subscription ready')).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/api key|secret|token/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Market-data only/)).toBeInTheDocument();
    expect(document.body.textContent?.toLowerCase()).not.toMatch(/db-secret|password|token/);
  });

  it('renders no key configured indicator without exposing a value', async () => {
    mocks.liveStatus.mockResolvedValueOnce({ ok: true, data: liveStatus('idle', false, true) });

    render(<LiveDataPanel />);

    await waitFor(() => expect(screen.getAllByText('No').length).toBeGreaterThanOrEqual(2));
    expect(screen.getByText(/API key is not configured in the backend environment/)).toBeInTheDocument();
    expect(screen.queryByDisplayValue(/./)).not.toBeInTheDocument();
  });

  it('disables start when key or backend flag is unavailable', async () => {
    mocks.liveStatus.mockResolvedValueOnce({ ok: true, data: liveStatus('idle', false, true) });
    render(<LiveDataPanel />);
    await waitFor(() => expect(screen.getByText(/API key is not configured/)).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Start Live' })).toBeDisabled();
    cleanup();

    mocks.liveStatus.mockResolvedValueOnce({ ok: true, data: liveStatus('idle', true, false) });
    render(<LiveDataPanel />);
    await waitFor(() => expect(screen.getByText(/TRADE_LAB_DATABENTO_LIVE_ENABLED=true/)).toBeInTheDocument());
    expect(screen.getByText(/restart the backend/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start Live' })).toBeDisabled();
  });

  it('starts and stops live feed through expected endpoints', async () => {
    render(<LiveDataPanel />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Start Live' })).toBeEnabled());

    fireEvent.click(screen.getByRole('button', { name: 'Start Live' }));

    await waitFor(() => expect(mocks.startLive).toHaveBeenCalledOnce());
    expect(liveStore.getSnapshot().status.state).toBe('running');
    expect(blotterStore.getSnapshot().events[0].message).toContain('Live start accepted');

    fireEvent.click(screen.getByRole('button', { name: 'Stop Live' }));
    await waitFor(() => expect(mocks.stopLive).toHaveBeenCalledOnce());
  });

  it('disables stop unless feed is connecting or running', async () => {
    mocks.liveStatus.mockResolvedValueOnce({ ok: true, data: liveStatus('stopped', true, true) });

    render(<LiveDataPanel />);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Start Live' })).toBeEnabled());
    expect(screen.getByRole('button', { name: 'Stop Live' })).toBeDisabled();
  });

  it('shows live control errors and leaves secrets out of state', async () => {
    mocks.startLive.mockResolvedValueOnce({ ok: false, error: 'HTTP 400 from /api/v1/live/start: Databento SDK is not installed on the backend host' });
    render(<LiveDataPanel />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Start Live' })).toBeEnabled());

    fireEvent.click(screen.getByRole('button', { name: 'Start Live' }));

    await waitFor(() => expect(screen.getByText(/HTTP 400 from \/api\/v1\/live\/start/)).toBeInTheDocument());
    expect(screen.getByText(/Databento SDK is not installed/)).toBeInTheDocument();
    expect(JSON.stringify(liveStore.getSnapshot()).toLowerCase()).not.toMatch(/db-secret|token|password|credential/);
  });

  it('shows offline and control errors without crashing', async () => {
    runtimeStore.setState({ apiOnline: false });
    mocks.liveStatus.mockResolvedValueOnce({ ok: false, error: 'Backend unavailable: offline' });

    render(<LiveDataPanel />);

    expect(screen.getByText(/Backend offline/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start Live' })).toBeDisabled();
    await waitFor(() => expect(liveStore.getSnapshot().error).toContain('Backend unavailable'));
  });
});
