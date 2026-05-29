import { describe, expect, it } from 'vitest';
import { createFrontendConfig, DEFAULT_API_BASE, DEFAULT_WS_URL } from './config';

describe('frontend config', () => {
  it('uses localhost Phase 3A defaults when Vite env is empty', () => {
    const cfg = createFrontendConfig({});

    expect(cfg).toEqual({
      apiBase: DEFAULT_API_BASE,
      wsUrl: DEFAULT_WS_URL,
      appName: 'Trade-Lab Workstation',
    });
  });

  it('uses test-safe Vite env overrides and trims API trailing slashes', () => {
    const cfg = createFrontendConfig({
      VITE_API_BASE: 'https://paper.example.test///',
      VITE_WS_URL: 'wss://paper.example.test/ws/v1',
    });

    expect(cfg.apiBase).toBe('https://paper.example.test');
    expect(cfg.wsUrl).toBe('wss://paper.example.test/ws/v1');
  });

  it('exposes only public endpoint fields and no secret-like config assumptions', () => {
    const cfg = createFrontendConfig({ VITE_API_BASE: 'http://localhost:9000', VITE_WS_URL: 'ws://localhost:9000/ws/v1' });

    expect(Object.keys(cfg).sort()).toEqual(['apiBase', 'appName', 'wsUrl']);
    expect(JSON.stringify(cfg).toLowerCase()).not.toMatch(/api[_-]?key|secret|token|password|credential/);
  });
});
