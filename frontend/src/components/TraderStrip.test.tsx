import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen } from '@testing-library/react';
import { TraderStrip } from './TraderStrip';
import { connectionStore, marketStore, runtimeStore } from '../state/stores';
import type { MarketBar } from '../domain/models';

const bar = (overrides: Partial<MarketBar>): MarketBar => ({
  timeframe: 147,
  tradingDay: '2026-07-10',
  barIndex: 1,
  barId: null,
  openTimeUtc: '2026-07-10T13:30:00Z',
  closeTimeUtc: '2026-07-10T13:31:00Z',
  openTicks: 96_000,
  highTicks: 96_040,
  lowTicks: 95_980,
  closeTicks: 96_020,
  volume: 10,
  tradeCount: 10,
  complete: true,
  ...overrides,
});

describe('TraderStrip', () => {
  beforeEach(() => {
    runtimeStore.reset();
    connectionStore.reset();
    marketStore.reset();
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-10T13:00:00Z')); // 09:00 EDT
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('shows the runtime session and trading day pills when present', () => {
    runtimeStore.setState({ session: 'london', tradingDay: '2026-05-21' });
    render(<TraderStrip />);

    expect(screen.getByText('london')).toBeInTheDocument();
    expect(screen.getByText('2026-05-21')).toBeInTheDocument();
  });

  it('renders em-dashes before any warm bars exist', () => {
    render(<TraderStrip />);

    expect(screen.getByText('unavailable')).toBeInTheDocument();
    expect(screen.getByTestId('strip-last-price')).toHaveTextContent('—');
    expect(screen.getByTestId('strip-net-change')).toHaveTextContent('—');
    expect(screen.getByTestId('strip-day-high-low')).toHaveTextContent('—');
  });

  it('derives price, net change, and day range from the bar streams', () => {
    runtimeStore.setState({ tradingDay: '2026-07-10' });
    marketStore.setState({
      currentBars: [bar({ complete: false, closeTicks: 96_100, closeTimeUtc: '2026-07-10T14:00:05Z', highTicks: 96_120 })],
      recentClosedBars: [
        bar({ barIndex: 0, openTicks: 95_900, closeTimeUtc: '2026-07-10T13:32:00Z' }),
        bar({ barIndex: 1, highTicks: 96_200, lowTicks: 95_800, closeTimeUtc: '2026-07-10T13:40:00Z' }),
      ],
    });
    render(<TraderStrip />);

    expect(screen.getByTestId('strip-last-price')).toHaveTextContent('24,025.00');
    // (96_100 - 95_900) ticks * 0.25 = +50.00 points
    expect(screen.getByTestId('strip-net-change')).toHaveTextContent('+50.00');
    expect(screen.getByTestId('strip-day-high-low')).toHaveTextContent('24,050.00 / 23,950.00');
  });

  it('keeps net change an em-dash when the day first bar is truncated away', () => {
    runtimeStore.setState({ tradingDay: '2026-07-10' });
    marketStore.setState({
      currentBars: [],
      recentClosedBars: [bar({ barIndex: 7, closeTicks: 96_100 })],
    });
    render(<TraderStrip />);

    expect(screen.getByTestId('strip-last-price')).toHaveTextContent('24,025.00');
    expect(screen.getByTestId('strip-net-change')).toHaveTextContent('—');
  });

  it('flashes tick direction on price changes', () => {
    runtimeStore.setState({ tradingDay: '2026-07-10' });
    marketStore.setState({
      currentBars: [bar({ complete: false, closeTicks: 96_000, closeTimeUtc: '2026-07-10T14:00:05Z' })],
      recentClosedBars: [],
    });
    render(<TraderStrip />);
    expect(screen.getByTestId('strip-last-price').className).not.toMatch(/flash/);

    act(() => {
      marketStore.setState({
        currentBars: [bar({ complete: false, closeTicks: 96_004, closeTimeUtc: '2026-07-10T14:00:06Z' })],
        recentClosedBars: [],
      });
    });
    expect(screen.getByTestId('strip-last-price').className).toMatch(/\bup\b/);
    expect(screen.getByTestId('strip-last-price').className).toMatch(/flash-up/);

    act(() => {
      marketStore.setState({
        currentBars: [bar({ complete: false, closeTicks: 95_996, closeTimeUtc: '2026-07-10T14:00:07Z' })],
        recentClosedBars: [],
      });
    });
    expect(screen.getByTestId('strip-last-price').className).toMatch(/\bdown\b/);
    expect(screen.getByTestId('strip-last-price').className).toMatch(/flash-down/);
  });

  it('counts down to the NY open and flatten on America/New_York wall time', () => {
    render(<TraderStrip />);

    // 09:00 EDT → 30 minutes to the 09:30 open, 7h40m to the 16:40 flatten.
    expect(screen.getByText('NY open 09:30')).toBeInTheDocument();
    expect(screen.getByText(/in 0:30:00/)).toBeInTheDocument();
    expect(screen.getByText('Flatten 16:40')).toBeInTheDocument();
    expect(screen.getByText(/in 7:40:00/)).toBeInTheDocument();
  });

  it('switches to since after a target passes and ticks with the clock', () => {
    vi.setSystemTime(new Date('2026-07-10T21:00:00Z')); // 17:00 EDT
    render(<TraderStrip />);

    expect(screen.getByText(/since 7:30:00/)).toBeInTheDocument(); // NY open
    expect(screen.getByText(/since 0:20:00/)).toBeInTheDocument(); // flatten

    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(screen.getByText(/since 0:21:00/)).toBeInTheDocument();
  });
});
