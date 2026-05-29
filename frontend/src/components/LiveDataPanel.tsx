import { useEffect } from 'react';
import { apiClient } from '../api/client';
import { normalizeLiveStatus } from '../domain/normalize';
import { addBlotterEvent, liveStore, useLive, useRuntime } from '../state/stores';

const BUSY_STATES = new Set(['connecting', 'running']);

export function LiveDataPanel() {
  const live = useLive();
  const apiOnline = useRuntime((state) => state.apiOnline);
  const status = live.status;
  const canStart = apiOnline && status.subscriptionReady && !live.loading && !BUSY_STATES.has(status.state);
  const canStop = apiOnline && !live.loading && BUSY_STATES.has(status.state);

  useEffect(() => {
    void refreshLiveStatus();
  }, []);

  return (
    <section className="panel live-panel" aria-label="Opt-in live market data controls">
      <div className="live-heading">
        <span className="eyebrow">Opt-in live data</span>
        <h2>Databento Market Data</h2>
        <div className={`replay-state ${status.state}`}>{status.state}</div>
      </div>
      <div className="live-grid">
        <Metric label="Dataset" value={status.dataset} />
        <Metric label="Requested symbol" value={status.requestedSymbol} />
        <Metric label="Schemas" value={status.schemas.join(', ')} />
        <Metric label="API key configured" value={status.apiKeyConfigured ? 'Yes' : 'No'} />
        <Metric label="SDK available" value={formatOptionalBoolean(status.sdkAvailable)} />
        <Metric label="Subscription ready" value={status.subscriptionReady ? 'Yes' : 'No'} />
        <Metric label="Processed" value={String(status.eventsProcessed)} />
        <Metric label="Last event" value={formatTime(status.lastEventUtc)} />
      </div>
      <div className="live-note">
        Market-data only. No trading, execution, risk engine, accounts, or browser API-key entry.
      </div>
      {(!status.enabled || !status.apiKeyConfigured || status.sdkAvailable === false || live.error || !apiOnline) && (
        <div className="replay-error" role="status">
          {!apiOnline
            ? 'Backend offline: live controls are disabled.'
            : !status.enabled
              ? 'Live onboarding flag is disabled on the backend. Set TRADE_LAB_DATABENTO_LIVE_ENABLED=true and restart the backend.'
              : !status.apiKeyConfigured
                ? 'Databento API key is not configured in the backend environment. Set TRADE_LAB_DATABENTO_API_KEY and restart the backend.'
                : status.sdkAvailable === false
                  ? 'Databento SDK is not installed on the backend host.'
                  : live.error}
        </div>
      )}
      <div className="replay-actions">
        <button disabled={!canStart} onClick={() => void runLiveControl('start')}>Start Live</button>
        <button disabled={!canStop} onClick={() => void runLiveControl('stop')}>Stop Live</button>
      </div>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="replay-metric"><span>{label}</span><strong>{value}</strong></div>;
}

async function refreshLiveStatus() {
  const result = await apiClient.liveStatus();
  if (result.ok) {
    liveStore.setState({ status: normalizeLiveStatus(result.data), error: null });
    return;
  }
  liveStore.setState({ error: result.error });
}

async function runLiveControl(action: 'start' | 'stop') {
  liveStore.setState({ loading: true, error: null });
  const result = action === 'start' ? await apiClient.startLive() : await apiClient.stopLive();
  liveStore.setState({ loading: false });
  if (result.ok) {
    liveStore.setState({ status: normalizeLiveStatus(result.data), error: null });
    addBlotterEvent({ timeUtc: new Date().toISOString(), category: 'live', severity: 'info', message: `Live ${action} accepted` });
    return;
  }
  liveStore.setState({ error: result.error });
  addBlotterEvent({ timeUtc: new Date().toISOString(), category: 'live', severity: 'error', message: `Live ${action} failed: ${result.error}` });
}

const formatTime = (value: string | null) => value ? new Date(value).toLocaleTimeString([], { hour12: false }) : '—';
const formatOptionalBoolean = (value: boolean | null) => value === null ? 'Unknown' : value ? 'Yes' : 'No';
