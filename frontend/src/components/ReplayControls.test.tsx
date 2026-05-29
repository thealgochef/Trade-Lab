import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ReplayControls } from './ReplayControls';
import { blotterStore, replayStore, runtimeStore } from '../state/stores';

const mocks = vi.hoisted(() => ({
  replaySources: vi.fn(),
  replayStatus: vi.fn(),
  startReplay: vi.fn(),
  pauseReplay: vi.fn(),
  resumeReplay: vi.fn(),
  stopReplay: vi.fn(),
}));

vi.mock('../api/client', () => ({ apiClient: mocks }));

const replayStatus = (state: string) => ({
  state,
  events_processed: 12,
  warnings_recorded: 0,
  last_event_ts_utc: '2026-01-05T08:00:00Z',
  last_error: null,
  requested_symbol: 'NQ.c.0',
  schema: 'trades',
  source_id: 'synthetic:nq-demo',
  source_label: 'Synthetic NQ demo',
});

describe('ReplayControls', () => {
  beforeEach(() => {
    replayStore.reset();
    runtimeStore.reset();
    blotterStore.reset();
    runtimeStore.setState({ apiOnline: true });
    mocks.replaySources.mockResolvedValue({ ok: true, data: { sources: [{ source_id: 'synthetic:nq-demo', label: 'Synthetic NQ demo', requested_symbol: 'NQ.c.0', schema: 'trades' }] } });
    mocks.replayStatus.mockResolvedValue({ ok: true, data: replayStatus('idle') });
    mocks.startReplay.mockResolvedValue({ ok: true, data: replayStatus('running') });
    mocks.pauseReplay.mockResolvedValue({ ok: true, data: replayStatus('paused') });
    mocks.resumeReplay.mockResolvedValue({ ok: true, data: replayStatus('running') });
    mocks.stopReplay.mockResolvedValue({ ok: true, data: replayStatus('stopped') });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('renders source, status, and disabled states', async () => {
    render(<ReplayControls />);

    expect(screen.getByRole('heading', { name: 'Safe Market Replay' })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('combobox')).toHaveTextContent('Synthetic NQ demo'));
    expect(screen.getByRole('button', { name: 'Pause' })).toBeDisabled();
    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.queryByLabelText(/file|path/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('starts replay through the allowlisted endpoint and records blotter outcome', async () => {
    render(<ReplayControls />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Start' })).toBeEnabled());

    fireEvent.click(screen.getByRole('button', { name: 'Start' }));

    await waitFor(() => expect(mocks.startReplay).toHaveBeenCalledWith({ source_id: 'synthetic:nq-demo' }));
    expect(replayStore.getSnapshot().status.state).toBe('running');
    expect(blotterStore.getSnapshot().events[0].message).toContain('Replay start accepted');
  });

  it('enables pause resume and stop according to replay state', async () => {
    mocks.replayStatus.mockResolvedValueOnce({ ok: true, data: replayStatus('running') });
    render(<ReplayControls />);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Pause' })).toBeEnabled());
    expect(screen.getByRole('button', { name: 'Start' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Resume' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Stop' })).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    await waitFor(() => expect(mocks.pauseReplay).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.getByRole('button', { name: 'Resume' })).toBeEnabled());
    expect(screen.getByRole('button', { name: 'Pause' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Resume' }));
    await waitFor(() => expect(mocks.resumeReplay).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.getByRole('button', { name: 'Pause' })).toBeEnabled());

    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    await waitFor(() => expect(mocks.stopReplay).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.getByRole('button', { name: 'Start' })).toBeEnabled());
  });

  it('filters unsafe backend source ids and labels before rendering controls', async () => {
    mocks.replaySources.mockResolvedValue({
      ok: true,
      data: {
        sources: [
          { source_id: 'synthetic:nq-demo', label: 'Synthetic NQ demo', requested_symbol: 'NQ.c.0', schema: 'trades' },
          { source_id: 'C:raw.parquet', label: 'Drive-relative unsafe source', requested_symbol: 'NQ.c.0', schema: 'trades' },
          { source_id: '..\\raw', label: 'C:\\Users\\secret\\raw.parquet', requested_symbol: 'NQ.c.0', schema: 'trades' },
          { source_id: 'synthetic/unsafe', label: 'Unsafe / raw', requested_symbol: 'NQ.c.0', schema: 'trades' },
          { source_id: 'historical:nq:2026-02-22:trades', label: '..\\secret\\raw.parquet', requested_symbol: 'NQ.c.0', schema: 'trades', kind: 'historical' },
        ],
      },
    });

    render(<ReplayControls />);

    await waitFor(() => expect(screen.getByRole('combobox')).toHaveTextContent('Synthetic NQ demo'));
    expect(screen.queryByText(/raw\.parquet|Users|Unsafe|Drive-relative/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Start' }));
    await waitFor(() => expect(mocks.startReplay).toHaveBeenCalledWith({ source_id: 'synthetic:nq-demo' }));
  });

  it('renders and starts historical sources returned by the backend', async () => {
    mocks.replaySources.mockResolvedValue({
      ok: true,
      data: {
        historical: { available: true, status: 'historical sources discovered' },
        sources: [
          { source_id: 'synthetic:nq-demo', label: 'Synthetic NQ demo', requested_symbol: 'NQ.c.0', schema: 'trades', kind: 'synthetic' },
          { source_id: 'historical:nq:2026-02-22:trades', label: 'Historical NQ 2026-02-22 trades', requested_symbol: 'NQ.c.0', schema: 'trades', kind: 'historical', session_label: '2026-02-22', availability: 'metadata_only' },
        ],
      },
    });
    render(<ReplayControls />);

    await waitFor(() => expect(screen.getByRole('combobox')).toHaveTextContent('Historical — Historical NQ 2026-02-22 trades'));
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'historical:nq:2026-02-22:trades' } });
    fireEvent.click(screen.getByRole('button', { name: 'Start' }));

    await waitFor(() => expect(mocks.startReplay).toHaveBeenCalledWith({ source_id: 'historical:nq:2026-02-22:trades' }));
  });

  it('shows action errors and keeps the component interactive', async () => {
    mocks.startReplay.mockResolvedValueOnce({ ok: false, error: 'HTTP 409 from /api/v1/replay/start', status: 409 });
    render(<ReplayControls />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Start' })).toBeEnabled());

    fireEvent.click(screen.getByRole('button', { name: 'Start' }));

    await waitFor(() => expect(screen.getByText(/HTTP 409/)).toBeInTheDocument());
    expect(blotterStore.getSnapshot().events[0].message).toContain('Replay start failed');
    expect(screen.getByRole('button', { name: 'Start' })).toBeEnabled();
  });

  it('shows backend offline and control error states without crashing', async () => {
    runtimeStore.setState({ apiOnline: false });
    mocks.replaySources.mockResolvedValue({ ok: false, error: 'Backend unavailable: offline' });

    render(<ReplayControls />);

    expect(screen.getByText(/Backend offline/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start' })).toBeDisabled();
    await waitFor(() => expect(replayStore.getSnapshot().error).toContain('Backend unavailable'));
  });

  it('shows historical unavailable state while keeping synthetic replay non-blocking', async () => {
    mocks.replaySources.mockResolvedValue({
      ok: true,
      data: {
        historical: { available: false, status: 'TRADE_LAB_DATA_PATH is not configured' },
        sources: [{ source_id: 'synthetic:nq-demo', label: 'Synthetic NQ demo', requested_symbol: 'NQ.c.0', schema: 'trades', kind: 'synthetic' }],
      },
    });

    render(<ReplayControls />);

    await waitFor(() => expect(screen.getByText(/Historical sources unavailable/)).toBeInTheDocument());
    expect(screen.getByText(/TRADE_LAB_DATA_PATH is not configured/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start' })).toBeEnabled();
    expect(screen.queryByLabelText(/file|path/i)).not.toBeInTheDocument();
  });

  it('shows safe historical diagnostics from the backend', async () => {
    mocks.replaySources.mockResolvedValue({
      ok: true,
      data: {
        historical: {
          available: false,
          status: 'no supported historical parquet sources found',
          diagnostics: {
            discovered: 0,
            parquet_candidates_seen: 20,
            parquet_files_inspected: 12,
            metadata_reads_attempted: 10,
            outside_root_or_unresolvable: 2,
            unsupported_schema_or_required_columns: 12,
            traversal_truncated: true,
            truncation_reason: 'metadata_read_limit',
            'C:\\Users\\secret\\raw.parquet': 1,
            raw_filename: 'part-000.parquet',
            unsafe_reason: 'C:\\Users\\secret\\raw.parquet',
          },
        },
        sources: [{ source_id: 'synthetic:nq-demo', label: 'Synthetic NQ demo', requested_symbol: 'NQ.c.0', schema: 'trades', kind: 'synthetic' }],
      },
    });

    render(<ReplayControls />);

    await waitFor(() => expect(screen.getByText(/0 discovered|discovered: 0/)).toBeInTheDocument());
    expect(screen.getByText(/parquet files inspected: 12/)).toBeInTheDocument();
    expect(screen.getByText(/parquet candidates seen: 20/)).toBeInTheDocument();
    expect(screen.getByText(/metadata reads attempted: 10/)).toBeInTheDocument();
    expect(screen.getByText(/outside root or unresolvable: 2/)).toBeInTheDocument();
    expect(screen.getByText(/truncation reason: metadata_read_limit/)).toBeInTheDocument();
    expect(screen.getByText(/unsupported schema or required columns: 12/)).toBeInTheDocument();
    expect(screen.queryByText(/Users|secret|raw_filename|part-000|unsafe_reason/)).not.toBeInTheDocument();
  });

  it('shows an empty safe-source state and does not start stale defaults', async () => {
    mocks.replaySources.mockResolvedValue({
      ok: true,
      data: {
        historical: { available: false, status: 'no supported historical parquet sources found' },
        sources: [
          { source_id: '..\\raw', label: 'C:\\Users\\secret\\raw.parquet', requested_symbol: 'NQ.c.0', schema: 'trades' },
          { source_id: 'synthetic/unsafe', label: 'Unsafe / raw', requested_symbol: 'NQ.c.0', schema: 'trades' },
        ],
      },
    });

    render(<ReplayControls />);

    await waitFor(() => expect(screen.getByText(/No safe replay sources/)).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Start' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Start' }));
    expect(mocks.startReplay).not.toHaveBeenCalled();
    expect(screen.queryByText(/raw\.parquet|Users|Unsafe/)).not.toBeInTheDocument();
  });
});
