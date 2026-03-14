/**
 * API client for shachotwo-app backend.
 * All requests include JWT from Supabase Auth session.
 */

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

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(url.toString(), {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!res.ok) {
    const errorData = await res.json().catch(() => null);
    // FastAPI returns {detail: "..."} or {detail: {error: {...}}}
    const message =
      errorData?.error?.message ??
      errorData?.detail?.error?.message ??
      (typeof errorData?.detail === "string" ? errorData.detail : null) ??
      res.statusText;
    throw new Error(message);
  }

  return res.json();
}
