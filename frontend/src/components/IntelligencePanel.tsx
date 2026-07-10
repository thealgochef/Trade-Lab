import { useIntelligence, useMarket, useModelStatus, usePredictions, useRuntime } from '../state/stores';
import type { ModelStatus, Prediction } from '../domain/models';
import { latestPrice } from '../execution/viewModels';
import {
  activeSetup,
  describeGateReason,
  formatLevelDistance,
  formatSetupCountdown,
  gateMath,
  levelOriginLabel,
  observationSecondsRemaining,
  sortLevelsByDistance,
} from '../intel/viewModels';

const price = (ticks: number) => (ticks / 4).toFixed(2);
const pct = (value: number) => `${(value * 100).toFixed(0)}%`;

export function IntelligencePanel() {
  const runtime = useRuntime();
  const { levels, touches, observations, warnings } = useIntelligence();
  const predictions = usePredictions();
  const modelStatus = useModelStatus();
  const currentBars = useMarket((state) => state.currentBars);
  const recentClosedBars = useMarket((state) => state.recentClosedBars);

  // COCKPIT P3a: levels sort by absolute distance from the last print; the
  // event clock (latest bar close) also drives the setup-card countdown.
  const last = latestPrice(currentBars, recentClosedBars);
  const lastPriceTicks = last === null ? null : Math.round(last.price * 4);
  const levelRows = sortLevelsByDistance(levels, lastPriceTicks);
  const setup = activeSetup(observations, touches);

  return (
    <aside className="panel intelligence-panel">
      <div className="panel-header compact"><span className="eyebrow">Intelligence</span><h2>Market Structure</h2></div>
      {setup && (
        <Section title={`Active Setup${setup.activeCount > 1 ? ` (${setup.activeCount} open)` : ''}`}>
          <div className="setup-card" data-testid="active-setup-card">
            <div className="setup-card-head">
              <strong>{setup.observation.levelKind.replaceAll('_', ' ')}</strong>
              {setup.observation.levelPriceTicks !== null && <span className="setup-level-price">@ {price(setup.observation.levelPriceTicks)}</span>}
              <span className={`intel-badge ${setup.observation.direction ?? ''}`}>{setup.observation.direction ?? 'direction —'}</span>
            </div>
            <div className="setup-card-meta">
              <span>Touch {setup.touchPriceTicks !== null ? price(setup.touchPriceTicks) : '—'}</span>
              <span>{setup.observation.session}</span>
              <span className="setup-countdown">
                ends in {formatSetupCountdown(observationSecondsRemaining(setup.observation.scheduledEndUtc, last?.timeUtc ?? null))}
              </span>
            </div>
          </div>
        </Section>
      )}
      <Section title="Runtime">
        <KeyValue label="Session" value={runtime.session ?? 'unavailable'} />
        <KeyValue label="Level origin" value={levelOriginLabel(levelRows[0]?.level)} />
        <KeyValue label="Trading day" value={runtime.tradingDay ?? levelRows[0]?.level.tradingDay ?? '—'} />
        <KeyValue label="Eligibility" value={runtime.engineReady ? 'engine ready' : 'engine offline'} />
      </Section>
      <Section title="Levels">
        {levelRows.length === 0 ? <Empty text="No display levels in snapshot." /> : levelRows.map(({ level, distanceTicks, nearest }) => (
          <div className={`level-row${nearest ? ' nearest' : ''}`} key={`${level.kind}-${level.priceTicks}`}>
            <span>{level.kind.replaceAll('_', ' ')}</span>
            <strong className={distanceTicks === null ? '' : distanceTicks >= 0 ? 'dist-above' : 'dist-below'}>
              {formatLevelDistance(distanceTicks)}
            </strong>
            <small className="level-price">{price(level.priceTicks)}</small>
            <em className={level.eligible ? 'eligible' : ''}>{level.eligible ? 'eligible' : 'display'}</em>
          </div>
        ))}
      </Section>
      <Section title="Predictions">
        {predictions.length === 0 ? <Empty text="No predictions yet." /> : predictions.slice(0, 8).map((prediction) => <PredictionRow key={prediction.id} prediction={prediction} modelStatus={modelStatus} />)}
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

function PredictionRow({ prediction, modelStatus }: { prediction: Prediction; modelStatus: ModelStatus | null }) {
  const probEntries = Object.entries(prediction.probabilities).sort(([, a], [, b]) => b - a);
  const outcome = prediction.outcome;
  const dropped = prediction.dropped;
  // COCKPIT P3b: the gate math — eligible-class probability vs confidence gate.
  const math = gateMath(prediction, modelStatus);
  const reason = math === null ? null : describeGateReason(math, prediction, modelStatus);
  return (
    <div className="intel-prediction">
      <div className="intel-prediction-head">
        <strong>{prediction.predictedClass}</strong>
        <span className={`intel-badge ${prediction.eligible ? 'eligible' : 'ineligible'}`}>{prediction.eligible ? 'eligible' : 'ineligible'}</span>
        <span className="intel-badge">{prediction.direction}</span>
      </div>
      {math !== null && (
        <div className="gate-math" data-testid="gate-math">
          <span className="gate-figures">
            {math.eligibleClass} {math.probability.toFixed(2)} / {math.gate.toFixed(2)}
          </span>
          <div className="gate-bar" role="presentation">
            <div className={`gate-fill ${math.passes ? 'pass' : 'fail'}`} style={{ width: `${Math.min(100, math.probability * 100).toFixed(1)}%` }} />
            <div className="gate-threshold" style={{ left: `${Math.min(100, math.gate * 100).toFixed(1)}%` }} />
          </div>
          {reason !== null && <span className="gate-reason">why: {reason}</span>}
        </div>
      )}
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
      {dropped && (
        <div className="intel-outcome">
          <span className="intel-badge dropped">dropped</span>
          <span>{dropped.reason.replaceAll('_', ' ')}</span>
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
