import { useEffect, useState } from 'react';
import { apiClient } from '../api/client';
import { normalizeModelBundle, normalizeModelStatus } from '../domain/normalize';
import { addBlotterEvent, setModelBundles, setModelStatus, useBundles, useModelStatus, useRuntime } from '../state/stores';
import type { ModelBundle, ModelStatus } from '../domain/models';

export function ModelPanel() {
  const bundles = useBundles();
  const modelStatus = useModelStatus();
  const apiOnline = useRuntime((state) => state.apiOnline);
  const [selectedModelId, setSelectedModelId] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void refreshModels(setSelectedModelId, setError);
  }, []);

  // Keep the dropdown selection valid as the bundle list changes; default to the
  // active model when one is loaded, otherwise the first discovered bundle.
  useEffect(() => {
    if (bundles.length === 0) return;
    setSelectedModelId((current) => {
      if (bundles.some((bundle) => bundle.modelId === current)) return current;
      const active = modelStatus?.loaded ? modelStatus.modelId : null;
      return (active && bundles.some((bundle) => bundle.modelId === active) ? active : bundles[0].modelId);
    });
  }, [bundles, modelStatus]);

  const selectedBundle = bundles.find((bundle) => bundle.modelId === selectedModelId) ?? null;
  const canActivate = apiOnline && !loading && Boolean(selectedBundle);
  const canDeactivate = apiOnline && !loading && Boolean(modelStatus?.loaded);

  const onActivate = async () => {
    if (!selectedBundle) return;
    setLoading(true);
    setError(null);
    const result = await apiClient.activateModel(selectedBundle.modelId);
    setLoading(false);
    if (result.ok) {
      setModelStatus(normalizeModelStatus(result.data));
      addBlotterEvent({ timeUtc: new Date().toISOString(), category: 'system', severity: 'info', message: `Model activated: ${selectedBundle.strategyId}` });
      return;
    }
    setError(result.error);
    addBlotterEvent({ timeUtc: new Date().toISOString(), category: 'system', severity: 'error', message: `Model activation failed: ${result.error}` });
  };

  const onDeactivate = async () => {
    setLoading(true);
    setError(null);
    const result = await apiClient.deactivateModel();
    setLoading(false);
    if (result.ok) {
      setModelStatus(normalizeModelStatus(result.data));
      addBlotterEvent({ timeUtc: new Date().toISOString(), category: 'system', severity: 'info', message: 'Model deactivated' });
      return;
    }
    setError(result.error);
    addBlotterEvent({ timeUtc: new Date().toISOString(), category: 'system', severity: 'error', message: `Model deactivation failed: ${result.error}` });
  };

  return (
    <section className="panel model-panel" aria-label="Inference model picker">
      <div className="model-heading">
        <div>
          <span className="eyebrow">Inference model</span>
          <h2>Model Hot-Swap</h2>
        </div>
        <div className={`replay-state ${modelStatus?.loaded ? 'completed' : 'stopped'}`}>{modelStatus?.loaded ? 'active' : 'inactive'}</div>
      </div>
      <div className="model-controls-grid">
        <label className="model-source-select">
          <span>Discovered bundle</span>
          <select
            value={selectedModelId}
            disabled={!apiOnline || loading || bundles.length === 0}
            onChange={(event) => setSelectedModelId(event.target.value)}
          >
            {bundles.length === 0 && <option value="">No bundles discovered</option>}
            {bundles.map((bundle) => (
              <option key={bundle.modelId} value={bundle.modelId}>{bundleLabel(bundle)}</option>
            ))}
          </select>
        </label>
        {selectedBundle && (
          <div className="model-bundle-meta">
            <Metric label="Training mode" value={selectedBundle.trainingMode || '—'} />
            <Metric label="Features" value={String(selectedBundle.featureCount)} />
            <Metric label="Classes" value={formatClassMap(selectedBundle.classMap)} />
            <Metric label="Validation" value={selectedBundle.validationOk ? 'ok' : 'rejected'} />
          </div>
        )}
      </div>
      <div className="model-note">
        Activation is operator-gated server-side; on localhost the loopback path authorizes. No credentials or filesystem locations are entered here.
        <div className="model-hint">Rerun = activate a model, then start a replay.</div>
      </div>
      {error && <div className="replay-error" role="status">{error}</div>}
      {!apiOnline && <div className="replay-error" role="status">Backend offline: model controls are disabled.</div>}
      <ActiveModelSummary status={modelStatus} />
      <div className="replay-actions">
        <button disabled={!canActivate} onClick={() => void onActivate()}>Activate</button>
        <button disabled={!canDeactivate} onClick={() => void onDeactivate()}>Deactivate</button>
      </div>
    </section>
  );
}

function ActiveModelSummary({ status }: { status: ModelStatus | null }) {
  if (!status || !status.loaded) {
    return <div className="model-active-summary" role="status"><span className="empty-text">No active model. Inference is idle; market data still streams.</span></div>;
  }
  return (
    <div className="model-active-summary">
      <h3>Active model</h3>
      <Metric label="Strategy" value={status.strategyId ?? '—'} />
      <Metric label="Instrument" value={status.instrument ?? '—'} />
      <Metric label="Classes" value={formatClassMap(status.classMap)} />
      <Metric label="Validation" value={status.validationOk ? 'ok' : (status.validationDetail ?? 'rejected')} />
      <div className="model-feature-list">
        <span>Features ({status.featureNames.length})</span>
        <strong>{status.featureNames.length > 0 ? status.featureNames.join(', ') : '—'}</strong>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="replay-metric"><span>{label}</span><strong>{value}</strong></div>;
}

async function refreshModels(
  setSelectedModelId: (updater: (current: string) => string) => void,
  setError: (error: string | null) => void,
) {
  const [models, active] = await Promise.all([apiClient.listModels(), apiClient.activeModel()]);
  if (models.ok) {
    const bundles = models.data.models.map(normalizeModelBundle);
    setModelBundles(bundles);
    setSelectedModelId((current) => (bundles.some((bundle) => bundle.modelId === current) ? current : (bundles[0]?.modelId ?? '')));
  }
  if (active.ok) setModelStatus(normalizeModelStatus(active.data));
  const error = !models.ok ? models.error : !active.ok ? active.error : null;
  if (error) setError(error);
}

const bundleLabel = (bundle: ModelBundle) => {
  const validity = bundle.validationOk ? 'ok' : 'rejected';
  return `${bundle.modelId} / ${bundle.strategyId} · ${bundle.trainingMode} · ${bundle.featureCount}f · ${formatClassMap(bundle.classMap)} · ${validity}`;
};

const formatClassMap = (classMap: Record<string, string>) => {
  const labels = Object.entries(classMap)
    .sort(([a], [b]) => Number(a) - Number(b))
    .map(([, label]) => label);
  return labels.length > 0 ? labels.join('/') : '—';
};
