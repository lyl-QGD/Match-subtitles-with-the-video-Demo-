export type Readiness = { status: string; missing: string[]; connectivity_checked: boolean; reason: string; database?: { status: string; table_count?: number }; storage?: { status: string } };
export async function health() {
  const signal = AbortSignal.timeout(5000);
  const [live, ready] = await Promise.all([fetch('/api/v1/health/live', { signal }), fetch('/api/v1/health/ready', { signal })]);
  if (!live.ok || (ready.status !== 503 && !ready.ok)) throw new Error('API unavailable');
  return { live: (await live.json()).status as string, ready: await ready.json() as Readiness };
}

export type DialogueClip = {
  id: string;
  series_slug: string;
  series_title_zh: string;
  series_title_en: string;
  season: number;
  episode: number;
  episode_title: string;
  sequence: number;
  start_ms: number;
  end_ms: number;
  text_en: string;
  text_zh: string;
  previous_en: string;
  previous_zh: string;
  next_en: string;
  next_zh: string;
};

export type SearchResult = DialogueClip & { matched_languages: ('en' | 'zh')[] };

export type SearchResponse = {
  query: string;
  season: number | null;
  episode: number | null;
  page: number;
  page_size: number;
  total: number;
  pages: number;
  results: SearchResult[];
};

export type PlaybackSegment = {
  id: string;
  version_id: string;
  revision_id: string;
  sequence: number;
  start_ms: number;
  end_ms: number;
  text_en: string;
  text_zh: string;
};

export type SegmentPlayback = {
  media_url: string;
  expires_in_seconds: number;
  media_type: string;
  duration_ms: number | null;
  version_id: string;
  revision_id: string;
  allow_share: boolean;
  start_ms: number;
  end_ms: number;
  segments: PlaybackSegment[];
};

export type CreateShareInput = {
  version_id: string;
  revision_id: string;
  segment_ids: string[];
  start_ms: number;
  end_ms: number;
  subtitle_mode: 'bilingual' | 'en' | 'zh' | 'off';
};

export type CreatedShare = {
  share_id: string;
  token: string;
  expires_at: string;
  expires_in_days: number;
};

export type SharedClip = {
  series_slug: string;
  series_title_zh: string;
  series_title_en: string;
  season: number;
  episode: number;
  episode_title: string;
  version_id: string;
  revision_id: string;
  start_ms: number;
  end_ms: number;
  subtitle_mode: 'bilingual' | 'en' | 'zh' | 'off';
  duration_ms: number | null;
  media_type: string;
  media_url: string;
  media_url_expires_in_seconds: number;
  segments: Array<{
    id: string;
    sequence: number;
    start_ms: number;
    end_ms: number;
    text_en: string;
    text_zh: string;
  }>;
};

export type ManagedShare = {
  id: string;
  status: 'active' | 'revoked' | 'expired';
  created_at: string;
  expires_at: string | null;
  start_ms: number;
  end_ms: number;
  series_slug: string;
  season: number;
  episode: number;
};

export class ApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

async function requestJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set('Accept', 'application/json');
  const response = await fetch(url, {
    ...init,
    signal: init.signal ?? AbortSignal.timeout(15000),
    headers,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body?.detail;
    const code = typeof detail === 'object' && detail !== null ? String(detail.code ?? 'request_failed') : 'request_failed';
    const message = typeof detail === 'object' && detail !== null ? String(detail.message ?? '请求暂时不可用。') : '请求暂时不可用。';
    throw new ApiError(response.status, code, message);
  }
  return body as T;
}

async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  return requestJson<T>(url, { method: 'GET', signal });
}

export async function searchDialogue(
  params: { query: string; season?: number; episode?: number; page: number; pageSize?: number },
  signal?: AbortSignal,
): Promise<SearchResponse> {
  const search = new URLSearchParams({ q: params.query, page: String(params.page), page_size: String(params.pageSize ?? 10) });
  if (params.season !== undefined) search.set('season', String(params.season));
  if (params.episode !== undefined) search.set('episode', String(params.episode));
  return fetchJson<SearchResponse>(`/api/v1/search?${search.toString()}`, signal);
}

export async function featuredDialogue(signal?: AbortSignal): Promise<DialogueClip[]> {
  const response = await fetchJson<{ results: DialogueClip[] }>('/api/v1/featured', signal);
  return response.results;
}

export async function segmentPlayback(segmentId: string, signal?: AbortSignal): Promise<SegmentPlayback> {
  return fetchJson<SegmentPlayback>(`/api/v1/segments/${encodeURIComponent(segmentId)}/playback`, signal);
}

export async function createShare(input: CreateShareInput): Promise<CreatedShare> {
  return requestJson<CreatedShare>('/api/v1/shares', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
}

export async function resolveShare(token: string): Promise<SharedClip> {
  return requestJson<SharedClip>('/api/v1/shares/resolve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  });
}

export async function managedShares(): Promise<ManagedShare[]> {
  const response = await fetchJson<{ shares: ManagedShare[] }>('/api/v1/shares/manage');
  return response.shares;
}

export async function revokeManagedShare(shareId: string): Promise<{ status: 'revoked' }> {
  return requestJson<{ status: 'revoked' }>(`/api/v1/shares/manage/${encodeURIComponent(shareId)}/revoke`, {
    method: 'POST',
  });
}
