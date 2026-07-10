import { useMemo } from 'react';
import { formatAge, formatClock, formatSignedPoints, latestPrice, unrealizedFor } from '../execution/viewModels';
import { useExecutions, useMarket } from '../state/stores';
import type { ClosedExecution, OpenPosition } from '../domain/models';
import type { LastPrice } from '../execution/viewModels';

export function ExecutionsPanel() {
  const openPositions = useExecutions((state) => state.openPositions);
  const closed = useExecutions((state) => state.closed);
  const currentBars = useMarket((state) => state.currentBars);
  const recentClosedBars = useMarket((state) => state.recentClosedBars);
  const last = useMemo(() => latestPrice(currentBars, recentClosedBars), [currentBars, recentClosedBars]);

  return (
    <section className="panel executions-panel" aria-label="Paper executions">
      <div className="panel-header compact">
        <span className="eyebrow">Paper executions</span>
        <h2>Observer-derived fills</h2>
        <span className="exec-note">
          optimistic / conservative (1-tick-adverse entry, 1-tick-adverse sl exit) · 1 contract · no costs
        </span>
      </div>
      <div className="exec-body">
        <div className="exec-open" aria-label="Open paper positions">
          <h3>Open positions</h3>
          {openPositions.length === 0 ? (
            <p className="empty-text">No open paper position.</p>
          ) : (
            openPositions.map((position) => (
              <OpenPositionCard key={position.predictionId} position={position} last={last} />
            ))
          )}
        </div>
        <div className="exec-closed" aria-label="Closed paper executions">
          <h3>Closed executions</h3>
          {closed.length === 0 ? (
            <p className="empty-text">No closed paper executions this session.</p>
          ) : (
            <table className="perf-table">
              <thead>
                <tr>
                  <th>Exit (UTC)</th>
                  <th>Side</th>
                  <th>Entry</th>
                  <th>Exit</th>
                  <th>Pts</th>
                  <th>Pts (cons)</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {closed.map((execution) => (
                  <ClosedExecutionRow key={execution.predictionId} execution={execution} />
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </section>
  );
}

function OpenPositionCard({ position, last }: { position: OpenPosition; last: LastPrice | null }) {
  const unrealized = unrealizedFor(position, last);
  const tone = unrealized.points === null ? '' : unrealized.points >= 0 ? 'pos' : 'neg';
  return (
    <div className="exec-card">
      <div className="exec-card-head">
        <span className={`intel-badge ${position.direction === 'long' ? 'eligible' : 'incorrect'}`}>
          {position.direction}
        </span>
        <strong>entry {position.entryPrice.toFixed(2)}</strong>
        <span className={`exec-unrealized ${tone}`}>
          {formatSignedPoints(unrealized.points)} pts
        </span>
        <span className="exec-meta">cons {formatSignedPoints(unrealized.pointsConservative)}</span>
      </div>
      <div className="exec-meta">
        tp {position.tpPrice.toFixed(2)} · sl {position.slPrice.toFixed(2)} · mark{' '}
        {unrealized.markPrice === null ? '—' : unrealized.markPrice.toFixed(2)} · age{' '}
        {formatAge(position.entryTsUtc, last?.timeUtc ?? null)}
      </div>
      <div className="exec-meta">
        {position.levelKind.replaceAll('_', ' ')} · {position.session} · {position.mode} ·{' '}
        {position.contracts} contract @ ${position.pointValue}/pt
      </div>
    </div>
  );
}

function ClosedExecutionRow({ execution }: { execution: ClosedExecution }) {
  return (
    <tr>
      <td>{formatClock(execution.exitTsUtc)}</td>
      <td>{execution.direction}</td>
      <td>{execution.entryPrice.toFixed(2)}</td>
      <td>{execution.exitPrice.toFixed(2)}</td>
      <td className={execution.points >= 0 ? 'pnl-pos' : 'pnl-neg'}>
        {formatSignedPoints(execution.points)}
      </td>
      <td className={execution.pointsConservative >= 0 ? 'pnl-pos' : 'pnl-neg'}>
        {formatSignedPoints(execution.pointsConservative)}
      </td>
      <td>{execution.reason.replaceAll('_', ' ')}</td>
    </tr>
  );
}
