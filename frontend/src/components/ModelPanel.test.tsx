import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ModelPanel } from './ModelPanel';
import { blotterStore, predictionStore, runtimeStore } from '../state/stores';
import type { ModelBundleDTO } from '../api/types';
import type { ModelStatusDTO } from '../realtime/types';

const mocks = vi.hoisted(() => ({
  listModels: vi.fn(),
  activeModel: vi.fn(),
  activateModel: vi.fn(),
  deactivateModel: vi.fn(),
}));

vi.mock('../api/client', () => ({ apiClient: mocks }));

const bundle = (overrides: Partial<ModelBundleDTO> = {}): ModelBundleDTO => ({
  model_id: 'model-alpha',
  strategy_id: 'NQ_pdh_revert',
  training_mode: 'walk_forward',
  instrument: 'NQ',
  feature_count: 12,
  class_map: { 0: 'down', 1: 'hold', 2: 'up' },
  has_checksum: true,
  validation_ok: true,
  validation_detail: 'ok',
  ...overrides,
});

const inactiveStatus: ModelStatusDTO = {
  loaded: false,
  model_id: null,
  strategy_id: null,
  training_mode: null,
  instrument: null,
  feature_names: [],
  class_map: {},
  validation_ok: false,
  validation_detail: null,
};

const activeStatus: ModelStatusDTO = {
  loaded: true,
  model_id: 'model-alpha',
  strategy_id: 'NQ_pdh_revert',
  training_mode: 'walk_forward',
  instrument: 'NQ',
  feature_names: ['dist_to_level', 'atr_pct'],
  class_map: { 0: 'down', 1: 'hold', 2: 'up' },
  validation_ok: true,
  validation_detail: 'ok',
};

describe('ModelPanel', () => {
  beforeEach(() => {
    predictionStore.reset();
    runtimeStore.reset();
    blotterStore.reset();
    runtimeStore.setState({ apiOnline: true });
    mocks.listModels.mockResolvedValue({ ok: true, data: { models: [bundle(), bundle({ model_id: 'model-beta', strategy_id: 'NQ_asia_breakout', validation_ok: false, validation_detail: 'feature mismatch' })] } });
    mocks.activeModel.mockResolvedValue({ ok: true, data: inactiveStatus });
    mocks.activateModel.mockResolvedValue({ ok: true, data: activeStatus });
    mocks.deactivateModel.mockResolvedValue({ ok: true, data: inactiveStatus });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('lists discovered bundles with mode, feature count, classes, and validation', async () => {
    render(<ModelPanel />);

    await waitFor(() => expect(mocks.listModels).toHaveBeenCalledOnce());
    const select = screen.getByRole('combobox') as HTMLSelectElement;
    expect(select.options).toHaveLength(2);
    expect(select.options[0].textContent).toContain('model-alpha / NQ_pdh_revert');
    expect(select.options[0].textContent).toContain('walk_forward');
    expect(select.options[0].textContent).toContain('12f');
    expect(select.options[0].textContent).toContain('down/hold/up');
    expect(select.options[1].textContent).toContain('rejected');
  });

  it('activates the selected model and reflects active status', async () => {
    render(<ModelPanel />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Activate' })).toBeEnabled());

    fireEvent.click(screen.getByRole('button', { name: 'Activate' }));

    await waitFor(() => expect(mocks.activateModel).toHaveBeenCalledWith('model-alpha'));
    expect(predictionStore.getSnapshot().modelStatus?.loaded).toBe(true);
    await waitFor(() => expect(screen.getByText('Active model')).toBeInTheDocument());
    expect(screen.getByText('NQ_pdh_revert')).toBeInTheDocument();
    expect(screen.getByText('dist_to_level, atr_pct')).toBeInTheDocument();
    expect(blotterStore.getSnapshot().events[0].message).toContain('Model activated');
  });

  it('deactivates the active model', async () => {
    mocks.activeModel.mockResolvedValueOnce({ ok: true, data: activeStatus });
    render(<ModelPanel />);
    await waitFor(() => expect(screen.getByText('Active model')).toBeInTheDocument());

    const deactivate = screen.getByRole('button', { name: 'Deactivate' });
    expect(deactivate).toBeEnabled();
    fireEvent.click(deactivate);

    await waitFor(() => expect(mocks.deactivateModel).toHaveBeenCalledOnce());
    expect(predictionStore.getSnapshot().modelStatus?.loaded).toBe(false);
  });

  it('shows the sanitized activation error message', async () => {
    mocks.activateModel.mockResolvedValueOnce({ ok: false, error: 'HTTP 409 from /api/v1/models/activate: bundle <path> validation failed' });
    render(<ModelPanel />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Activate' })).toBeEnabled());

    fireEvent.click(screen.getByRole('button', { name: 'Activate' }));

    await waitFor(() => expect(screen.getByText(/HTTP 409 from \/api\/v1\/models\/activate/)).toBeInTheDocument());
    expect(screen.getByText(/validation failed/)).toBeInTheDocument();
  });

  it('renders no API key or secret input fields', async () => {
    render(<ModelPanel />);
    await waitFor(() => expect(mocks.listModels).toHaveBeenCalledOnce());

    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/api key|secret|token|password|path/i)).not.toBeInTheDocument();
    expect(document.body.textContent?.toLowerCase()).not.toMatch(/api[_-]?key|secret|password/);
  });

  it('disables controls when the backend is offline', async () => {
    runtimeStore.setState({ apiOnline: false });
    render(<ModelPanel />);

    expect(screen.getByText(/Backend offline/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Activate' })).toBeDisabled());
    expect(screen.getByRole('button', { name: 'Deactivate' })).toBeDisabled();
  });
});
