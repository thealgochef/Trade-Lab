import { config } from '../config';
import type { ApiResult, HealthDTO, LiveStatusDTO, ReplaySourcesResponseDTO, ReplayStartRequestDTO, ReplayStatusDTO, RuntimeStatusDTO } from './types';

type FetchLike = typeof fetch;

const defaultFetch: FetchLike = (input, init) => globalThis.fetch(input, init);

export class ApiClient {
  constructor(
    private readonly baseUrl = config.apiBase,
    private readonly fetchImpl: FetchLike = defaultFetch,
  ) {}

  health(): Promise<ApiResult<HealthDTO>> {
    return this.get<HealthDTO>('/health');
  }

  status(): Promise<ApiResult<RuntimeStatusDTO>> {
    return this.get<RuntimeStatusDTO>('/api/v1/status');
  }

  replayStatus(): Promise<ApiResult<ReplayStatusDTO>> {
    return this.get<ReplayStatusDTO>('/api/v1/replay/status');
  }

  replaySources(): Promise<ApiResult<ReplaySourcesResponseDTO>> {
    return this.get<ReplaySourcesResponseDTO>('/api/v1/replay/sources');
  }

  liveStatus(): Promise<ApiResult<LiveStatusDTO>> {
    return this.get<LiveStatusDTO>('/api/v1/live/status');
  }

  startLive(): Promise<ApiResult<LiveStatusDTO>> {
    return this.post<LiveStatusDTO>('/api/v1/live/start');
  }

  stopLive(): Promise<ApiResult<LiveStatusDTO>> {
    return this.post<LiveStatusDTO>('/api/v1/live/stop');
  }

  startReplay(request: ReplayStartRequestDTO): Promise<ApiResult<ReplayStatusDTO>> {
    return this.post<ReplayStatusDTO>('/api/v1/replay/start', request);
  }

  pauseReplay(): Promise<ApiResult<ReplayStatusDTO>> {
    return this.post<ReplayStatusDTO>('/api/v1/replay/pause');
  }

  resumeReplay(): Promise<ApiResult<ReplayStatusDTO>> {
    return this.post<ReplayStatusDTO>('/api/v1/replay/resume');
  }

  stopReplay(): Promise<ApiResult<ReplayStatusDTO>> {
    return this.post<ReplayStatusDTO>('/api/v1/replay/stop');
  }

  private async get<T>(path: string): Promise<ApiResult<T>> {
    try {
      const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        method: 'GET',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) {
        return { ok: false, error: await this.errorMessage(response, path), status: response.status };
      }
      return { ok: true, data: (await response.json()) as T };
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unknown network error';
      return { ok: false, error: `Backend unavailable: ${message}` };
    }
  }

  private async post<T>(path: string, body?: unknown): Promise<ApiResult<T>> {
    try {
      const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      if (!response.ok) {
        return { ok: false, error: await this.errorMessage(response, path), status: response.status };
      }
      return { ok: true, data: (await response.json()) as T };
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unknown network error';
      return { ok: false, error: `Backend unavailable: ${message}` };
    }
  }

  private async errorMessage(response: Response, path: string): Promise<string> {
    const prefix = `HTTP ${response.status} from ${path}`;
    try {
      const body = (await response.json()) as unknown;
      const detail = this.safeDetail(body);
      return detail ? `${prefix}: ${detail}` : prefix;
    } catch {
      return prefix;
    }
  }

  private safeDetail(body: unknown): string | null {
    if (!body || typeof body !== 'object' || !('detail' in body)) return null;
    const detail = (body as { detail?: unknown }).detail;
    const text = typeof detail === 'string' ? detail : JSON.stringify(detail);
    return sanitizeDetail(text ?? '');
  }
}

export const apiClient = new ApiClient();

function sanitizeDetail(value: string): string | null {
  let sanitized = value
    .replace(/[A-Za-z]:\\[^\s,;]+/g, '<path>')
    .replace(/\/(?:[^\s,;]+\/)+[^\s,;]+/g, '<path>')
    .replace(/(secret|token|password|api[_-]?key)\s*[:=]\s*[^\s,;]+/gi, '<redacted>')
    .replace(/[A-Za-z0-9_-]{24,}/g, '<redacted>')
    .replace(/secret|token|password|api[_-]?key/gi, '<redacted>')
    .trim();
  if (sanitized.length > 240) sanitized = `${sanitized.slice(0, 237)}...`;
  return sanitized.length > 0 ? sanitized : null;
}
