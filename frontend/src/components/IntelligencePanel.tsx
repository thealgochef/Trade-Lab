import { useIntelligence, usePredictions, useRuntime } from '../state/stores';
import type { Prediction } from '../domain/models';

const LEVEL_ORDER = ['pdh', 'pdl', 'asia_high', 'asia_low', 'london_high', 'london_low', 'ny_high', 'ny_low'];
const price = (ticks: number) => (ticks / 4).toFixed(2);
const pct = (value: number) => `${(value * 100).toFixed(0)}%`;

export function IntelligencePanel() {
  const runtime = useRuntime();
  const { levels, touches, observations, warnings } = useIntelligence();
  const predictions = usePredictions();
  const orderedLevels = [...levels].sort((a, b) => LEVEL_ORDER.indexOf(a.kind) - LEVEL_ORDER.indexOf(b.kind));

  return (
    <aside className="panel intelligence-panel">
      <div className="panel-header compact"><span className="eyebrow">Intelligence</span><h2>Market Structure</h2></div>
      <Section title="Runtime">
        <KeyValue label="Session" value={runtime.session ?? 'unavailable'} />
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
      <Section title="Predictions">
        {predictions.length === 0 ? <Empty text="No predictions yet." /> : predictions.slice(0, 8).map((prediction) => <PredictionRow key={prediction.id} prediction={prediction} />)}
      </Section>
      <Section title="Touches">
        {touches.length === 0 ? <Empty text="No touches detected." /> : touches.slice(0, 6).map((touch) => <KeyValue key={touch.id} label={touch.levelKind} value={`${price(touch.priceTicks)} · ${touch.session}`} />)}
      </Section>
      <Section title="Observations">
        {observations.length === 0 ? <Empty text="No active observations." /> : observations.slice(0, 6).map((obs) => <KeyValue key={obs.id} label={obs.levelKind} value={obs.status} />)}
      </Section>
      <Section title="Data Quality">
        {warnings.length === 0 ? <Empty text="No warnings." /> : warnings.slice(0, 5).map((warning, index) => <KeyValue key={`${warning.code}-${warning.timeUtc ?? 'na'}-${index}`} label={warning.severity} value={warning.message} />)}
      </Section>
    </aside>
  );
}

function PredictionRow({ prediction }: { prediction: Prediction }) {
  const probEntries = Object.entries(prediction.probabilities).sort(([, a], [, b]) => b - a);
  const outcome = prediction.outcome;
  return (
    <div className="intel-prediction">
      <div className="intel-prediction-head">
        <strong>{prediction.predictedClass}</strong>
        <span className={`intel-badge ${prediction.eligible ? 'eligible' : 'ineligible'}`}>{prediction.eligible ? 'eligible' : 'ineligible'}</span>
        <span className="intel-badge">{prediction.direction}</span>
      </div>
      <div className="intel-probs">
        {probEntries.length === 0 ? <span>no probabilities</span> : probEntries.map(([label, value]) => <span key={label}>{label} {pct(value)}</span>)}
      </div>
      <div className="intel-prediction-meta">{prediction.levelKind.replaceAll('_', ' ')} @ {price(prediction.levelPriceTicks)} · {prediction.session}</div>
      {outcome && (
        <div className="intel-outcome">
          <span className={`intel-badge ${outcome.correct ? 'correct' : 'incorrect'}`}>{outcome.correct ? 'correct' : 'incorrect'}</span>
          <span>actual {outcome.actualClass}</span>
          <span>MFE {outcome.maxMfePts.toFixed(2)} pts</span>
          <span>MAE {outcome.maxMaePts.toFixed(2)} pts</span>
          <span>{outcome.resolutionType.replaceAll('_', ' ')}</span>
        </div>
      )}
    </div>
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
