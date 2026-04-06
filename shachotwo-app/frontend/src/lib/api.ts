/**
 * API client for shachotwo-app backend.
 * All requests include JWT from Supabase Auth session.
 * tokenを渡さなくともSupabaseセッションから自動取得する。
 *
 * NEXT_PUBLIC_API_URL が未設定のときは同一オリジン `/api/v1` を使い、
 * next.config の rewrites で FastAPI にプロキシする（開発時の直叩き失敗を減らす）。
 */

import { createClient } from "@/lib/supabase";

function getApiBase(): string {
  const env = process.env.NEXT_PUBLIC_API_URL?.trim();
  if (env) return env.replace(/\/$/, "");
  return "/api/v1";
}

function buildRequestUrl(path: string, params?: Record<string, string>): URL {
  const base = getApiBase();
  const p = path.startsWith("/") ? path : `/${path}`;
  const pathWithBase = `${base.replace(/\/$/, "")}${p}`;
  const url = base.startsWith("http")
    ? new URL(pathWithBase)
    : new URL(
        pathWithBase,
        typeof window !== "undefined"
          ? window.location.origin
          : "http://localhost:3000"
      );
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      url.searchParams.set(key, value);
    });
  }
  return url;
}

const DEFAULT_TIMEOUT_MS = 30_000;

const isDev = process.env.NODE_ENV === "development";

function isAbortError(err: unknown): boolean {
  return (
    (typeof DOMException !== "undefined" &&
      err instanceof DOMException &&
      err.name === "AbortError") ||
    (err instanceof Error && err.name === "AbortError")
  );
}

interface ApiOptions {
  method?: string;
  body?: unknown;
  token?: string;
  params?: Record<string, string>;
  signal?: AbortSignal;
  /** 0 のときタイムアウトしない。未指定は DEFAULT_TIMEOUT_MS */
  timeoutMs?: number;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  has_more: boolean;
}

export interface ApiError {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
}

/**
 * Supabaseセッションからアクセストークンを自動取得。
 * tokenが明示的に渡されていればそちらを優先。
 */
async function resolveToken(token?: string): Promise<string | undefined> {
  if (token) return token;
  try {
    const supabase = createClient();
    const { data } = await supabase.auth.getSession();
    return data.session?.access_token;
  } catch {
    return undefined;
  }
}

function resolveFetchSignal(
  external: AbortSignal | undefined,
  timeoutMs: number
): AbortSignal | undefined {
  if (timeoutMs <= 0) return external;
  const timeoutSignal = AbortSignal.timeout(timeoutMs);
  if (!external) return timeoutSignal;
  return AbortSignal.any([external, timeoutSignal]);
}

export async function apiFetch<T>(
  path: string,
  {
    method = "GET",
    body,
    token,
    params,
    signal,
    timeoutMs = DEFAULT_TIMEOUT_MS,
  }: ApiOptions = {}
): Promise<T> {
  const url = buildRequestUrl(path, params);

  const resolvedToken = await resolveToken(token);

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (resolvedToken) {
    headers["Authorization"] = `Bearer ${resolvedToken}`;
  }

  const fetchSignal = resolveFetchSignal(signal, timeoutMs);

  let res: Response;
  try {
    res = await fetch(url.toString(), {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
      signal: fetchSignal,
    });
  } catch (err) {
    if (isDev) {
      if (isAbortError(err)) {
        console.debug(
          "[apiFetch] request aborted — サーバーには届かないためターミナルにログは出ません。原因の例: React Strict Mode の再マウント、ページ遷移、別リクエストによる置き換え、タイムアウト",
          { method, path, url: url.toString() }
        );
      } else {
        console.warn("[apiFetch] network error", { method, path, url: url.toString(), err });
      }
    }
    throw err;
  }

  if (!res.ok) {
    const errorData = await res.json().catch(() => null);
    const message =
      errorData?.error?.message ??
      errorData?.detail?.error?.message ??
      (typeof errorData?.detail === "string" ? errorData.detail : null) ??
      res.statusText;
    if (isDev) {
      console.warn("[apiFetch] HTTP error", { method, path, status: res.status, message });
    }
    throw new Error(message);
  }

  return res.json();
}
