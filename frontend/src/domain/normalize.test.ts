import { describe, expect, it } from 'vitest';
import { normalizeBar, normalizeLevel, normalizeRuntimeStatus, normalizeWarning } from './normalize';
import type { RuntimeStatusDTO } from '../api/types';
import type { BarDTO, DisplayLevelDTO } from '../realtime/types';

describe('normalizeRuntimeStatus', () => {
  it('keeps API DTOs out of component-facing runtime shape', () => {
    const dto: RuntimeStatusDTO = {
      service: 'trade-lab-backend',
      version: '0.1.0',
      runtime_mode: 'idle',
      requested_symbol: 'NQ.c.0',
      instrument_root: 'NQ',
      supported_tick_timeframes: [147, 987, 2000, 5],
      engine_ready: true,
      feed_ready: false,
      feed_state: 'disconnected',
      replay: {
        state: 'idle',
        events_processed: 0,
        warnings_recorded: 0,
        last_event_ts_utc: null,
        last_error: null,
        requested_symbol: null,
        schema: null,
      },
      live: {
        state: 'idle',
        requested_symbol: 'NQ.c.0',
        dataset: 'GLBX.MDP3',
        schemas: ['trades'],
        api_key_configured: false,
        enabled: false,
        events_processed: 0,
        last_event_ts_utc: null,
        last_error: null,
        started_at_utc: null,
        stopped_at_utc: null,
      },
    };

    expect(normalizeRuntimeStatus(dto)).toMatchObject({
      apiOnline: true,
      requestedSymbol: 'NQ.c.0',
      supportedTimeframes: [147, 987, 2000],
      replayState: 'idle',
    });
  });
});

describe('market DTO normalization', () => {
  it('normalizes tick bar payloads for chart state', () => {
    const dto: BarDTO = {
      timeframe_ticks: 987,
      trading_day: '2026-05-21',
      bar_index: 12,
      bar_id: '987t:2026-05-21:12',
      open_ts_utc: '2026-05-21T14:00:00Z',
      close_ts_utc: '2026-05-21T14:01:00Z',
      open_ticks: 76000,
      high_ticks: 76012,
      low_ticks: 75996,
      close_ticks: 76008,
      volume: 123,
      trade_count: 45,
      is_complete: false,
      is_partial: true,
      close_reason: null,
    };

    expect(normalizeBar(dto)).toEqual({
      timeframe: 987,
      tradingDay: '2026-05-21',
      barIndex: 12,
      barId: '987t:2026-05-21:12',
      openTimeUtc: '2026-05-21T14:00:00Z',
      closeTimeUtc: '2026-05-21T14:01:00Z',
      openTicks: 76000,
      highTicks: 76012,
      lowTicks: 75996,
      closeTicks: 76008,
      volume: 123,
      tradeCount: 45,
      complete: false,
    });
  });

  it('preserves display-only and eligible level flags', () => {
    const displayOnly: DisplayLevelDTO = { kind: 'asia_high', price_ticks: 76100, trading_day: '2026-05-21', origin_session: 'asia', is_developing: true, is_eligible: false };
    const eligible: DisplayLevelDTO = { ...displayOnly, kind: 'pdh', is_developing: false, is_eligible: true };

    expect(normalizeLevel(displayOnly)).toMatchObject({ kind: 'asia_high', developing: true, eligible: false });
    expect(normalizeLevel(eligible)).toMatchObject({ kind: 'pdh', developing: false, eligible: true });
  });

  it('normalizes warnings with only allowlisted safe metadata', () => {
    const warning = normalizeWarning({
      code: 'gap',
      message: 'Gap detected',
      severity: 'warning',
      source: null,
      event_ts_utc: null,
      metadata: { public: true, schema: 'mbp-1', detail: 'safe provider detail', dropped: 2, token: 'secret', path: 'C:\\secret\\file' },
    });

    expect(warning).toEqual({ code: 'gap', message: 'Gap detected', severity: 'warning', source: null, timeUtc: null, metadata: { schema: 'mbp-1', detail: 'safe provider detail', dropped: 2 } });
    expect(JSON.stringify(warning).toLowerCase()).not.toMatch(/api[_-]?key|secret|token|password|credential/);
  });

  it('drops allowlisted string metadata when values look like secrets or paths', () => {
    const warning = normalizeWarning({
      code: 'provider_error',
      message: 'Databento provider reported an error',
      severity: 'warning',
      source: 'databento',
      event_ts_utc: '2026-05-21T14:00:00Z',
      metadata: {
        schema: '18',
        detail: 'api_key=db-secret path=C:\\Users\\operator\\secret.txt',
        dropped_messages: Number.NaN,
        total_dropped_messages: 4,
        credentials: 'db-secret',
      },
    });

    expect(warning.metadata).toEqual({ schema: '18', total_dropped_messages: 4 });
    expect(JSON.stringify(warning)).not.toContain('db-secret');
    expect(JSON.stringify(warning)).not.toContain('C:\\Users');
  });
});
