import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { EventBlotter } from './EventBlotter';
import { blotterStore } from '../state/stores';

describe('EventBlotter', () => {
  beforeEach(() => {
    blotterStore.reset();
  });

  afterEach(() => {
    cleanup();
    blotterStore.reset();
  });

  it('renders compact warning rows with expandable safe provider details only', () => {
    blotterStore.setState({
      events: [{
        id: 'event-1',
        timeUtc: '2026-05-21T14:03:00Z',
        category: 'warning',
        severity: 'warning',
        message: 'Databento provider reported an error',
        code: 'provider_error',
        source: 'databento',
        details: {
          schema: 'mbp-1',
          detail: 'code=bad_request; message=<redacted> path=<path>',
          dropped: 2,
          token: 'db-secret',
          raw_record: { api_key: 'db-secret' },
        } as never,
      }],
    });

    render(<EventBlotter />);

    const row = screen.getByText('Databento provider reported an error').closest('details');
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getAllByText('provider_error').length).toBeGreaterThanOrEqual(1);

    fireEvent.click(within(row as HTMLElement).getByText('Databento provider reported an error'));

    expect(within(row as HTMLElement).getByText('Timestamp')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('2026-05-21T14:03:00Z')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('Code')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('Source')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('Schema')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('Detail')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('mbp-1')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('code=bad_request; message=<redacted> path=<path>')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('Dropped')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('2')).toBeInTheDocument();
    expect(row as HTMLElement).not.toHaveTextContent('token');
    expect(row as HTMLElement).not.toHaveTextContent('raw_record');
    expect(row as HTMLElement).not.toHaveTextContent('db-secret');
    expect(row as HTMLElement).not.toHaveTextContent('[object Object]');
  });
});
