import { describe, expect, it, vi } from 'vitest';
import { ApiClient } from './client';
import type { HealthDTO, ReplayStatusDTO, RuntimeStatusDTO } from './types';

const jsonResponse = <T>(data: T, init: { ok?: boolean; status?: number } = {}) =>
  ({ ok: init.ok ?? true, status: init.status ?? 200, json: vi.fn(async () => data) }) as unknown as Response;

describe('ApiClient', () => {
  it('uses a bound default browser fetch wrapper', async () => {
    const health: HealthDTO = { ok: true, service: 'trade-lab-backend', version: '0.1.0' };
    const originalFetch = globalThis.fetch;
    const fetchImpl = vi.fn(function (this: typeof globalThis) {
      if (this !== globalThis) {
        throw new TypeError('Illegal invocation');
      }
      return Promise.resolve(jsonResponse(health));
    });
    globalThis.fetch = fetchImpl as unknown as typeof fetch;

    try {
      const client = new ApiClient('http://localhost:8001');

      await expect(client.health()).resolves.toEqual({ ok: true, data: health });
      expect(fetchImpl).toHaveBeenCalledWith('http://localhost:8001/health', {
        method: 'GET',
        headers: { Accept: 'application/json' },
      });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it('parses health responses from mocked fetch', async () => {
    const health: HealthDTO = { ok: true, service: 'trade-lab-backend', version: '0.1.0' };
    const fetchImpl = vi.fn(async () => jsonResponse(health)) as unknown as typeof fetch;
    const client = new ApiClient('http://localhost:8001', fetchImpl);

    await expect(client.health()).resolves.toEqual({ ok: true, data: health });
    expect(fetchImpl).toHaveBeenCalledWith('http://localhost:8001/health', {
      method: 'GET',
      headers: { Accept: 'application/json' },
    });
  });

  it('parses runtime status responses from mocked fetch', async () => {
    const status: RuntimeStatusDTO = {
      service: 'trade-lab-backend',
      version: '0.1.0',
      runtime_mode: 'live',
      requested_symbol: 'NQ.c.0',
      instrument_root: 'NQ',
      supported_tick_timeframes: [147, 987, 2000],
      engine_ready: true,
      feed_ready: true,
      feed_state: 'connected',
      replay: { state: 'idle', events_processed: 0, warnings_recorded: 0, last_event_ts_utc: null, last_error: null, requested_symbol: null, schema: null },
      live: { state: 'idle', requested_symbol: 'NQ.c.0', dataset: 'GLBX.MDP3', schemas: ['trades'], api_key_configured: false, enabled: false, events_processed: 0, last_event_ts_utc: null, last_error: null, started_at_utc: null, stopped_at_utc: null },
    };
    const fetchImpl = vi.fn(async () => jsonResponse(status)) as unknown as typeof fetch;
    const client = new ApiClient('http://localhost:8001', fetchImpl);

    await expect(client.status()).resolves.toEqual({ ok: true, data: status });
    expect(fetchImpl).toHaveBeenCalledWith('http://localhost:8001/api/v1/status', expect.objectContaining({ method: 'GET' }));
  });

  it('parses replay status responses from mocked fetch', async () => {
    const replay: ReplayStatusDTO = { state: 'running', events_processed: 42, warnings_recorded: 1, last_event_ts_utc: '2026-05-21T14:00:00Z', last_error: null, requested_symbol: 'NQ.c.0', schema: 'mbp-1' };
    const fetchImpl = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async () => jsonResponse(replay));
    const client = new ApiClient('http://localhost:8001', fetchImpl as unknown as typeof fetch);

    await expect(client.replayStatus()).resolves.toEqual({ ok: true, data: replay });
    expect(fetchImpl).toHaveBeenCalledWith('http://localhost:8001/api/v1/replay/status', expect.objectContaining({ method: 'GET' }));
  });

  it('loads safe replay sources without path inputs', async () => {
    const sources = { sources: [{ source_id: 'synthetic:nq-demo', label: 'Synthetic NQ demo', requested_symbol: 'NQ.c.0', schema: 'trades' }] };
    const fetchImpl = vi.fn(async () => jsonResponse(sources)) as unknown as typeof fetch;
    const client = new ApiClient('http://localhost:8001', fetchImpl);

    await expect(client.replaySources()).resolves.toEqual({ ok: true, data: sources });
    expect(fetchImpl).toHaveBeenCalledWith('http://localhost:8001/api/v1/replay/sources', expect.objectContaining({ method: 'GET' }));
  });

  it('parses historical replay source catalog entries', async () => {
    const sources = {
      historical: { available: true, status: 'historical sources discovered' },
      sources: [
        { source_id: 'synthetic:nq-demo', label: 'Synthetic NQ demo', requested_symbol: 'NQ.c.0', schema: 'trades', kind: 'synthetic' },
        { source_id: 'historical:nq:2026-02-22:trades', label: 'Historical NQ 2026-02-22 trades', requested_symbol: 'NQ.c.0', schema: 'trades', kind: 'historical', session_label: '2026-02-22', availability: 'metadata_only' },
      ],
    };
    const fetchImpl = vi.fn(async () => jsonResponse(sources)) as unknown as typeof fetch;
    const client = new ApiClient('http://localhost:8001', fetchImpl);

    await expect(client.replaySources()).resolves.toEqual({ ok: true, data: sources });
  });

  it('posts replay control actions to safe endpoints', async () => {
    const replay: ReplayStatusDTO = { state: 'running', events_processed: 1, warnings_recorded: 0, last_event_ts_utc: null, last_error: null, requested_symbol: 'NQ.c.0', schema: 'trades', source_id: 'synthetic:nq-demo' };
    const fetchImpl = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async () => jsonResponse(replay));
    const client = new ApiClient('http://localhost:8001', fetchImpl as unknown as typeof fetch);

    await expect(client.startReplay({ source_id: 'synthetic:nq-demo' })).resolves.toEqual({ ok: true, data: replay });
    await client.pauseReplay();
    await client.resumeReplay();
    await client.stopReplay();

    expect(fetchImpl.mock.calls.map(([url]) => String(url))).toEqual([
      'http://localhost:8001/api/v1/replay/start',
      'http://localhost:8001/api/v1/replay/pause',
      'http://localhost:8001/api/v1/replay/resume',
      'http://localhost:8001/api/v1/replay/stop',
    ]);
    expect(fetchImpl.mock.calls[0][1]).toMatchObject({ method: 'POST', body: JSON.stringify({ source_id: 'synthetic:nq-demo' }) });
  });

  it('surfaces replay control HTTP errors without throwing', async () => {
    const fetchImpl = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async () => jsonResponse({ detail: 'invalid replay source id' }, { ok: false, status: 400 }));
    const client = new ApiClient('http://localhost:8001', fetchImpl as unknown as typeof fetch);

    await expect(client.startReplay({ source_id: 'synthetic:nq-demo' })).resolves.toEqual({ ok: false, error: 'HTTP 400 from /api/v1/replay/start: invalid replay source id', status: 400 });
    await expect(client.pauseReplay()).resolves.toEqual({ ok: false, error: 'HTTP 400 from /api/v1/replay/pause: invalid replay source id', status: 400 });
    await expect(client.resumeReplay()).resolves.toEqual({ ok: false, error: 'HTTP 400 from /api/v1/replay/resume: invalid replay source id', status: 400 });
    await expect(client.stopReplay()).resolves.toEqual({ ok: false, error: 'HTTP 400 from /api/v1/replay/stop: invalid replay source id', status: 400 });
  });

  it('gets and posts live control actions without sending secrets', async () => {
    const live = { state: 'running', requested_symbol: 'NQ.c.0', dataset: 'GLBX.MDP3', schemas: ['trades', 'mbp-1'], api_key_configured: true, enabled: true, events_processed: 0, last_event_ts_utc: null, last_error: null, started_at_utc: null, stopped_at_utc: null };
    const fetchImpl = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async () => jsonResponse(live));
    const client = new ApiClient('http://localhost:8001', fetchImpl as unknown as typeof fetch);

    await expect(client.liveStatus()).resolves.toEqual({ ok: true, data: live });
    await expect(client.startLive()).resolves.toEqual({ ok: true, data: live });
    await expect(client.stopLive()).resolves.toEqual({ ok: true, data: live });

    expect(fetchImpl.mock.calls.map(([url]) => String(url))).toEqual([
      'http://localhost:8001/api/v1/live/status',
      'http://localhost:8001/api/v1/live/start',
      'http://localhost:8001/api/v1/live/stop',
    ]);
    expect(JSON.stringify(fetchImpl.mock.calls).toLowerCase()).not.toMatch(/secret|token|credential/);
  });

  it('surfaces live control errors without sending request bodies or credentials', async () => {
    const fetchImpl = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async () => jsonResponse({ detail: 'Databento API key is not configured' }, { ok: false, status: 400 }));
    const client = new ApiClient('http://localhost:8001', fetchImpl as unknown as typeof fetch);

    await expect(client.liveStatus()).resolves.toEqual({ ok: false, error: 'HTTP 400 from /api/v1/live/status: Databento API key is not configured', status: 400 });
    await expect(client.startLive()).resolves.toEqual({ ok: false, error: 'HTTP 400 from /api/v1/live/start: Databento API key is not configured', status: 400 });
    await expect(client.stopLive()).resolves.toEqual({ ok: false, error: 'HTTP 400 from /api/v1/live/stop: Databento API key is not configured', status: 400 });

    for (const [url, init] of fetchImpl.mock.calls) {
      expect(String(url)).toMatch(/\/api\/v1\/live\//);
      expect(init?.body).toBeUndefined();
      expect(JSON.stringify(init).toLowerCase()).not.toMatch(/api[_-]?key|secret|token|password|credential/);
    }
  });

  it('does not create path-like source ids in replay start requests', async () => {
    const replay: ReplayStatusDTO = { state: 'running', events_processed: 1, warnings_recorded: 0, last_event_ts_utc: null, last_error: null, requested_symbol: 'NQ.c.0', schema: 'trades', source_id: 'synthetic:nq-demo' };
    const fetchImpl = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async () => jsonResponse(replay));
    const client = new ApiClient('http://localhost:8001', fetchImpl as unknown as typeof fetch);

    await client.startReplay({ source_id: 'synthetic:nq-demo' });

    const body = JSON.parse(String(fetchImpl.mock.calls[0][1]?.body));
    expect(body.source_id).toBe('synthetic:nq-demo');
    expect(body.source_id).not.toMatch(/[\\/]|\.\.|^[A-Za-z]:/);
  });

  it('starts historical replay by opaque id without sending path or file fields', async () => {
    const replay: ReplayStatusDTO = { state: 'running', events_processed: 1, warnings_recorded: 0, last_event_ts_utc: null, last_error: null, requested_symbol: 'NQ.c.0', schema: 'trades', source_id: 'historical:nq:2026-02-22:trades' };
    const fetchImpl = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async () => jsonResponse(replay));
    const client = new ApiClient('http://localhost:8001', fetchImpl as unknown as typeof fetch);

    await client.startReplay({ source_id: 'historical:nq:2026-02-22:trades' });

    const body = JSON.parse(String(fetchImpl.mock.calls[0][1]?.body));
    expect(body).toEqual({ source_id: 'historical:nq:2026-02-22:trades' });
    expect(JSON.stringify(body).toLowerCase()).not.toMatch(/path|file|parquet|[\\/]|\.\.|^[a-z]:/);
  });

  it('returns graceful offline errors without throwing', async () => {
    const fetchImpl = vi.fn(async () => {
      throw new Error('connect ECONNREFUSED');
    }) as unknown as typeof fetch;
    const client = new ApiClient('http://localhost:8001', fetchImpl);

    await expect(client.health()).resolves.toMatchObject({
      ok: false,
      error: expect.stringContaining('Backend unavailable'),
    });
  });

  it('surfaces HTTP errors for UI state without exposing secrets', async () => {
    const fetchImpl = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async () => jsonResponse({ error: 'nope' }, { ok: false, status: 503 }));
    const client = new ApiClient('http://localhost:8001', fetchImpl as unknown as typeof fetch);

    const result = await client.status();

    expect(result).toEqual({ ok: false, error: 'HTTP 503 from /api/v1/status', status: 503 });
    const [, init] = fetchImpl.mock.calls[0];
    expect(JSON.stringify(init).toLowerCase()).not.toMatch(/api[_-]?key|secret|token|password|credential/);
  });

  it('surfaces bounded safe live start details and redacts secret-like values', async () => {
    const detail = 'Databento live subscription wiring is not enabled in this build; api_key=db-abcdefghijklmnopqrstuvwxyz123 password=hunter2';
    const fetchImpl = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async () => jsonResponse({ detail }, { ok: false, status: 400 }));
    const client = new ApiClient('http://localhost:8001', fetchImpl as unknown as typeof fetch);

    const result = await client.startLive();

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toContain('Databento live subscription wiring is not enabled in this build');
      expect(result.error).not.toContain('abcdefghijklmnopqrstuvwxyz');
      expect(result.error).not.toContain('hunter2');
      expect(result.error.length).toBeLessThanOrEqual(280);
    }
  });

  it('does not place API keys or secrets in requested URLs or headers', async () => {
    const fetchImpl = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async () => jsonResponse({ ok: true, service: 'trade-lab-backend', version: '0.1.0' }));
    const client = new ApiClient('http://localhost:8001', fetchImpl as unknown as typeof fetch);

    await client.health();

    const [url, init] = fetchImpl.mock.calls[0];
    expect(String(url).toLowerCase()).not.toMatch(/api[_-]?key|secret|token|password|credential/);
    expect(JSON.stringify(init).toLowerCase()).not.toMatch(/api[_-]?key|secret|token|password|credential/);
  });
});
