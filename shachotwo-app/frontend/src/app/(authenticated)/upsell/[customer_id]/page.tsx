"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
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

// ---------- Types ----------

interface RecommendedModule {
  module_id: string;
  module_name: string;
  reason: string;
  monthly_price: number;
  expected_roi: string;
}

interface QuotationLine {
  label: string;
  monthly_price: number;
  is_new: boolean;
}

interface UpsellBriefing {
  customer_id: string;
  customer_company_name: string;
  industry: string;
  health_score: number;
  mrr: number;
  active_modules: string[];

  // AI生成の顧客分析
  customer_analysis: {
    usage_summary: string;        // 利用状況サマリ
    strengths: string[];          // うまく活用できている点
    pain_points: string[];        // まだ課題として残っている点
    expansion_opportunities: string[];  // 拡張機会
  };

  // 推奨アクション
  recommended_actions: {
    priority: "high" | "medium" | "low";
    action: string;
    rationale: string;
  }[];

  // 推奨モジュール
  recommended_modules: RecommendedModule[];

  // 見積シミュレーション
  quotation_simulation: {
    current_mrr: number;
    lines: QuotationLine[];
    new_total_mrr: number;
    annual_saving_estimate: number | null;
    roi_summary: string;
  };

  generated_at: string;
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

const PRIORITY_CONFIG = {
  high: { label: "優先度：高", className: "bg-red-100 text-red-800" },
  medium: { label: "優先度：中", className: "bg-yellow-100 text-yellow-800" },
  low: { label: "優先度：低", className: "bg-gray-100 text-gray-700" },
};

// ---------- Skeleton ----------

function BriefingSkeleton() {
  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className="space-y-2">
        <div className="h-8 w-64 animate-pulse rounded bg-muted" />
        <div className="h-4 w-48 animate-pulse rounded bg-muted" />
      </div>
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="h-32 w-full animate-pulse rounded-lg bg-muted" />
      ))}
      <p className="text-center text-sm text-muted-foreground">
        AIが顧客データを分析しています。少しお待ちください...
      </p>
    </div>
  );
}

// ---------- Page ----------

export default function UpsellBriefingPage() {
  const params = useParams<{ customer_id: string }>();
  const router = useRouter();
  const { session } = useAuth();
  const [briefing, setBriefing] = useState<UpsellBriefing | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [regenerating, setRegenerating] = useState(false);
  const [regenSuccess, setRegenSuccess] = useState(false);

  useEffect(() => {
    if (!session?.access_token || !params.customer_id) return;
    const token = session.access_token;
    const customerId = params.customer_id;
    setLoading(true);
    setError(null);
    apiFetch<UpsellBriefing>(`/upsell/customers/${customerId}/briefing`, {
      token,
    })
      .then(setBriefing)
      .catch(() =>
        setError(
          "ブリーフィングの取得に失敗しました。しばらく経ってから再度お試しください"
        )
      )
      .finally(() => setLoading(false));
  }, [session?.access_token, params.customer_id]);

  async function handleRegenerate() {
    if (!session?.access_token) return;
    const token = session.access_token;
    const customerId = params.customer_id;
    setRegenerating(true);
    setRegenSuccess(false);
    try {
      await apiFetch<UpsellBriefing>(
        `/upsell/customers/${customerId}/briefing/regenerate`,
        { token, method: "POST", body: {} }
      );
      // 再取得
      const updated = await apiFetch<UpsellBriefing>(
        `/upsell/customers/${customerId}/briefing`,
        { token }
      );
      setBriefing(updated);
      setRegenSuccess(true);
    } catch {
      setError(
        "再生成に失敗しました。しばらく経ってから再度お試しください"
      );
    } finally {
      setRegenerating(false);
    }
  }

  if (loading) return <BriefingSkeleton />;

  if (error || !briefing) {
    return (
      <div className="mx-auto max-w-4xl">
        <Card className="border-destructive/50 bg-destructive/5">
          <CardContent className="py-8 text-center">
            <p className="text-sm text-destructive">
              {error ?? "ブリーフィングが見つかりませんでした。"}
            </p>
            <Button
              variant="outline"
              className="mt-4"
              onClick={() => router.push("/upsell")}
            >
              アップセル候補一覧に戻る
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  const { quotation_simulation: sim } = briefing;
  const mrr_increase = sim.new_total_mrr - sim.current_mrr;

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      {/* ヘッダー */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <button
              type="button"
              className="hover:text-foreground transition-colors"
              onClick={() => router.push("/upsell")}
            >
              アップセル候補
            </button>
            <span>/</span>
            <span>{briefing.customer_company_name}</span>
          </div>
          <h1 className="text-2xl font-bold">
            {briefing.customer_company_name} — コンサルブリーフィング
          </h1>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">
              {INDUSTRY_LABELS[briefing.industry] ?? briefing.industry}
            </Badge>
            <Badge
              className={
                briefing.health_score >= 70
                  ? "bg-green-100 text-green-800"
                  : briefing.health_score >= 40
                  ? "bg-yellow-100 text-yellow-800"
                  : "bg-red-100 text-red-800"
              }
            >
              ヘルス: {briefing.health_score}/100
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground">
            AI 生成日時:{" "}
            {new Date(briefing.generated_at).toLocaleDateString("ja-JP", {
              year: "numeric",
              month: "2-digit",
              day: "2-digit",
              hour: "2-digit",
              minute: "2-digit",
            })}
          </p>
        </div>

        <div className="flex flex-col gap-2 items-start sm:items-end shrink-0">
          <Button
            variant="outline"
            size="sm"
            onClick={handleRegenerate}
            disabled={regenerating}
          >
            {regenerating ? (
              <>
                <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                再生成中...
              </>
            ) : (
              "AIで再生成する"
            )}
          </Button>
          {regenSuccess && (
            <p className="text-xs text-green-700">ブリーフィングを更新しました。</p>
          )}
        </div>
      </div>

      {/* 顧客分析 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg font-semibold">顧客利用状況の分析</CardTitle>
          <CardDescription>AIが利用データを分析した結果です。</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm">{briefing.customer_analysis.usage_summary}</p>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="space-y-2">
              <p className="text-sm font-medium text-green-700">うまく活用できている点</p>
              <ul className="space-y-1">
                {briefing.customer_analysis.strengths.map((s, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <span className="mt-0.5 text-green-600 shrink-0">✓</span>
                    <span>{s}</span>
                  </li>
                ))}
              </ul>
            </div>

            <div className="space-y-2">
              <p className="text-sm font-medium text-orange-700">残っている課題</p>
              <ul className="space-y-1">
                {briefing.customer_analysis.pain_points.map((p, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <span className="mt-0.5 text-orange-600 shrink-0">!</span>
                    <span>{p}</span>
                  </li>
                ))}
              </ul>
            </div>

            <div className="space-y-2">
              <p className="text-sm font-medium text-primary">拡張の機会</p>
              <ul className="space-y-1">
                {briefing.customer_analysis.expansion_opportunities.map((o, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <span className="mt-0.5 text-primary shrink-0">→</span>
                    <span>{o}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* 推奨アクション */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg font-semibold">推奨アクション</CardTitle>
          <CardDescription>商談前に準備・実施すべき事項です。</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {briefing.recommended_actions.map((act, i) => {
            const prioCfg = PRIORITY_CONFIG[act.priority];
            return (
              <div key={i} className="flex flex-col gap-1 rounded-lg border p-3 sm:flex-row sm:items-start sm:gap-3">
                <Badge className={`${prioCfg.className} shrink-0`}>
                  {prioCfg.label}
                </Badge>
                <div>
                  <p className="text-sm font-medium">{act.action}</p>
                  <p className="text-xs text-muted-foreground">{act.rationale}</p>
                </div>
              </div>
            );
          })}
        </CardContent>
      </Card>

      {/* 推奨モジュール */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg font-semibold">提案するモジュール</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {briefing.recommended_modules.map((mod) => (
            <div
              key={mod.module_id}
              className="flex flex-col gap-2 rounded-lg border p-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="space-y-0.5">
                <p className="text-sm font-medium">{mod.module_name}</p>
                <p className="text-xs text-muted-foreground">{mod.reason}</p>
                <p className="text-xs text-primary">{mod.expected_roi}</p>
              </div>
              <p className="shrink-0 text-sm font-semibold">
                ¥{mod.monthly_price.toLocaleString()}/月
              </p>
            </div>
          ))}
        </CardContent>
      </Card>

      {/* 見積シミュレーション */}
      <Card className="border-primary/20 bg-primary/5">
        <CardHeader>
          <CardTitle className="text-lg font-semibold">見積シミュレーション</CardTitle>
          <CardDescription>{sim.roi_summary}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            {sim.lines.map((line, i) => (
              <div
                key={i}
                className={`flex items-center justify-between text-sm ${
                  line.is_new ? "font-medium text-primary" : ""
                }`}
              >
                <span className="flex items-center gap-2">
                  {line.is_new && (
                    <Badge className="bg-primary/10 text-primary text-[11px]">新規</Badge>
                  )}
                  {line.label}
                </span>
                <span>¥{line.monthly_price.toLocaleString()}/月</span>
              </div>
            ))}
          </div>

          <div className="border-t pt-3 space-y-1">
            <div className="flex items-center justify-between text-sm text-muted-foreground">
              <span>現在の月額合計</span>
              <span>¥{sim.current_mrr.toLocaleString()}</span>
            </div>
            <div className="flex items-center justify-between font-semibold">
              <span>追加後の月額合計</span>
              <span className="text-primary">¥{sim.new_total_mrr.toLocaleString()}</span>
            </div>
            <div className="flex items-center justify-between text-sm text-green-700">
              <span>月額増加分</span>
              <span>+¥{mrr_increase.toLocaleString()}</span>
            </div>
            {sim.annual_saving_estimate !== null && (
              <div className="flex items-center justify-between text-sm text-muted-foreground">
                <span>顧客の年間コスト削減見込み</span>
                <span>¥{sim.annual_saving_estimate.toLocaleString()}</span>
              </div>
            )}
          </div>

          <Button
            className="w-full sm:w-auto"
            onClick={() => {
              window.open(
                `https://calendar.google.com/calendar/r/eventedit?text=${encodeURIComponent(
                  `${briefing.customer_company_name} アップセル商談`
                )}`,
                "_blank"
              );
            }}
          >
            商談を予約する
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
