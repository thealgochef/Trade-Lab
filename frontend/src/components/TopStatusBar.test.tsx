import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { TopStatusBar } from './TopStatusBar';
import { connectionStore, marketStore, runtimeStore } from '../state/stores';

describe('TopStatusBar', () => {
  beforeEach(() => {
    runtimeStore.reset();
    connectionStore.reset();
    marketStore.reset();
  });

  afterEach(() => cleanup());

  it('shows the real runtime session and trading day when present', () => {
    runtimeStore.setState({ session: 'london', tradingDay: '2026-05-21' });
    render(<TopStatusBar />);

    expect(screen.getByText('london')).toBeInTheDocument();
    expect(screen.getByText('2026-05-21')).toBeInTheDocument();
  });

  it('falls back to placeholders when session and trading day are absent', () => {
    render(<TopStatusBar />);

    expect(screen.getByText('unavailable')).toBeInTheDocument();
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(1);
  });
});
