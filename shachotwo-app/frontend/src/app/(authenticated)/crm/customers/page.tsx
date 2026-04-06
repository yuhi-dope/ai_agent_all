"use client";

import { useEffect, useState } from "react";
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
import { Input } from "@/components/ui/input";

// ---------- 型定義 ----------

interface Customer {
  id: string;
  customer_company_name: string;
  industry: string;
  plan: string;
  mrr: number;
  health_score: number;
  status: string;
  onboarded_at: string | null;
  created_at: string;
}

interface CustomerListResponse {
  items: Customer[];
  total: number;
  has_more: boolean;
}

// ---------- ヘルパー ----------

function healthScoreLabel(score: number): { label: string; className: string } {
  if (score >= 80) return { label: "良好", className: "bg-green-100 text-green-800" };
  if (score >= 60) return { label: "注意", className: "bg-yellow-100 text-yellow-800" };
  return { label: "リスク", className: "bg-red-100 text-red-800" };
}

function statusBadge(status: string): { label: string; variant: "default" | "secondary" | "outline" | "destructive" } {
  switch (status) {
    case "active":
      return { label: "利用中", variant: "default" };
    case "onboarding":
      return { label: "導入中", variant: "secondary" };
    case "at_risk":
      return { label: "解約リスク", variant: "destructive" };
    case "churned":
      return { label: "解約済み", variant: "outline" };
    default:
      return { label: status, variant: "outline" };
  }
}

function planLabel(plan: string): string {
  switch (plan) {
    case "brain":
      return "ブレイン";
    case "bpo_core":
      return "業務自動化コア";
    case "enterprise":
      return "エンタープライズ";
    default:
      return plan;
  }
}

function formatMrr(mrr: number): string {
  return `¥${mrr.toLocaleString()}`;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("ja-JP", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

// ---------- スケルトン ----------

function CustomerCardSkeleton() {
  return (
    <Card>
      <CardContent className="pt-4">
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <div className="h-5 w-40 animate-pulse rounded bg-muted" />
            <div className="h-5 w-16 animate-pulse rounded-full bg-muted" />
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-4 w-full animate-pulse rounded bg-muted" />
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------- ヘルススコアバー ----------

function HealthScoreBar({ score }: { score: number }) {
  const { label, className } = healthScoreLabel(score);
  const barColor =
    score >= 80
      ? "bg-green-500"
      : score >= 60
      ? "bg-yellow-500"
      : "bg-red-500";

  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 overflow-hidden rounded-full bg-muted h-2">
        <div
          className={`h-2 rounded-full transition-all ${barColor}`}
          style={{ width: `${Math.max(score, 2)}%` }}
        />
      </div>
      <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${className}`}>
        {score}点 / {label}
      </span>
    </div>
  );
}

// ---------- ページ ----------

export default function CrmCustomersPage() {
  const { session } = useAuth();
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");

  useEffect(() => {
    if (!session?.access_token) return;

    async function fetchCustomers() {
      setLoading(true);
      setError(null);
      try {
        const params: Record<string, string> = {
          sort_by: "health_score",
          order: "asc",
          limit: "50",
        };
        if (statusFilter !== "all") {
          params.status = statusFilter;
        }
        if (search.trim()) {
          params.search = search.trim();
        }

        const res = await apiFetch<CustomerListResponse>("/crm/customers", {
          token: session?.access_token,
          params,
        });
        setCustomers(res.items);
        setTotal(res.total);
      } catch {
        setError("顧客一覧の取得に失敗しました。しばらく経ってから再度お試しください。");
      } finally {
        setLoading(false);
      }
    }

    fetchCustomers();
  }, [session?.access_token, statusFilter, search]);

  const statusOptions = [
    { value: "all", label: "すべて" },
    { value: "onboarding", label: "導入中" },
    { value: "active", label: "利用中" },
    { value: "at_risk", label: "解約リスク" },
    { value: "churned", label: "解約済み" },
  ];

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      {/* ページヘッダー */}
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold">顧客管理</h1>
        <p className="text-sm text-muted-foreground">
          {total !== null ? `${total}社` : ""}　ヘルススコアが低い顧客を優先表示しています。
        </p>
      </div>

      {/* フィルター・検索 */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <Input
          className="w-full sm:max-w-xs"
          placeholder="例: 株式会社〇〇、建設業"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div className="flex flex-wrap gap-2">
          {statusOptions.map((opt) => (
            <Button
              key={opt.value}
              variant={statusFilter === opt.value ? "default" : "outline"}
              size="sm"
              onClick={() => setStatusFilter(opt.value)}
            >
              {opt.label}
            </Button>
          ))}
        </div>
      </div>

      {/* エラー */}
      {error && (
        <Card className="border-destructive/50 bg-destructive/5">
          <CardContent className="pt-4">
            <p className="text-sm text-destructive">{error}</p>
          </CardContent>
        </Card>
      )}

      {/* サマリーカード */}
      {!loading && customers.length > 0 && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Card>
            <CardHeader className="pb-1 pt-4">
              <CardDescription className="text-xs">総顧客数</CardDescription>
            </CardHeader>
            <CardContent className="pb-4">
              <p className="text-2xl font-bold">{total ?? customers.length}社</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-1 pt-4">
              <CardDescription className="text-xs">総MRR</CardDescription>
            </CardHeader>
            <CardContent className="pb-4">
              <p className="text-2xl font-bold">
                ¥{customers.reduce((s, c) => s + c.mrr, 0).toLocaleString()}
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-1 pt-4">
              <CardDescription className="text-xs">解約リスク顧客</CardDescription>
            </CardHeader>
            <CardContent className="pb-4">
              <p className="text-2xl font-bold text-destructive">
                {customers.filter((c) => c.status === "at_risk").length}社
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-1 pt-4">
              <CardDescription className="text-xs">平均ヘルススコア</CardDescription>
            </CardHeader>
            <CardContent className="pb-4">
              <p className="text-2xl font-bold">
                {Math.round(
                  customers.reduce((s, c) => s + c.health_score, 0) /
                    Math.max(customers.length, 1)
                )}
                点
              </p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* 顧客一覧 */}
      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <CustomerCardSkeleton key={i} />
          ))}
        </div>
      ) : customers.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-4 py-12 text-center">
            <p className="text-sm text-muted-foreground">
              {search || statusFilter !== "all"
                ? "条件に一致する顧客が見つかりませんでした。"
                : "まだ顧客が登録されていません。"}
            </p>
            {statusFilter !== "all" || search ? (
              <Button
                variant="outline"
                onClick={() => {
                  setSearch("");
                  setStatusFilter("all");
                }}
              >
                フィルターをリセットする
              </Button>
            ) : null}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {customers.map((customer) => {
            const st = statusBadge(customer.status);
            return (
              <Link key={customer.id} href={`/crm/customers/${customer.id}`}>
                <Card className="cursor-pointer transition-shadow hover:shadow-md">
                  <CardContent className="pt-4 pb-4">
                    <div className="flex flex-col gap-3">
                      {/* 1行目: 会社名 + ステータス */}
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-base font-semibold">
                          {customer.customer_company_name}
                        </span>
                        <Badge variant={st.variant}>{st.label}</Badge>
                        <Badge variant="outline">{planLabel(customer.plan)}</Badge>
                        <span className="ml-auto text-xs text-muted-foreground">
                          {customer.industry}
                        </span>
                      </div>

                      {/* 2行目: ヘルススコアバー */}
                      <HealthScoreBar score={customer.health_score} />

                      {/* 3行目: メタ情報 */}
                      <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
                        <span>
                          MRR:{" "}
                          <span className="font-medium text-foreground">
                            {formatMrr(customer.mrr)}
                          </span>
                        </span>
                        <span>
                          契約開始:{" "}
                          {customer.onboarded_at
                            ? formatDate(customer.onboarded_at)
                            : "導入中"}
                        </span>
                        <span className="ml-auto text-xs text-muted-foreground">
                          詳細を見る →
                        </span>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
