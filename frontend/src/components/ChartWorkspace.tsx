import { useMemo } from 'react';
import { combineMarkers, normalizeBarsForTimeframe, normalizeLevels } from '../chart/viewModels';
import { TradingChart } from './TradingChart';
import { marketStore, useConnection, useIntelligence, useMarket, useRuntime } from '../state/stores';
import type { Timeframe } from '../domain/models';

const TIMEFRAMES: Timeframe[] = [147, 987, 2000];

export function ChartWorkspace() {
  // Chart-facing subscriptions intentionally select narrow store slices so
  // heartbeat/feed-status changes do not rebuild candle data or overlays.
  const selectedTimeframe = useMarket((state) => state.selectedTimeframe);
  const currentBars = useMarket((state) => state.currentBars);
  const recentClosedBars = useMarket((state) => state.recentClosedBars);
  const levels = useIntelligence((state) => state.levels);
  const touches = useIntelligence((state) => state.touches);
  const observations = useIntelligence((state) => state.observations);
  const feedReady = useRuntime((state) => state.feedReady);
  const apiOnline = useRuntime((state) => state.apiOnline);
  const wsStatus = useConnection((state) => state.wsStatus);

  const bars = useMemo(() => normalizeBarsForTimeframe([...currentBars, ...recentClosedBars], selectedTimeframe), [currentBars, recentClosedBars, selectedTimeframe]);
  const levelOverlays = useMemo(() => normalizeLevels(levels), [levels]);
  const markerOverlays = useMemo(() => combineMarkers(touches, observations), [touches, observations]);

  return (
    <section className="panel chart-panel">
      <div className="panel-header">
        <div>
          <span className="eyebrow">Main chart workspace</span>
          <h1>NQ Tick-Bar Analytics</h1>
        </div>
        <div className="segmented-control" aria-label="Tick timeframe">
          {TIMEFRAMES.map((timeframe) => (
            <button key={timeframe} className={selectedTimeframe === timeframe ? 'active' : ''} onClick={() => marketStore.setState({ selectedTimeframe: timeframe })}>
              {timeframe}t
            </button>
          ))}
        </div>
      </div>
      <TradingChart
        timeframe={selectedTimeframe}
        bars={bars}
        levels={levelOverlays}
        markers={markerOverlays}
        emptyTitle={feedReady ? `Awaiting ${selectedTimeframe}t tick bars` : 'Runtime snapshot idle'}
        emptySubtitle={`Backend ${apiOnline ? 'reachable' : 'offline'} · WebSocket ${wsStatus}`}
      />
    </section>
  );
}
