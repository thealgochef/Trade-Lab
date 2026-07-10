import { useEffect, useState } from 'react';
import { apiClient } from './api/client';
import { normalizeLiveStatus, normalizeReplayStatus, normalizeRuntimeStatus } from './domain/normalize';
import { realtimeClient } from './realtime/client';
import { addBlotterEvent, liveStore, replayStore, runtimeStore } from './state/stores';
import { TopStatusBar } from './components/TopStatusBar';
import { ChartWorkspace } from './components/ChartWorkspace';
import { IntelligencePanel } from './components/IntelligencePanel';
import { EventBlotter } from './components/EventBlotter';
import { ReplayControls } from './components/ReplayControls';
import { LiveDataPanel } from './components/LiveDataPanel';
import { ModelPanel } from './components/ModelPanel';
import { PerformancePage } from './components/PerformancePage';

type WorkspaceView = 'workstation' | 'performance';

export function App() {
  const [view, setView] = useState<WorkspaceView>('workstation');
  useEffect(() => {
    let cancelled = false;
    const refreshStatus = async () => {
      const [health, status] = await Promise.all([apiClient.health(), apiClient.status()]);
      if (cancelled) return;
      if (health.ok && status.ok) {
        runtimeStore.setState(normalizeRuntimeStatus(status.data));
        replayStore.setState((current) => ({ ...current, status: normalizeReplayStatus(status.data.replay), error: null }));
        liveStore.setState((current) => ({ ...current, status: normalizeLiveStatus(status.data.live), error: null }));
      } else {
        const error = !health.ok ? health.error : !status.ok ? status.error : 'Unknown API error';
        runtimeStore.setState((current) => ({ ...current, apiOnline: false, feedReady: false, lastError: error }));
        addBlotterEvent({ timeUtc: new Date().toISOString(), category: 'system', severity: 'warning', message: error });
      }
    };
    void refreshStatus();
    const interval = window.setInterval(refreshStatus, 15_000);
    realtimeClient.start();
    return () => {
      cancelled = true;
      window.clearInterval(interval);
      realtimeClient.stop();
    };
  }, []);

  return (
    <main className={view === 'workstation' ? 'workstation-shell' : 'workstation-shell performance-view'}>
      <TopStatusBar />
      <nav className="view-tabs" aria-label="Workspace view">
        <div className="segmented-control">
          <button className={view === 'workstation' ? 'active' : ''} onClick={() => setView('workstation')}>
            Workstation
          </button>
          <button className={view === 'performance' ? 'active' : ''} onClick={() => setView('performance')}>
            Performance
          </button>
        </div>
      </nav>
      {view === 'workstation' ? (
        <>
          <section className="control-row">
            <ReplayControls />
            <LiveDataPanel />
          </section>
          <section className="control-row model-row">
            <ModelPanel />
          </section>
          <section className="workspace-grid">
            <ChartWorkspace />
            <IntelligencePanel />
          </section>
          <EventBlotter />
        </>
      ) : (
        <PerformancePage />
      )}
    </main>
  );
}
