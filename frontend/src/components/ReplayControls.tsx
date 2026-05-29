import { useEffect } from 'react';
import { apiClient } from '../api/client';
import { normalizeReplaySource, normalizeReplayStatus } from '../domain/normalize';
import { addBlotterEvent, replayStore, runtimeStore, useReplay, useRuntime } from '../state/stores';

const BUSY_STATES = new Set(['loading', 'ready', 'running']);

export function ReplayControls() {
  const replay = useReplay();
  const apiOnline = useRuntime((state) => state.apiOnline);
  const state = replay.status.state;
  const hasSelectedSource = replay.sources.some((source) => source.id === replay.selectedSourceId);
  const canStart = apiOnline && hasSelectedSource && !replay.loading && !BUSY_STATES.has(state);
  const canPause = apiOnline && !replay.loading && state === 'running';
  const canResume = apiOnline && !replay.loading && state === 'paused';
  const canStop = apiOnline && !replay.loading && ['loading', 'ready', 'running', 'paused'].includes(state);

  useEffect(() => {
    void refreshReplayData();
  }, []);

  return (
    <section className="panel replay-panel" aria-label="Safe replay controls">
      <div className="replay-heading">
        <div>
          <span className="eyebrow">Safe replay</span>
          <h2>Safe Market Replay</h2>
        </div>
        <div className={`replay-state ${state}`}>{state}</div>
      </div>
      <div className="replay-controls-grid">
        <label className="replay-source-select">
          <span>Allowlisted source</span>
          <select
            value={replay.selectedSourceId}
            disabled={!apiOnline || replay.loading || BUSY_STATES.has(state)}
            onChange={(event) => replayStore.setState({ selectedSourceId: event.target.value })}
          >
            {replay.sources.map((source) => (
              <option key={source.id} value={source.id}>{source.kind === 'historical' ? 'Historical' : 'Synthetic'} — {source.label}</option>
            ))}
          </select>
        </label>
        <ReplayMetric label="Processed" value={String(replay.status.eventsProcessed)} />
        <ReplayMetric label="Warnings" value={String(replay.status.warningsRecorded)} />
        <ReplayMetric label="Last event" value={formatTime(replay.status.lastEventUtc)} />
        <ReplayMetric label="Started" value={formatTime(replay.status.startedAtUtc)} />
        <ReplayMetric label="Completed" value={formatTime(replay.status.completedAtUtc ?? replay.status.failedAtUtc)} />
      </div>
      {(replay.error || replay.status.lastError || !apiOnline) && (
        <div className="replay-error" role="status">
          {!apiOnline ? 'Backend offline: replay controls are disabled.' : replay.error ?? replay.status.lastError}
        </div>
      )}
      {replay.sources.every((source) => source.kind !== 'historical') && (
        <div className="replay-hint" role="status">
          Historical sources unavailable; synthetic replay remains available.
          {replay.historical && <div>{formatHistoricalStatus(replay.historical)}</div>}
        </div>
      )}
      {replay.sources.length === 0 && (
        <div className="replay-hint" role="status">No safe replay sources are currently available.</div>
      )}
      <div className="replay-actions">
        <button disabled={!canStart} onClick={() => void runControl('start')}>Start</button>
        <button disabled={!canPause} onClick={() => void runControl('pause')}>Pause</button>
        <button disabled={!canResume} onClick={() => void runControl('resume')}>Resume</button>
        <button disabled={!canStop} onClick={() => void runControl('stop')}>Stop</button>
      </div>
    </section>
  );
}

function ReplayMetric({ label, value }: { label: string; value: string }) {
  return <div className="replay-metric"><span>{label}</span><strong>{value}</strong></div>;
}

async function refreshReplayData() {
  const [sources, status] = await Promise.all([apiClient.replaySources(), apiClient.replayStatus()]);
  if (sources.ok) {
    const safeSources = sources.data.sources.filter(isSafeReplaySourceDto).map(normalizeReplaySource);
    replayStore.setState((current) => ({
      ...current,
      sources: safeSources,
      selectedSourceId: safeSources.some((source) => source.id === current.selectedSourceId) ? current.selectedSourceId : (safeSources[0]?.id ?? current.selectedSourceId),
      historical: normalizeHistoricalStatus(sources.data.historical),
      error: null,
    }));
  }
  if (status.ok) {
    const normalized = normalizeReplayStatus(status.data);
    replayStore.setState((current) => ({ ...current, status: normalized, selectedSourceId: normalized.sourceId ?? current.selectedSourceId, error: null }));
    runtimeStore.setState((current) => ({ ...current, replayState: normalized.state, lastError: normalized.lastError }));
  }
  const error = !sources.ok ? sources.error : !status.ok ? status.error : null;
  if (error) replayStore.setState({ error });
}

async function runControl(action: 'start' | 'pause' | 'resume' | 'stop') {
  replayStore.setState({ loading: true, error: null });
  const selectedSourceId = replayStore.getSnapshot().selectedSourceId;
  if (!isSafeSourceId(selectedSourceId)) {
    replayStore.setState({ loading: false, error: 'Invalid replay source selected' });
    addBlotterEvent({ timeUtc: new Date().toISOString(), category: 'replay', severity: 'error', message: 'Replay start failed: invalid replay source selected' });
    return;
  }
  const result = action === 'start'
    ? await apiClient.startReplay({ source_id: selectedSourceId })
    : action === 'pause'
      ? await apiClient.pauseReplay()
      : action === 'resume'
        ? await apiClient.resumeReplay()
        : await apiClient.stopReplay();
  replayStore.setState({ loading: false });
  if (result.ok) {
    const status = normalizeReplayStatus(result.data);
    replayStore.setState((current) => ({ ...current, status, selectedSourceId: status.sourceId ?? current.selectedSourceId, error: null }));
    runtimeStore.setState((current) => ({ ...current, replayState: status.state, lastError: status.lastError }));
    addBlotterEvent({ timeUtc: new Date().toISOString(), category: 'replay', severity: 'info', message: `Replay ${action} accepted` });
    return;
  }
  replayStore.setState({ error: result.error });
  addBlotterEvent({ timeUtc: new Date().toISOString(), category: 'replay', severity: 'error', message: `Replay ${action} failed: ${result.error}` });
}

const formatTime = (value: string | null) => value ? new Date(value).toLocaleTimeString([], { hour12: false }) : '—';

const normalizeHistoricalStatus = (historical: { available: boolean; status: string; diagnostics?: Record<string, boolean | number | string> } | undefined) => {
  if (!historical) return null;
  return { available: historical.available, status: historical.status, diagnostics: historical.diagnostics };
};

const formatHistoricalStatus = (historical: { status: string; diagnostics?: Record<string, boolean | number | string> }) => {
  const diagnostics = historical.diagnostics;
  if (!diagnostics) return historical.status;
  const parts = SAFE_HISTORICAL_DIAGNOSTIC_KEYS
    .map((key) => [key, diagnostics[key]] as const)
    .filter(([, value]) => typeof value === 'number' || value === true || (typeof value === 'string' && /^[a-z_]+$/.test(value)))
    .map(([key, value]) => `${SAFE_HISTORICAL_DIAGNOSTIC_LABELS[key]}: ${String(value)}`);
  return parts.length ? `${historical.status} (${parts.join(', ')})` : historical.status;
};

const SAFE_HISTORICAL_DIAGNOSTIC_KEYS = [
  'data_path_configured',
  'root_available',
  'root_exists',
  'root_traversable',
  'parquet_candidates_seen',
  'parquet_files_inspected',
  'metadata_reads_attempted',
  'skipped_unsupported_names',
  'outside_root_or_unresolvable',
  'unreadable_metadata',
  'unsupported_schema_or_required_columns',
  'duplicates',
  'discovered',
  'traversal_truncated',
  'truncation_reason',
] as const;

const SAFE_HISTORICAL_DIAGNOSTIC_LABELS: Record<typeof SAFE_HISTORICAL_DIAGNOSTIC_KEYS[number], string> = {
  data_path_configured: 'data path configured',
  root_available: 'root available',
  root_exists: 'root exists',
  root_traversable: 'root traversable',
  parquet_candidates_seen: 'parquet candidates seen',
  parquet_files_inspected: 'parquet files inspected',
  metadata_reads_attempted: 'metadata reads attempted',
  skipped_unsupported_names: 'skipped unsupported names',
  outside_root_or_unresolvable: 'outside root or unresolvable',
  unreadable_metadata: 'unreadable metadata',
  unsupported_schema_or_required_columns: 'unsupported schema or required columns',
  duplicates: 'duplicates',
  discovered: 'discovered',
  traversal_truncated: 'traversal truncated',
  truncation_reason: 'truncation reason',
};

const isSafeSourceId = (sourceId: string) => /^[A-Za-z0-9_.:-]+$/.test(sourceId) && !/^[A-Za-z]:/.test(sourceId) && !sourceId.includes('..') && !sourceId.includes('/') && !sourceId.includes('\\');

const isSafeReplaySourceDto = (source: { source_id: string; label: string }) =>
  isSafeSourceId(source.source_id) && !/[\\/]|\.\.|[A-Za-z]:/.test(source.label);
