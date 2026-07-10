// COCKPIT P2: the persistent trader strip. Price first — large last print with
// tick-direction flash, session net change, day high/low, NY-open and flatten
// countdowns on America/New_York wall time — with the ops pills compacted to
// the right.
import { useEffect, useRef, useState } from 'react';
import { useConnection, useMarket, useRuntime } from '../state/stores';
import {
  NY_FLATTEN,
  NY_OPEN,
  dayFirstOpenTicks,
  dayHighLowTicks,
  formatCountdown,
  formatSignedPoints,
  formatStripPrice,
  latestPrint,
  nyCountdown,
  stripTicksToPrice,
} from '../strip/viewModels';

const EM_DASH = '—';

const fmt = (value: string | null) => (value ? new Date(value).toLocaleTimeString() : EM_DASH);

export function TraderStrip() {
  const runtime = useRuntime();
  const connection = useConnection();
  const currentBars = useMarket((state) => state.currentBars);
  const recentClosedBars = useMarket((state) => state.recentClosedBars);
  const selectedTimeframe = useMarket((state) => state.selectedTimeframe);
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const interval = window.setInterval(() => setNowMs(Date.now()), 1_000);
    return () => window.clearInterval(interval);
  }, []);

  const print = latestPrint(currentBars, recentClosedBars);
  const firstOpenTicks = dayFirstOpenTicks(currentBars, recentClosedBars, runtime.tradingDay);
  const highLow = dayHighLowTicks(currentBars, recentClosedBars, runtime.tradingDay);

  // Tick direction: compare successive rendered prints; remount the price node
  // (via key) so the flash animation restarts on every change.
  const prevTicksRef = useRef<number | null>(null);
  const flashSeqRef = useRef(0);
  const directionRef = useRef<'up' | 'down' | null>(null);
  if (print !== null && prevTicksRef.current !== null && print.priceTicks !== prevTicksRef.current) {
    directionRef.current = print.priceTicks > prevTicksRef.current ? 'up' : 'down';
    flashSeqRef.current += 1;
  }
  if (print !== null) prevTicksRef.current = print.priceTicks;
  const direction = directionRef.current;

  const netChange =
    print !== null && firstOpenTicks !== null
      ? stripTicksToPrice(print.priceTicks - firstOpenTicks)
      : null;

  const open = nyCountdown(nowMs, NY_OPEN);
  const flatten = nyCountdown(nowMs, NY_FLATTEN);

  const tradingDay = runtime.tradingDay ?? EM_DASH;
  const session = runtime.session ?? 'unavailable';

  return (
    <header className="top-bar trader-strip">
      <div className="brand-block">
        <span className="eyebrow">Trade-Lab</span>
        <strong>NQ / {runtime.requestedSymbol}</strong>
      </div>
      <div className="strip-price-block" aria-label="Last price">
        <span
          key={flashSeqRef.current}
          className={`strip-price${direction ? ` ${direction} flash-${direction}` : ''}`}
          data-testid="strip-last-price"
        >
          {print !== null ? formatStripPrice(print.priceTicks) : EM_DASH}
        </span>
        <div className="strip-metric" aria-label="Session net change">
          <span>Net</span>
          <strong className={netChange === null ? '' : netChange >= 0 ? 'pos' : 'neg'} data-testid="strip-net-change">
            {netChange === null ? EM_DASH : formatSignedPoints(netChange)}
          </strong>
        </div>
        <div className="strip-metric" aria-label="Day high and low">
          <span>Day H / L</span>
          <strong data-testid="strip-day-high-low">
            {highLow
              ? `${formatStripPrice(highLow.highTicks)} / ${formatStripPrice(highLow.lowTicks)}`
              : EM_DASH}
          </strong>
        </div>
      </div>
      <div className="strip-countdowns">
        <StripCountdown label="NY open 09:30" countdown={open} />
        <StripCountdown label="Flatten 16:40" countdown={flatten} sinceTone="red" />
      </div>
      <div className="strip-pills">
        <StatusPill label="Mode" value={runtime.runtimeMode} tone={runtime.runtimeMode === 'replay' ? 'amber' : 'blue'} />
        <StatusPill label="Session" value={session} tone={runtime.session ? 'blue' : 'neutral'} />
        <StatusPill label="Trading Day" value={tradingDay} />
        <StatusPill label="Feed" value={runtime.feedState} tone={runtime.feedReady ? 'green' : 'red'} />
        <StatusPill label="API" value={runtime.apiOnline ? 'online' : 'offline'} tone={runtime.apiOnline ? 'green' : 'red'} />
        <StatusPill label="WS" value={connection.wsStatus} tone={connection.wsStatus === 'connected' ? 'green' : 'amber'} />
        <StatusPill label="Heartbeat" value={fmt(connection.lastHeartbeatUtc)} />
        <StatusPill label="Timeframe" value={`${selectedTimeframe}t`} tone="blue" />
      </div>
    </header>
  );
}

function StripCountdown({
  label,
  countdown,
  sinceTone = 'amber',
}: {
  label: string;
  countdown: { mode: 'until' | 'since'; seconds: number };
  sinceTone?: 'amber' | 'red';
}) {
  const tone = countdown.mode === 'until' ? 'until' : sinceTone;
  return (
    <div className={`strip-countdown ${tone}`}>
      <span>{label}</span>
      <strong>
        {countdown.mode === 'until' ? 'in ' : 'since '}
        {formatCountdown(countdown.seconds)}
      </strong>
    </div>
  );
}

function StatusPill({ label, value, tone = 'neutral' }: { label: string; value: string; tone?: 'neutral' | 'green' | 'red' | 'amber' | 'blue' }) {
  return (
    <div className={`status-pill ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
