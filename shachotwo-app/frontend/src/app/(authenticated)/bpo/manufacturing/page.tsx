"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Select } from "@/components/ui/select";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";

// ---------- 型定義 ----------

interface ManufacturingQuote {
  id: string;
  quote_number: string;
  customer_name: string;
  product_name: string;
  material: string;
  quantity: number;
  total_amount: number | null;
  status: string;
  created_at: string;
}

// ---------- ステータス設定 ----------

const STATUS_LABELS: Record<string, { label: string; className: string }> = {
  draft: { label: "下書き", className: "bg-secondary text-secondary-foreground" },
  sent: { label: "送付済み", className: "bg-secondary text-secondary-foreground" },
  won: { label: "受注", className: "bg-green-100 text-green-800" },
  lost: { label: "失注", className: "bg-destructive/10 text-destructive" },
};

const ALL_STATUSES = [
  { value: "", label: "すべて" },
  { value: "draft", label: "下書き" },
  { value: "sent", label: "送付済み" },
  { value: "won", label: "受注" },
  { value: "lost", label: "失注" },
];

// ---------- 日付フォーマット ----------

function formatDate(isoString: string): string {
  try {
    const d = new Date(isoString);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  } catch {
    return isoString;
  }
}

// ---------- スケルトン ----------

function SkeletonCard() {
  return (
    <Card>
      <CardContent className="py-4">
        <div className="flex items-center justify-between gap-4">
          <div className="space-y-2 flex-1">
            <div className="h-4 w-32 rounded bg-muted animate-pulse" />
            <div className="h-3 w-48 rounded bg-muted animate-pulse" />
            <div className="h-3 w-24 rounded bg-muted animate-pulse" />
          </div>
          <div className="h-6 w-16 rounded bg-muted animate-pulse" />
        </div>
      </CardContent>
    </Card>
  );
}

// ---------- Page ----------

export default function ManufacturingQuotesPage() {
  const { session } = useAuth();
  const [quotes, setQuotes] = useState<ManufacturingQuote[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState("");

  useEffect(() => {
    const token = session?.access_token;
    if (!token) return;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const params: Record<string, string> = {};
        if (statusFilter) params.status = statusFilter;
        const data = await apiFetch<ManufacturingQuote[]>(
          "/bpo/manufacturing/quotes",
          { token, params }
        );
        setQuotes(Array.isArray(data) ? data : []);
      } catch {
        setError("見積一覧の取得に失敗しました。しばらく経ってから再度お試しください。");
        setQuotes([]);
      } finally {
        setLoading(false);
      }
    }

    load();
  }, [session?.access_token, statusFilter]);

  return (
    <div className="space-y-6">
      {/* ヘッダー */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">製造業 見積管理</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            仕様から原価計算し、見積書を自動作成します
          </p>
        </div>
        <Link href="/bpo/manufacturing/new">
          <Button>新規見積を作成する</Button>
        </Link>
      </div>

      {/* フィルター */}
      <div className="flex items-center gap-3">
        <span className="text-sm text-muted-foreground shrink-0">ステータス：</span>
        <Select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="w-36"
        >
          {ALL_STATUSES.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </Select>
      </div>

      {/* エラー */}
      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* リスト */}
      {loading ? (
        <div className="space-y-3">
          {[...Array(4)].map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      ) : quotes.length === 0 ? (
        <Card>
          <CardContent className="py-16 text-center space-y-4">
            <p className="text-muted-foreground">
              {statusFilter
                ? "条件に一致する見積がありません"
                : "まだ見積がありません"}
            </p>
            <Link href="/bpo/manufacturing/new">
              <Button>はじめての見積を作成する</Button>
            </Link>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {quotes.map((q) => {
            const st = STATUS_LABELS[q.status] ?? {
              label: q.status,
              className: "bg-secondary text-secondary-foreground",
            };
            return (
              <Link key={q.id} href={`/bpo/manufacturing/${q.id}`}>
                <Card className="cursor-pointer transition-colors hover:bg-accent/50">
                  <CardContent className="py-4">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="space-y-1 min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-xs font-mono text-muted-foreground">
                            {q.quote_number}
                          </span>
                          <span className="font-medium leading-tight">
                            {q.product_name}
                          </span>
                        </div>
                        <p className="text-sm text-muted-foreground">
                          {q.customer_name} / {q.material} / {q.quantity.toLocaleString()}個
                        </p>
                        <p className="text-xs text-muted-foreground">
                          作成日: {formatDate(q.created_at)}
                        </p>
                      </div>
                      <div className="flex items-center gap-3 shrink-0">
                        {q.total_amount != null && (
                          <span className="text-lg font-semibold">
                            ¥{Math.round(q.total_amount).toLocaleString()}
                          </span>
                        )}
                        <span
                          className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${st.className}`}
                        >
                          {st.label}
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
