"use client";

import { useEffect, useState } from "react";
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

// ---------- Types ----------

interface UpsellCandidate {
  customer_id: string;
  customer_company_name: string;
  industry: string;
  health_score: number;
  mrr: number;
  active_modules: string[];
  recommended_modules: string[];
  trigger_reason: string;       // 拡張タイミングの理由
  expansion_score: number;      // 0-100 拡張確度
  last_activity_at: string | null;
}

// ---------- Config ----------

const INDUSTRY_LABELS: Record<string, string> = {
  construction: "建設業",
  manufacturing: "製造業",
  dental: "歯科",
  restaurant: "飲食業",
  realestate: "不動産",
  professional: "士業",
  nursing: "介護",
  logistics: "物流",
  clinic: "医療クリニック",
  pharmacy: "薬局",
  beauty: "美容・エステ",
  auto_repair: "自動車整備",
  hotel: "ホテル・旅館",
  ecommerce: "EC・小売",
  staffing: "人材派遣",
  architecture: "建築設計",
};

const MODULE_LABELS: Record<string, string> = {
  brain: "ブレイン",
  bpo_core: "業務自動化（コア）",
  bpo_additional: "業務自動化（追加）",
  backoffice: "バックオフィス",
};

function formatModuleName(module: string): string {
  return MODULE_LABELS[module] ?? module;
}

function getExpansionScoreColor(score: number): string {
  if (score >= 70) return "text-green-600";
  if (score >= 40) return "text-yellow-600";
  return "text-muted-foreground";
}

function getExpansionScoreBadge(score: number): {
  label: string;
  className: string;
} {
  if (score >= 70)
    return { label: "拡張確度：高", className: "bg-green-100 text-green-800" };
  if (score >= 40)
    return { label: "拡張確度：中", className: "bg-yellow-100 text-yellow-800" };
  return {
    label: "拡張確度：低",
    className: "bg-gray-100 text-gray-700",
  };
}

function formatDate(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleDateString("ja-JP", {
    month: "2-digit",
    day: "2-digit",
  });
}

// ---------- Skeleton ----------

function CandidateCardSkeleton() {
  return (
    <Card>
      <CardContent className="space-y-3 pt-4">
        <div className="flex flex-wrap gap-2">
          <div className="h-5 w-16 animate-pulse rounded-full bg-muted" />
          <div className="h-5 w-20 animate-pulse rounded-full bg-muted" />
        </div>
        <div className="h-5 w-48 animate-pulse rounded bg-muted" />
        <div className="h-4 w-full animate-pulse rounded bg-muted" />
        <div className="h-4 w-3/4 animate-pulse rounded bg-muted" />
      </CardContent>
    </Card>
  );
}

// ---------- Page ----------

export default function UpsellPage() {
  const { session } = useAuth();
  const [candidates, setCandidates] = useState<UpsellCandidate[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const PAGE_SIZE = 10;

  useEffect(() => {
    if (!session?.access_token) return;
    setLoading(true);
    setError(null);
    apiFetch<PaginatedResponse<UpsellCandidate>>("/upsell/opportunities", {
      token: session.access_token,
      params: {
        limit: String(PAGE_SIZE),
        offset: String(offset),
        sort: "expansion_score",
      },
    })
      .then((res) => {
        setCandidates(res.items);
        setTotal(res.total);
      })
      .catch(() =>
        setError(
          "アップセル候補の取得に失敗しました。しばらく経ってから再度お試しください"
        )
      )
      .finally(() => setLoading(false));
  }, [session?.access_token, offset]);

  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;
  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold">アップセル候補</h1>
        <p className="text-sm text-muted-foreground">
          AIが利用データを分析し、追加モジュールの提案に適したタイミングの顧客を自動検出しています。
          商談・提案はコンサルタントが担当します。
        </p>
      </div>

      {error && (
        <Card className="border-destructive/50 bg-destructive/5">
          <CardContent className="pt-4">
            <p className="text-sm text-destructive">{error}</p>
          </CardContent>
        </Card>
      )}

      {loading ? (
        <div className="space-y-4">
          {Array.from({ length: 5 }).map((_, i) => (
            <CandidateCardSkeleton key={i} />
          ))}
        </div>
      ) : candidates.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-4 py-10 text-center">
            <p className="text-sm text-muted-foreground">
              現在、アップセル候補の顧客はいません。
              顧客の利用データが蓄積されると、AIが自動で候補を検出します。
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {candidates.map((c) => {
            const scoreBadge = getExpansionScoreBadge(c.expansion_score);
            return (
              <Card key={c.customer_id} className="transition-colors hover:bg-muted/20">
                <CardContent className="flex flex-col gap-4 pt-4 sm:flex-row sm:items-start sm:justify-between">
                  <div className="space-y-2 flex-1 min-w-0">
                    {/* バッジ行 */}
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge className={scoreBadge.className}>
                        {scoreBadge.label}
                      </Badge>
                      <Badge variant="outline">
                        {INDUSTRY_LABELS[c.industry] ?? c.industry}
                      </Badge>
                    </div>

                    {/* 会社名 */}
                    <p className="text-base font-semibold">
                      {c.customer_company_name}
                    </p>

                    {/* 拡張タイミング理由 */}
                    <p className="text-sm text-muted-foreground">
                      {c.trigger_reason}
                    </p>

                    {/* 現在のモジュール → 推奨モジュール */}
                    <div className="flex flex-wrap items-center gap-1 text-xs">
                      <span className="text-muted-foreground">現在:</span>
                      {c.active_modules.map((m) => (
                        <Badge key={m} variant="secondary" className="text-[11px]">
                          {formatModuleName(m)}
                        </Badge>
                      ))}
                      <span className="text-muted-foreground mx-1">→ 推奨:</span>
                      {c.recommended_modules.map((m) => (
                        <Badge
                          key={m}
                          className="bg-primary/10 text-primary text-[11px]"
                        >
                          {formatModuleName(m)}
                        </Badge>
                      ))}
                    </div>

                    {/* MRR・最終アクティビティ */}
                    <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
                      <span>
                        現在の月額:{" "}
                        <span className="font-medium text-foreground">
                          ¥{c.mrr.toLocaleString()}
                        </span>
                      </span>
                      <span>
                        最終利用日: {formatDate(c.last_activity_at)}
                      </span>
                      <span>
                        ヘルススコア:{" "}
                        <span
                          className={
                            c.health_score >= 70
                              ? "font-medium text-green-700"
                              : c.health_score >= 40
                              ? "font-medium text-yellow-700"
                              : "font-medium text-destructive"
                          }
                        >
                          {c.health_score}
                        </span>
                        /100
                      </span>
                    </div>
                  </div>

                  {/* アクションボタン */}
                  <div className="flex flex-row gap-2 sm:flex-col sm:items-end shrink-0">
                    <Link href={`/upsell/${c.customer_id}`}>
                      <Button size="sm" className="w-full sm:w-auto">
                        ブリーフィングを見る
                      </Button>
                    </Link>
                    <Button
                      size="sm"
                      variant="outline"
                      className="w-full sm:w-auto"
                      onClick={() => {
                        // 商談予約（カレンダー連携 or 外部リンク）
                        window.open(
                          `https://calendar.google.com/calendar/r/eventedit?text=${encodeURIComponent(
                            `${c.customer_company_name} アップセル商談`
                          )}`,
                          "_blank"
                        );
                      }}
                    >
                      商談を予約する
                    </Button>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

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
            disabled={offset + PAGE_SIZE >= total || loading}
            onClick={() => setOffset((prev) => prev + PAGE_SIZE)}
          >
            次へ
          </Button>
        </div>
      )}
    </div>
  );
}
