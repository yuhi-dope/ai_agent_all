/**
 * API client for shachotwo-app backend.
 * All requests include JWT from Supabase Auth session.
 * tokenを渡さなくてもSupabaseセッションから自動取得する。
 */

import { createClient } from "@/lib/supabase";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

interface ApiOptions {
  method?: string;
  body?: unknown;
  token?: string;
  params?: Record<string, string>;
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

export async function apiFetch<T>(
  path: string,
  { method = "GET", body, token, params }: ApiOptions = {}
): Promise<T> {
  const url = new URL(`${API_BASE}${path}`);
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      url.searchParams.set(key, value);
    });
  }

  const resolvedToken = await resolveToken(token);

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (resolvedToken) {
    headers["Authorization"] = `Bearer ${resolvedToken}`;
  }

  const res = await fetch(url.toString(), {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!res.ok) {
    const errorData = await res.json().catch(() => null);
    const message =
      errorData?.error?.message ??
      errorData?.detail?.error?.message ??
      (typeof errorData?.detail === "string" ? errorData.detail : null) ??
      res.statusText;
    throw new Error(message);
  }

  return res.json();
}
