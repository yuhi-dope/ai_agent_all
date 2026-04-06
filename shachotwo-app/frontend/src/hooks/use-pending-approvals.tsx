"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";

/** サイドバー・BPO ダッシュ・承認一覧で共有（/execution/pending-approvals は1ソース） */
export interface PendingApprovalItem {
  id: string;
  pipeline_key: string;
  pipeline_label: string;
  created_at: string;
  summary: string;
  confidence: number;
  output_detail?: string;
}

interface PendingApprovalsResponse {
  count: number;
  items: PendingApprovalItem[];
}

export type PendingApprovalsContextValue = {
  items: PendingApprovalItem[];
  count: number;
  loading: boolean;
  refetch: () => Promise<void>;
  removeItemLocally: (id: string) => void;
};

const PendingApprovalsContext = createContext<PendingApprovalsContextValue | null>(null);

const POLL_MS = 60_000;

const isDev = process.env.NODE_ENV === "development";

type FetchReason = "effect" | "interval" | "manual";

export function PendingApprovalsProvider({ children }: { children: ReactNode }) {
  const { session } = useAuth();
  const [items, setItems] = useState<PendingApprovalItem[]>([]);
  const [count, setCount] = useState(0);
  const [loading, setLoading] = useState(true);

  const runFetch = useCallback(
    async (reason: FetchReason) => {
      if (!session?.access_token) {
        if (isDev) {
          console.debug("[PendingApprovals] skip fetch (no session token)", { reason });
        }
        setItems([]);
        setCount(0);
        setLoading(false);
        return;
      }
      if (isDev) {
        console.debug("[PendingApprovals] fetch start", {
          reason,
          hint:
            reason === "effect"
              ? "React Strict Mode で development 時は2回続くことがあります"
              : undefined,
        });
      }
      setLoading(true);
      try {
        const data = await apiFetch<PendingApprovalsResponse>("/approvals/pending", {
          token: session.access_token,
        });
        const n = data.count ?? 0;
        setItems(data.items ?? []);
        setCount(n);
        if (isDev) {
          console.debug("[PendingApprovals] fetch ok", { count: n });
        }
      } catch (err) {
        setItems([]);
        setCount(0);
        if (isDev) {
          console.warn(
            "[PendingApprovals] fetch failed — 承認待ち一覧は空表示になります（詳細は [apiFetch] ログ参照）",
            err
          );
        }
      } finally {
        setLoading(false);
      }
    },
    [session?.access_token]
  );

  const refetch = useCallback(() => runFetch("manual"), [runFetch]);

  const removeItemLocally = useCallback((id: string) => {
    setItems((prev) => prev.filter((x) => x.id !== id));
    setCount((c) => Math.max(0, c - 1));
  }, []);

  useEffect(() => {
    void runFetch("effect");
  }, [runFetch]);

  useEffect(() => {
    if (!session?.access_token) return;
    const t = setInterval(() => void runFetch("interval"), POLL_MS);
    return () => clearInterval(t);
  }, [session?.access_token, runFetch]);

  const value = useMemo(
    () => ({ items, count, loading, refetch, removeItemLocally }),
    [items, count, loading, refetch, removeItemLocally]
  );

  return (
    <PendingApprovalsContext.Provider value={value}>{children}</PendingApprovalsContext.Provider>
  );
}

export function usePendingApprovals(): PendingApprovalsContextValue {
  const ctx = useContext(PendingApprovalsContext);
  if (!ctx) {
    throw new Error("usePendingApprovals must be used within PendingApprovalsProvider");
  }
  return ctx;
}
