// COCKPIT P5: the Runtime Events blotter is a trade tape — typed rows for
// predictions, outcomes, drops, and position open/close, with filter chips and
// a newest-row flash. Untyped runtime events keep their plain rows under All.
import { useState } from 'react';
import { useBlotter, useExecutions } from '../state/stores';
import type { ClosedExecution, TapeRow } from '../domain/models';
import { TAPE_FILTERS, formatTapeBracket, matchesTapeFilter, realizedPointsFor, type TapeFilter } from '../blotter/viewModels';

const numericDetailLabels: Record<string, string> = {
  dropped: 'Dropped',
  dropped_messages: 'Dropped messages',
  client_dropped_messages: 'Client dropped messages',
  total_dropped_messages: 'Total dropped messages',
};

const FILTER_LABELS: Record<TapeFilter, string> = {
  all: 'All',
  predictions: 'Predictions',
  executions: 'Executions',
  drops: 'Drops',
};

export function EventBlotter() {
  const { events } = useBlotter();
  const closed = useExecutions((state) => state.closed);
  const [filter, setFilter] = useState<TapeFilter>('all');
  const visible = events.filter((event) => matchesTapeFilter(event, filter)).slice(0, 80);

  return (
    <section className="panel blotter-panel">
      <div className="panel-header compact">
        <span className="eyebrow">Trade tape</span>
        <h2>Runtime Events</h2>
        <div className="segmented-control blotter-filters" role="group" aria-label="Trade tape filter">
          {TAPE_FILTERS.map((entry) => (
            <button key={entry} className={filter === entry ? 'active' : ''} onClick={() => setFilter(entry)}>
              {FILTER_LABELS[entry]}
            </button>
          ))}
        </div>
      </div>
      <div className="blotter-table">
        {visible.length === 0 ? (
          <p className="empty-text">
            {events.length === 0 ? 'Waiting for API status and WebSocket events.' : `No ${filter} rows in the retained tape.`}
          </p>
        ) : (
          visible.map((event, index) => (
            <details className={`blotter-row${index === 0 ? ' tape-newest' : ''}`} key={event.id}>
              <summary>
                <time>{new Date(event.timeUtc).toLocaleTimeString()}</time>
                <span className={`category ${event.severity}`}>{event.category}</span>
                {event.tape ? <TapeSummary tape={event.tape} closed={closed} /> : <strong>{event.message}</strong>}
                {event.code ? <span className="code-badge">{event.code}</span> : null}
              </summary>
              <dl className="blotter-details">
                <div><dt>Timestamp</dt><dd>{event.timeUtc}</dd></div>
                {event.code ? <div><dt>Code</dt><dd>{event.code}</dd></div> : null}
                {event.source ? <div><dt>Source</dt><dd>{event.source}</dd></div> : null}
                {event.details?.schema ? <div><dt>Schema</dt><dd>{event.details.schema}</dd></div> : null}
                {event.details?.detail ? <div><dt>Detail</dt><dd>{event.details.detail}</dd></div> : null}
                {Object.entries(numericDetailLabels).filter(([key]) => typeof event.details?.[key as keyof typeof event.details] === 'number').map(([key, label]) => (
                  <div key={key}><dt>{label}</dt><dd>{String(event.details?.[key as keyof typeof event.details])}</dd></div>
                ))}
              </dl>
            </details>
          ))
        )}
      </div>
    </section>
  );
}

function TapeSummary({ tape, closed }: { tape: TapeRow; closed: ClosedExecution[] }) {
  switch (tape.kind) {
    case 'prediction':
      return (
        <span className="tape-summary">
          <span className="tape-kind prediction">pred</span>
          <strong>{tape.predictedClass}</strong>
          <span className="tape-meta">{tape.probability === null ? 'p —' : `p ${tape.probability.toFixed(2)}`}</span>
          <span className={`intel-badge ${tape.eligible ? 'eligible' : 'ineligible'}`}>{tape.eligible ? 'eligible' : 'ineligible'}</span>
          <span className="tape-meta">{tape.direction} · {tape.session}</span>
        </span>
      );
    case 'outcome': {
      const realized = realizedPointsFor(tape, closed);
      return (
        <span className="tape-summary">
          <span className="tape-kind outcome">outcome</span>
          <span className={`intel-badge ${tape.correct ? 'correct' : 'incorrect'}`}>{tape.correct ? 'correct' : 'miss'}</span>
          <strong>{tape.resolutionType.replaceAll('_', ' ')}</strong>
          <span className="tape-meta">actual {tape.actualClass}</span>
          {realized ? (
            <span className={`tape-points ${realized.points >= 0 ? 'pos' : 'neg'}`}>{formatTapeBracket(realized.points, realized.pointsConservative)}</span>
          ) : null}
        </span>
      );
    }
    case 'drop':
      return (
        <span className="tape-summary">
          <span className="tape-kind drop">drop</span>
          <strong>{tape.reason.replaceAll('_', ' ')}</strong>
        </span>
      );
    case 'position_open':
      return (
        <span className="tape-summary">
          <span className="tape-kind open">open</span>
          <strong>{tape.direction} @ {tape.entryPrice.toFixed(2)} / {tape.entryPriceConservative.toFixed(2)}</strong>
          <span className="tape-meta">tp {tape.tpPrice.toFixed(2)} · sl {tape.slPrice.toFixed(2)}</span>
        </span>
      );
    case 'position_close':
      return (
        <span className="tape-summary">
          <span className="tape-kind close">close</span>
          <strong>{tape.reason.replaceAll('_', ' ')}</strong>
          <span className={`tape-points ${tape.points >= 0 ? 'pos' : 'neg'}`}>{formatTapeBracket(tape.points, tape.pointsConservative)}</span>
          <span className="tape-meta">{tape.direction} exit {tape.exitPrice.toFixed(2)}</span>
        </span>
      );
  }
}
