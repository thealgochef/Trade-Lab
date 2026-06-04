import { useConnection, useMarket, useRuntime } from '../state/stores';

const fmt = (value: string | null) => (value ? new Date(value).toLocaleTimeString() : '—');

export function TopStatusBar() {
  const runtime = useRuntime();
  const connection = useConnection();
  const market = useMarket();
  const tradingDay = runtime.tradingDay ?? '—';
  const session = runtime.session ?? 'unavailable';

  return (
    <header className="top-bar">
      <div className="brand-block">
        <span className="eyebrow">Trade-Lab</span>
        <strong>NQ / {runtime.requestedSymbol}</strong>
      </div>
      <StatusPill label="Mode" value={runtime.runtimeMode} tone={runtime.runtimeMode === 'replay' ? 'amber' : 'blue'} />
      <StatusPill label="Session" value={session} tone={runtime.session ? 'blue' : 'neutral'} />
      <StatusPill label="Trading Day" value={tradingDay} />
      <StatusPill label="Feed" value={runtime.feedState} tone={runtime.feedReady ? 'green' : 'red'} />
      <StatusPill label="API" value={runtime.apiOnline ? 'online' : 'offline'} tone={runtime.apiOnline ? 'green' : 'red'} />
      <StatusPill label="WS" value={connection.wsStatus} tone={connection.wsStatus === 'connected' ? 'green' : 'amber'} />
      <StatusPill label="Heartbeat" value={fmt(connection.lastHeartbeatUtc)} />
      <StatusPill label="Timeframe" value={`${market.selectedTimeframe}t`} tone="blue" />
    </header>
  );
}

function StatusPill({ label, value, tone = 'neutral' }: { label: string; value: string; tone?: 'neutral' | 'green' | 'red' | 'amber' | 'blue' }) {
  return (
    <div className={`status-pill ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
