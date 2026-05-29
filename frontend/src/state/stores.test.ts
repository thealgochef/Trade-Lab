import { afterEach, describe, expect, it } from 'vitest';
import { addBlotterEvent, blotterStore, connectionStore, intelligenceStore, marketStore, runtimeStore } from './stores';

const resetStores = () => {
  runtimeStore.reset();
  connectionStore.reset();
  marketStore.reset();
  intelligenceStore.reset();
  blotterStore.reset();
};

describe('workstation stores', () => {
  afterEach(resetStores);

  it('keeps runtime, connection, market, intelligence, and blotter slices independent', () => {
    runtimeStore.setState({ apiOnline: true, feedReady: true });
    connectionStore.setState({ wsStatus: 'connected', lastSequence: 10 });
    marketStore.setState({ selectedTimeframe: 2000 });
    intelligenceStore.setState({ levels: [{ kind: 'pdh', priceTicks: 76000, tradingDay: '2026-05-21', originSession: 'ny', developing: false, eligible: true }] });

    expect(runtimeStore.getSnapshot()).toMatchObject({ apiOnline: true, feedReady: true });
    expect(connectionStore.getSnapshot()).toMatchObject({ wsStatus: 'connected', lastSequence: 10 });
    expect(marketStore.getSnapshot()).toMatchObject({ selectedTimeframe: 2000, currentBars: [] });
    expect(intelligenceStore.getSnapshot().levels).toHaveLength(1);
    expect(blotterStore.getSnapshot().events).toEqual([]);
  });

  it('bounds event blotter retention to the newest 200 events', () => {
    for (let index = 0; index < 205; index += 1) {
      addBlotterEvent({ timeUtc: `2026-05-21T14:${String(index).padStart(2, '0')}:00Z`, category: 'system', severity: 'info', message: `event-${index}` });
    }

    const events = blotterStore.getSnapshot().events;
    expect(events).toHaveLength(200);
    expect(events[0].message).toBe('event-204');
    expect(events.at(-1)?.message).toBe('event-5');
  });

  it('assigns unique IDs to repeated identical blotter events', () => {
    const event = { timeUtc: '2026-05-21T14:00:00Z', category: 'system' as const, severity: 'info' as const, message: 'Heartbeat', sequence: 10 };

    addBlotterEvent(event);
    addBlotterEvent(event);

    const ids = blotterStore.getSnapshot().events.map((entry) => entry.id);
    expect(new Set(ids).size).toBe(2);
    expect(ids.every((id) => id.startsWith('ws-10-'))).toBe(true);
  });

  it('stores backend offline state without requiring chart or intelligence data', () => {
    runtimeStore.setState((current) => ({ ...current, apiOnline: false, feedReady: false, lastError: 'Backend unavailable: connect ECONNREFUSED' }));
    connectionStore.setState({ wsStatus: 'offline', error: 'socket closed' });

    expect(runtimeStore.getSnapshot()).toMatchObject({ apiOnline: false, feedReady: false, lastError: expect.stringContaining('Backend unavailable') });
    expect(connectionStore.getSnapshot()).toMatchObject({ wsStatus: 'offline', error: 'socket closed' });
    expect(marketStore.getSnapshot().currentBars).toEqual([]);
    expect(intelligenceStore.getSnapshot().warnings).toEqual([]);
  });
});
