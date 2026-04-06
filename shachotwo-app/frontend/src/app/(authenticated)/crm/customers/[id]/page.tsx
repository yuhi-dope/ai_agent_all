"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
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

// ---------- 型定義 ----------

interface CustomerDetail {
  id: string;
  customer_company_name: string;
  industry: string;
  employee_count: number | null;
  plan: string;
  active_modules: string[];
  mrr: number;
  health_score: number;
  nps_score: number | null;
  status: string;
  onboarded_at: string | null;
  churned_at: string | null;
  churn_reason: string | null;
  created_at: string;
}

interface HealthHistory {
  id: string;
  score: number;
  dimensions: {
    usage: number;
    engagement: number;
    support: number;
    nps: number;
    expansion: number;
  };
  risk_factors: string[];
  calculated_at: string;
}

interface ActivityItem {
  id: string;
  event_type: string;
  description: string;
  occurred_at: string;
}

interface SupportTicket {
  id: string;
  ticket_number: string;
  subject: string;
  category: string;
  priority: string;
  status: string;
  ai_handled: boolean;
  created_at: string;
}

interface FeatureRequest {
  id: string;
  title: string;
  category: string;
  priority: string;
  vote_count: number;
  status: string;
  created_at: string;
}

// ---------- ヘルパー ----------

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("ja-JP", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("ja-JP", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function statusBadge(status: string): { label: string; variant: "default" | "secondary" | "outline" | "destructive" } {
  const map: Record<string, { label: string; variant: "default" | "secondary" | "outline" | "destructive" }> = {
    active: { label: "利用中", variant: "default" },
    onboarding: { label: "導入中", variant: "secondary" },
    at_risk: { label: "解約リスク", variant: "destructive" },
    churned: { label: "解約済み", variant: "outline" },
  };
  return map[status] ?? { label: status, variant: "outline" };
}

function planLabel(plan: string): string {
  const map: Record<string, string> = {
    brain: "ブレイン",
    bpo_core: "業務自動化コア",
    enterprise: "エンタープライズ",
  };
  return map[plan] ?? plan;
}

function ticketStatusBadge(status: string): { label: string; className: string } {
  const map: Record<string, { label: string; className: string }> = {
    open: { label: "対応中", className: "bg-yellow-100 text-yellow-800" },
    resolved: { label: "解決済み", className: "bg-green-100 text-green-800" },
    closed: { label: "クローズ", className: "bg-muted text-muted-foreground" },
    escalated: { label: "エスカレ", className: "bg-red-100 text-red-800" },
  };
  return map[status] ?? { label: status, className: "bg-muted text-muted-foreground" };
}

function requestStatusBadge(status: string): { label: string; className: string } {
  const map: Record<string, { label: string; className: string }> = {
    new: { label: "新規", className: "bg-blue-100 text-blue-800" },
    reviewing: { label: "検討中", className: "bg-yellow-100 text-yellow-800" },
    planned: { label: "対応予定", className: "bg-purple-100 text-purple-800" },
    in_progress: { label: "対応中", className: "bg-orange-100 text-orange-800" },
    done: { label: "完了", className: "bg-green-100 text-green-800" },
    declined: { label: "見送り", className: "bg-muted text-muted-foreground" },
  };
  return map[status] ?? { label: status, className: "bg-muted text-muted-foreground" };
}

function healthScoreColor(score: number): string {
  if (score >= 80) return "text-green-600";
  if (score >= 60) return "text-yellow-600";
  return "text-destructive";
}

// ---------- スケルトン ----------

function DetailSkeleton() {
  return (
    <div className="space-y-4">
      <div className="h-8 w-48 animate-pulse rounded bg-muted" />
      <div className="h-4 w-64 animate-pulse rounded bg-muted" />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Card key={i}>
            <CardContent className="pt-4">
              <div className="h-4 w-20 animate-pulse rounded bg-muted mb-2" />
              <div className="h-8 w-16 animate-pulse rounded bg-muted" />
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}

// ---------- ヘルス推移チャート（シンプルなバー） ----------

function HealthHistoryChart({ history }: { history: HealthHistory[] }) {
  if (history.length === 0) {
    return (
      <p className="text-sm text-muted-foreground py-4 text-center">
        ヘルススコアの履歴がありません。
      </p>
    );
  }

  const recent = history.slice(-10);
  const maxScore = 100;

  return (
    <div className="space-y-4">
      {/* バーチャート */}
      <div className="flex items-end gap-1 h-24">
        {recent.map((h) => {
          const pct = Math.max((h.score / maxScore) * 100, 2);
          const color =
            h.score >= 80
              ? "bg-green-400"
              : h.score >= 60
              ? "bg-yellow-400"
              : "bg-red-400";
          return (
            <div
              key={h.id}
              className="flex flex-1 flex-col items-center gap-1"
              title={`${formatDate(h.calculated_at)}: ${h.score}点`}
            >
              <span className="text-[11px] text-muted-foreground">{h.score}</span>
              <div className="w-full flex-1 flex items-end">
                <div
                  className={`w-full rounded-t ${color} transition-all`}
                  style={{ height: `${pct}%` }}
                />
              </div>
              <span className="text-[11px] text-muted-foreground truncate w-full text-center">
                {new Date(h.calculated_at).toLocaleDateString("ja-JP", { month: "numeric", day: "numeric" })}
              </span>
            </div>
          );
        })}
      </div>

      {/* 最新リスク要因 */}
      {recent[recent.length - 1]?.risk_factors?.length > 0 && (
        <div>
          <p className="text-xs font-medium text-muted-foreground mb-2">現在のリスク要因</p>
          <div className="flex flex-wrap gap-2">
            {recent[recent.length - 1].risk_factors.map((r, i) => (
              <span
                key={i}
                className="rounded-full bg-red-100 px-2 py-0.5 text-xs text-red-700"
              >
                {r}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* 5次元スコア */}
      {recent[recent.length - 1]?.dimensions && (
        <div>
          <p className="text-xs font-medium text-muted-foreground mb-2">スコア内訳（最新）</p>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
            {Object.entries(recent[recent.length - 1].dimensions).map(([key, val]) => {
              const labels: Record<string, string> = {
                usage: "利用度",
                engagement: "関与度",
                support: "サポート",
                nps: "NPS",
                expansion: "拡張",
              };
              return (
                <div key={key} className="text-center">
                  <p className="text-xs text-muted-foreground">{labels[key] ?? key}</p>
                  <p className={`text-lg font-bold ${healthScoreColor(val)}`}>{val}</p>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------- ページ ----------

export default function CustomerDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { session } = useAuth();

  const [customer, setCustomer] = useState<CustomerDetail | null>(null);
  const [healthHistory, setHealthHistory] = useState<HealthHistory[]>([]);
  const [timeline, setTimeline] = useState<ActivityItem[]>([]);
  const [tickets, setTickets] = useState<SupportTicket[]>([]);
  const [requests, setRequests] = useState<FeatureRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!session?.access_token || !id) return;

    async function fetchAll() {
      setLoading(true);
      setError(null);
      try {
        const token = session?.access_token;

        const [customerRes, healthRes, timelineRes, ticketsRes, requestsRes] =
          await Promise.allSettled([
            apiFetch<CustomerDetail>(`/crm/customers/${id}`, { token }),
            apiFetch<{ items: HealthHistory[] }>(`/crm/customers/${id}/health`, {
              token,
              params: { limit: "10" },
            }),
            apiFetch<{ items: ActivityItem[] }>(`/crm/customers/${id}/timeline`, {
              token,
              params: { limit: "20" },
            }),
            apiFetch<{ items: SupportTicket[] }>(`/crm/tickets`, {
              token,
              params: { customer_id: id, limit: "20" },
            }),
            apiFetch<{ items: FeatureRequest[] }>(`/crm/requests`, {
              token,
              params: { customer_id: id, limit: "20" },
            }),
          ]);

        if (customerRes.status === "fulfilled") {
          setCustomer(customerRes.value);
        } else {
          setError("顧客情報の取得に失敗しました。");
          return;
        }
        if (healthRes.status === "fulfilled") {
          setHealthHistory(healthRes.value.items);
        }
        if (timelineRes.status === "fulfilled") {
          setTimeline(timelineRes.value.items);
        }
        if (ticketsRes.status === "fulfilled") {
          setTickets(ticketsRes.value.items);
        }
        if (requestsRes.status === "fulfilled") {
          setRequests(requestsRes.value.items);
        }
      } catch {
        setError("データの取得に失敗しました。しばらく経ってから再度お試しください。");
      } finally {
        setLoading(false);
      }
    }

    fetchAll();
  }, [session?.access_token, id]);

  if (loading) {
    return (
      <div className="mx-auto max-w-5xl space-y-6">
        <DetailSkeleton />
      </div>
    );
  }

  if (error || !customer) {
    return (
      <div className="mx-auto max-w-5xl space-y-4">
        <Link href="/crm/customers">
          <Button variant="ghost" size="sm">← 顧客一覧に戻る</Button>
        </Link>
        <Card className="border-destructive/50 bg-destructive/5">
          <CardContent className="pt-4">
            <p className="text-sm text-destructive">
              {error ?? "顧客情報が見つかりませんでした。"}
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const st = statusBadge(customer.status);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      {/* パンくず */}
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Link href="/crm/customers" className="hover:text-foreground transition-colors">
          顧客管理
        </Link>
        <span>/</span>
        <span className="text-foreground">{customer.customer_company_name}</span>
      </div>

      {/* ヘッダー */}
      <div className="flex flex-wrap items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-bold">{customer.customer_company_name}</h1>
            <Badge variant={st.variant}>{st.label}</Badge>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            {customer.industry}　{customer.employee_count ? `/ ${customer.employee_count}名` : ""}　/　{planLabel(customer.plan)}
          </p>
        </div>
        <Link href={`/upsell/${customer.id}`}>
          <Button variant="outline" size="sm">アップセル分析を見る</Button>
        </Link>
      </div>

      {/* KPIカード */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Card>
          <CardHeader className="pb-1 pt-4">
            <CardDescription className="text-xs">MRR</CardDescription>
          </CardHeader>
          <CardContent className="pb-4">
            <p className="text-2xl font-bold">¥{customer.mrr.toLocaleString()}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-1 pt-4">
            <CardDescription className="text-xs">ヘルススコア</CardDescription>
          </CardHeader>
          <CardContent className="pb-4">
            <p className={`text-2xl font-bold ${healthScoreColor(customer.health_score)}`}>
              {customer.health_score}点
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-1 pt-4">
            <CardDescription className="text-xs">NPS</CardDescription>
          </CardHeader>
          <CardContent className="pb-4">
            <p className="text-2xl font-bold">
              {customer.nps_score !== null ? customer.nps_score : "未回答"}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-1 pt-4">
            <CardDescription className="text-xs">利用中モジュール</CardDescription>
          </CardHeader>
          <CardContent className="pb-4">
            <p className="text-2xl font-bold">{customer.active_modules.length}個</p>
          </CardContent>
        </Card>
      </div>

      {/* タブ */}
      <Tabs defaultValue="health">
        <TabsList className="w-full sm:w-auto">
          <TabsTrigger value="health">ヘルス推移</TabsTrigger>
          <TabsTrigger value="timeline">活動履歴</TabsTrigger>
          <TabsTrigger value="tickets">
            チケット
            {tickets.length > 0 && (
              <span className="ml-1 rounded-full bg-muted px-1.5 text-xs">{tickets.length}</span>
            )}
          </TabsTrigger>
          <TabsTrigger value="requests">
            要望
            {requests.length > 0 && (
              <span className="ml-1 rounded-full bg-muted px-1.5 text-xs">{requests.length}</span>
            )}
          </TabsTrigger>
          <TabsTrigger value="contract">契約情報</TabsTrigger>
        </TabsList>

        {/* ヘルス推移 */}
        <TabsContent value="health">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">ヘルススコア推移</CardTitle>
              <CardDescription>過去10回分の推移と要因分析</CardDescription>
            </CardHeader>
            <CardContent>
              <HealthHistoryChart history={healthHistory} />
            </CardContent>
          </Card>
        </TabsContent>

        {/* 活動履歴 */}
        <TabsContent value="timeline">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">活動履歴</CardTitle>
            </CardHeader>
            <CardContent>
              {timeline.length === 0 ? (
                <p className="text-sm text-muted-foreground py-4 text-center">
                  活動履歴がありません。
                </p>
              ) : (
                <div className="space-y-3">
                  {timeline.map((item) => (
                    <div key={item.id} className="flex gap-3">
                      <div className="mt-1 h-2 w-2 shrink-0 rounded-full bg-primary" />
                      <div>
                        <p className="text-sm">{item.description}</p>
                        <p className="text-xs text-muted-foreground">
                          {formatDateTime(item.occurred_at)}
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* チケット履歴 */}
        <TabsContent value="tickets">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">サポートチケット履歴</CardTitle>
            </CardHeader>
            <CardContent>
              {tickets.length === 0 ? (
                <p className="text-sm text-muted-foreground py-4 text-center">
                  チケットはありません。
                </p>
              ) : (
                <div className="space-y-2">
                  {tickets.map((ticket) => {
                    const ts = ticketStatusBadge(ticket.status);
                    return (
                      <div
                        key={ticket.id}
                        className="flex flex-wrap items-start gap-2 rounded-lg border p-3"
                      >
                        <div className="flex-1 min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-xs text-muted-foreground">
                              #{ticket.ticket_number}
                            </span>
                            <span
                              className={`rounded-full px-2 py-0.5 text-xs font-medium ${ts.className}`}
                            >
                              {ts.label}
                            </span>
                            {ticket.ai_handled && (
                              <span className="rounded-full bg-blue-100 px-2 py-0.5 text-xs text-blue-700">
                                AI対応済み
                              </span>
                            )}
                          </div>
                          <p className="mt-1 text-sm font-medium">{ticket.subject}</p>
                          <p className="text-xs text-muted-foreground">
                            {formatDate(ticket.created_at)}
                          </p>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* 要望一覧 */}
        <TabsContent value="requests">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">この顧客の要望</CardTitle>
            </CardHeader>
            <CardContent>
              {requests.length === 0 ? (
                <p className="text-sm text-muted-foreground py-4 text-center">
                  要望はありません。
                </p>
              ) : (
                <div className="space-y-2">
                  {requests.map((req) => {
                    const rs = requestStatusBadge(req.status);
                    return (
                      <div
                        key={req.id}
                        className="flex flex-wrap items-start gap-2 rounded-lg border p-3"
                      >
                        <div className="flex-1 min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <span
                              className={`rounded-full px-2 py-0.5 text-xs font-medium ${rs.className}`}
                            >
                              {rs.label}
                            </span>
                            <span className="text-xs text-muted-foreground">
                              投票数: {req.vote_count}
                            </span>
                          </div>
                          <p className="mt-1 text-sm font-medium">{req.title}</p>
                          <p className="text-xs text-muted-foreground">
                            {formatDate(req.created_at)}
                          </p>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* 契約情報 */}
        <TabsContent value="contract">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">契約情報</CardTitle>
            </CardHeader>
            <CardContent>
              <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <dt className="text-xs text-muted-foreground">プラン</dt>
                  <dd className="mt-1 text-sm font-medium">{planLabel(customer.plan)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">月次経常収益 (MRR)</dt>
                  <dd className="mt-1 text-sm font-medium">¥{customer.mrr.toLocaleString()}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">契約開始日</dt>
                  <dd className="mt-1 text-sm font-medium">
                    {customer.onboarded_at ? formatDate(customer.onboarded_at) : "導入中"}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">登録日</dt>
                  <dd className="mt-1 text-sm font-medium">{formatDate(customer.created_at)}</dd>
                </div>
                {customer.churned_at && (
                  <div>
                    <dt className="text-xs text-muted-foreground">解約日</dt>
                    <dd className="mt-1 text-sm font-medium text-destructive">
                      {formatDate(customer.churned_at)}
                    </dd>
                  </div>
                )}
                {customer.churn_reason && (
                  <div className="sm:col-span-2">
                    <dt className="text-xs text-muted-foreground">解約理由</dt>
                    <dd className="mt-1 text-sm">{customer.churn_reason}</dd>
                  </div>
                )}
                <div className="sm:col-span-2">
                  <dt className="text-xs text-muted-foreground mb-2">利用中モジュール</dt>
                  <dd>
                    {customer.active_modules.length === 0 ? (
                      <span className="text-sm text-muted-foreground">なし</span>
                    ) : (
                      <div className="flex flex-wrap gap-2">
                        {customer.active_modules.map((mod) => (
                          <Badge key={mod} variant="secondary">
                            {mod}
                          </Badge>
                        ))}
                      </div>
                    )}
                  </dd>
                </div>
              </dl>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
