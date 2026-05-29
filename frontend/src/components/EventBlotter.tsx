import { useBlotter } from '../state/stores';

const numericDetailLabels: Record<string, string> = {
  dropped: 'Dropped',
  dropped_messages: 'Dropped messages',
  client_dropped_messages: 'Client dropped messages',
  total_dropped_messages: 'Total dropped messages',
};

export function EventBlotter() {
  const { events } = useBlotter();
  return (
    <section className="panel blotter-panel">
      <div className="panel-header compact"><span className="eyebrow">Event blotter</span><h2>Runtime Events</h2></div>
      <div className="blotter-table">
        {events.length === 0 ? <p className="empty-text">Waiting for API status and WebSocket events.</p> : events.slice(0, 80).map((event) => (
          <details className="blotter-row" key={event.id}>
            <summary>
              <time>{new Date(event.timeUtc).toLocaleTimeString()}</time>
              <span className={`category ${event.severity}`}>{event.category}</span>
              <strong>{event.message}</strong>
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
        ))}
      </div>
    </section>
  );
}
