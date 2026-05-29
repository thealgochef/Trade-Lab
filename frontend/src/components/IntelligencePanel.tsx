import { useIntelligence, useRuntime } from '../state/stores';

const LEVEL_ORDER = ['pdh', 'pdl', 'asia_high', 'asia_low', 'london_high', 'london_low', 'ny_high', 'ny_low'];
const price = (ticks: number) => (ticks / 4).toFixed(2);

export function IntelligencePanel() {
  const runtime = useRuntime();
  const { levels, touches, observations, warnings } = useIntelligence();
  const orderedLevels = [...levels].sort((a, b) => LEVEL_ORDER.indexOf(a.kind) - LEVEL_ORDER.indexOf(b.kind));

  return (
    <aside className="panel intelligence-panel">
      <div className="panel-header compact"><span className="eyebrow">Intelligence</span><h2>Market Structure</h2></div>
      <Section title="Runtime">
        <KeyValue label="Session" value="unavailable" />
        <KeyValue label="Level origin" value={levels[0]?.originSession ?? 'unknown'} />
        <KeyValue label="Trading day" value={runtime.tradingDay ?? levels[0]?.tradingDay ?? '—'} />
        <KeyValue label="Eligibility" value={runtime.engineReady ? 'engine ready' : 'engine offline'} />
      </Section>
      <Section title="Levels">
        {orderedLevels.length === 0 ? <Empty text="No display levels in snapshot." /> : orderedLevels.map((level) => (
          <div className="level-row" key={`${level.kind}-${level.priceTicks}`}>
            <span>{level.kind.replaceAll('_', ' ')}</span>
            <strong>{price(level.priceTicks)}</strong>
            <em className={level.eligible ? 'eligible' : ''}>{level.eligible ? 'eligible' : 'display'}</em>
          </div>
        ))}
      </Section>
      <Section title="Touches">
        {touches.length === 0 ? <Empty text="No touches detected." /> : touches.slice(0, 6).map((touch) => <KeyValue key={touch.id} label={touch.levelKind} value={`${price(touch.priceTicks)} · ${touch.session}`} />)}
      </Section>
      <Section title="Observations">
        {observations.length === 0 ? <Empty text="No active observations." /> : observations.slice(0, 6).map((obs) => <KeyValue key={obs.id} label={obs.levelKind} value={obs.status} />)}
      </Section>
      <Section title="Data Quality">
        {warnings.length === 0 ? <Empty text="No warnings." /> : warnings.slice(0, 5).map((warning) => <KeyValue key={`${warning.code}-${warning.timeUtc}`} label={warning.severity} value={warning.message} />)}
      </Section>
    </aside>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="intel-section"><h3>{title}</h3>{children}</section>;
}

function KeyValue({ label, value }: { label: string; value: string }) {
  return <div className="key-value"><span>{label}</span><strong>{value}</strong></div>;
}

function Empty({ text }: { text: string }) {
  return <p className="empty-text">{text}</p>;
}
