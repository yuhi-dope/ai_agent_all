"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch, type PaginatedResponse } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

// ---------- Types ----------

type TicketStatus =
  | "open"
  | "waiting"
  | "ai_responded"
  | "escalated"
  | "resolved"
  | "closed";
type TicketPriority = "low" | "medium" | "high" | "urgent";
type TicketCategory =
  | "usage"
  | "billing"
  | "bug"
  | "feature"
  | "account";

interface SupportTicket {
  id: string;
  ticket_number: string;
  subject: string;
  category: TicketCategory;
  priority: TicketPriority;
  status: TicketStatus;
  ai_handled: boolean;
  escalated: boolean;
  sla_due_at: string | null;
  first_response_at: string | null;
  created_at: string;
  updated_at: string;
}

// ---------- Config ----------

const PRIORITY_CONFIG: Record<
  TicketPriority,
  { label: string; className: string }
> = {
  urgent: { label: "緊急", className: "bg-red-100 text-red-800" },
  high: { label: "高", className: "bg-orange-100 text-orange-800" },
  medium: { label: "中", className: "bg-yellow-100 text-yellow-800" },
  low: { label: "低", className: "bg-gray-100 text-gray-700" },
};

const STATUS_CONFIG: Record<
  TicketStatus,
  { label: string; className: string }
> = {
  open: { label: "未対応", className: "bg-blue-100 text-blue-800" },
  waiting: { label: "返信待ち", className: "bg-yellow-100 text-yellow-800" },
  ai_responded: {
    label: "AI対応済み",
    className: "bg-green-100 text-green-800",
  },
  escalated: { label: "担当者対応中", className: "bg-orange-100 text-orange-800" },
  resolved: { label: "解決済み", className: "bg-gray-100 text-gray-700" },
  closed: { label: "クローズ", className: "bg-gray-100 text-gray-500" },
};

const CATEGORY_LABELS: Record<TicketCategory, string> = {
  usage: "使い方",
  billing: "請求・料金",
  bug: "不具合",
  feature: "機能要望",
  account: "アカウント",
};

const PAGE_SIZE = 15;

type FilterTab = "all" | "ai_responded" | "escalated" | "open";

// ---------- Helpers ----------

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("ja-JP", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function getSlaRemaining(slaDueAt: string | null): {
  label: string;
  className: string;
} | null {
  if (!slaDueAt) return null;
  const due = new Date(slaDueAt);
  const now = new Date();
  const diffMs = due.getTime() - now.getTime();
  const diffMin = Math.floor(diffMs / 60000);

  if (diffMin < 0) {
    return {
      label: "SLA超過",
      className: "bg-red-100 text-red-800",
    };
  }
  if (diffMin < 60) {
    return {
      label: `残${diffMin}分`,
      className: "bg-red-100 text-red-800",
    };
  }
  if (diffMin < 240) {
    const h = Math.floor(diffMin / 60);
    const m = diffMin % 60;
    return {
      label: `残${h}時間${m}分`,
      className: "bg-yellow-100 text-yellow-800",
    };
  }
  const h = Math.floor(diffMin / 60);
  return {
    label: `残${h}時間`,
    className: "bg-green-100 text-green-800",
  };
}

// ---------- Skeleton ----------

function TicketRowSkeleton() {
  return (
    <Card>
      <CardContent className="flex flex-col gap-2 pt-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-col gap-2 flex-1">
          <div className="flex flex-wrap gap-2">
            <div className="h-5 w-12 animate-pulse rounded-full bg-muted" />
            <div className="h-5 w-16 animate-pulse rounded-full bg-muted" />
            <div className="h-4 w-48 animate-pulse rounded bg-muted" />
          </div>
          <div className="h-3 w-32 animate-pulse rounded bg-muted" />
        </div>
        <div className="h-5 w-20 animate-pulse rounded-full bg-muted" />
      </CardContent>
    </Card>
  );
}

// ---------- Page ----------

export default function SupportTicketsPage() {
  const { session } = useAuth();
  const [tickets, setTickets] = useState<SupportTicket[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [offset, setOffset] = useState(0);
  const [activeTab, setActiveTab] = useState<FilterTab>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchTickets = useCallback(
    async (tab: FilterTab, pageOffset: number) => {
      if (!session?.access_token) return;
      setLoading(true);
      setError(null);
      try {
        const params: Record<string, string> = {
          limit: String(PAGE_SIZE),
          offset: String(pageOffset),
          sort: "priority",
        };
        if (tab === "ai_responded") params.ai_handled = "true";
        else if (tab === "escalated") params.escalated = "true";
        else if (tab === "open") params.status = "open";

        const res = await apiFetch<PaginatedResponse<SupportTicket>>(
          "/support/tickets",
          { token: session.access_token, params }
        );
        setTickets(res.items);
        setTotal(res.total);
        setHasMore(res.has_more);
      } catch {
        setError(
          "チケットの取得に失敗しました。しばらく経ってから再度お試しください"
        );
      } finally {
        setLoading(false);
      }
    },
    [session?.access_token]
  );

  useEffect(() => {
    fetchTickets(activeTab, offset);
  }, [activeTab, offset, fetchTickets]);

  function handleTabChange(tab: FilterTab) {
    setActiveTab(tab);
    setOffset(0);
  }

  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;
  const totalPages = Math.ceil(total / PAGE_SIZE);

  function renderTicketCard(ticket: SupportTicket) {
    const priorityCfg = PRIORITY_CONFIG[ticket.priority] ?? PRIORITY_CONFIG.medium;
    const statusCfg = STATUS_CONFIG[ticket.status] ?? STATUS_CONFIG.open;
    const slaRemaining = getSlaRemaining(ticket.sla_due_at);

    return (
      <Link key={ticket.id} href={`/support/tickets/${ticket.id}`}>
        <Card className="cursor-pointer transition-colors hover:bg-muted/30">
          <CardContent className="flex flex-col gap-2 pt-4 sm:flex-row sm:items-start sm:justify-between">
            <div className="flex flex-col gap-1.5 flex-1 min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <Badge className={priorityCfg.className}>
                  {priorityCfg.label}
                </Badge>
                <Badge className={statusCfg.className}>{statusCfg.label}</Badge>
                <Badge variant="outline">
                  {CATEGORY_LABELS[ticket.category] ?? ticket.category}
                </Badge>
                {ticket.ai_handled && (
                  <Badge className="bg-green-100 text-green-800">AI対応済み</Badge>
                )}
                {ticket.escalated && (
                  <Badge className="bg-orange-100 text-orange-800">担当者対応中</Badge>
                )}
              </div>
              <p className="text-sm font-medium truncate">{ticket.subject}</p>
              <p className="text-xs text-muted-foreground">
                受付: {formatDate(ticket.created_at)}
              </p>
            </div>
            <div className="flex flex-row sm:flex-col items-center sm:items-end gap-2 shrink-0">
              {slaRemaining && (
                <Badge className={slaRemaining.className}>
                  {slaRemaining.label}
                </Badge>
              )}
              <span className="text-xs text-muted-foreground">
                #{ticket.ticket_number}
              </span>
            </div>
          </CardContent>
        </Card>
      </Link>
    );
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold">サポートチケット</h1>
        <p className="text-sm text-muted-foreground">
          AIが自動対応したチケットと、担当者対応中のチケットを管理します。
        </p>
      </div>

      <Tabs
        defaultValue="all"
        onValueChange={(v) => handleTabChange(v as FilterTab)}
      >
        <TabsList className="flex-wrap h-auto gap-1">
          <TabsTrigger value="all">すべて</TabsTrigger>
          <TabsTrigger value="open">未対応</TabsTrigger>
          <TabsTrigger value="ai_responded">AI対応済み</TabsTrigger>
          <TabsTrigger value="escalated">担当者対応中</TabsTrigger>
        </TabsList>

        {(["all", "open", "ai_responded", "escalated"] as const).map((tab) => (
          <TabsContent key={tab} value={tab} className="space-y-3 mt-4">
            {loading ? (
              <div className="space-y-3">
                {Array.from({ length: 5 }).map((_, i) => (
                  <TicketRowSkeleton key={i} />
                ))}
              </div>
            ) : error ? (
              <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
                {error}
              </div>
            ) : tickets.length === 0 ? (
              <Card>
                <CardContent className="flex flex-col items-center gap-4 py-10 text-center">
                  <p className="text-sm text-muted-foreground">
                    {tab === "all"
                      ? "現在、チケットはありません。"
                      : "該当するチケットはありません。"}
                  </p>
                </CardContent>
              </Card>
            ) : (
              <div className="space-y-3">
                {tickets.map(renderTicketCard)}
              </div>
            )}
          </TabsContent>
        ))}
      </Tabs>

      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between">
          <Button
            variant="outline"
            size="sm"
            disabled={offset === 0 || loading}
            onClick={() => setOffset((prev) => Math.max(0, prev - PAGE_SIZE))}
          >
            前へ
          </Button>
          <span className="text-sm text-muted-foreground">
            {currentPage} / {totalPages} ページ（全{total}件）
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={!hasMore || loading}
            onClick={() => setOffset((prev) => prev + PAGE_SIZE)}
          >
            次へ
          </Button>
        </div>
      )}
    </div>
  );
}
