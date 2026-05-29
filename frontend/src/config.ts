export type FrontendConfig = {
  apiBase: string;
  wsUrl: string;
  appName: string;
};

export const DEFAULT_API_BASE = 'http://localhost:8001';
export const DEFAULT_WS_URL = 'ws://localhost:8001/ws/v1';

const trimTrailingSlash = (value: string) => value.replace(/\/+$/, '');

type PublicViteEnv = {
  readonly VITE_API_BASE?: string;
  readonly VITE_WS_URL?: string;
};

export const createFrontendConfig = (env: PublicViteEnv): FrontendConfig => ({
  apiBase: trimTrailingSlash(env.VITE_API_BASE || DEFAULT_API_BASE),
  wsUrl: env.VITE_WS_URL || DEFAULT_WS_URL,
  appName: 'Trade-Lab Workstation',
});

// VITE_* values are bundled into browser code and are therefore public configuration,
// never a place for API keys, credentials, file paths, or trading secrets.
export const config: FrontendConfig = createFrontendConfig(import.meta.env as PublicViteEnv);
