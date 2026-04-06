"use client";

import { useEffect, useState, useCallback } from "react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

// ---------------------------------------------------------------------------
// 型定義
// ---------------------------------------------------------------------------

interface MonthlyForecast {
  month: string;            // "2026-03" など YYYY-MM
  weighted_amount: number;  // 加重予測金額（月額）
  pipeline_amount: number;  // パイプライン総額
  won_amount: number;       // 受注確定金額
  opportunity_count: number;
}

interface ForecastSummary {
  current_pipeline_weighted: number;
  total_won_mrr: number;
  average_deal_size: number;
  win_rate: number;
  monthly_forecasts: MonthlyForecast[];
}

interface Opportunity {
  id: string;
  title: string;
  target_company_name: string;
  monthly_amount: number;
  stage: string;
  probability: number;
  expected_close_date: string | null;
}

// ---------------------------------------------------------------------------
// ヘルパー
// ---------------------------------------------------------------------------

function formatAmount(yen: number): string {
  if (yen >= 100000000) {
    return `¥${(yen / 100000000).toFixed(1)}億`;
  }
  if (yen >= 10000) {
    return `¥${Math.round(yen / 10000)}万`;
  }
  return `¥${yen.toLocaleString()}`;
}

function formatMonth(yyyyMM: string): string {
  const [year, month] = yyyyMM.split("-");
  return `${year}年${parseInt(month)}月`;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("ja-JP", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

const STAGE_LABELS: Record<string, string> = {
  proposal: "提案中",
  quotation: "見積提示",
  negotiation: "交渉中",
  contract: "契約準備",
  won: "受注",
};

const STAGE_COLORS: Record<string, string> = {
  proposal: "bg-blue-100 text-blue-800",
  quotation: "bg-yellow-100 text-yellow-800",
  negotiation: "bg-orange-100 text-orange-800",
  contract: "bg-purple-100 text-purple-800",
  won: "bg-green-100 text-green-800",
};

// ---------------------------------------------------------------------------
// バーチャート（シンプルなCSSバー）
// ---------------------------------------------------------------------------

function ForecastBar({
  forecasts,
  maxAmount,
}: {
  forecasts: MonthlyForecast[];
  maxAmount: number;
}) {
  if (forecasts.length === 0) return null;

  return (
    <div className="space-y-2">
      {forecasts.map((f) => {
        const wonPct = maxAmount > 0 ? (f.won_amount / maxAmount) * 100 : 0;
        const weightedPct =
          maxAmount > 0 ? (f.weighted_amount / maxAmount) * 100 : 0;
        const pipelinePct =
          maxAmount > 0 ? (f.pipeline_amount / maxAmount) * 100 : 0;

        return (
          <div key={f.month} className="space-y-1">
            <div className="flex items-center justify-between text-xs">
              <span className="font-medium w-20 shrink-0">
                {formatMonth(f.month)}
              </span>
              <div className="flex-1 mx-3 relative h-6">
                {/* パイプライン総額（薄い背景） */}
                <div
                  className="absolute inset-y-0 left-0 rounded bg-blue-100"
                  style={{ width: `${pipelinePct}%` }}
                />
                {/* 加重予測 */}
                <div
                  className="absolute inset-y-0 left-0 rounded bg-blue-400"
                  style={{ width: `${weightedPct}%` }}
                />
                {/* 受注確定 */}
                <div
                  className="absolute inset-y-0 left-0 rounded bg-green-500"
                  style={{ width: `${wonPct}%` }}
                />
              </div>
              <div className="text-right min-w-[80px]">
                <span className="text-green-700 font-semibold">
                  {formatAmount(f.won_amount)}
                </span>
                <span className="text-muted-foreground">
                  {" "}+ {formatAmount(f.weighted_amount - f.won_amount)}
                </span>
              </div>
            </div>
          </div>
        );
      })}
      {/* 凡例 */}
      <div className="flex flex-wrap gap-4 pt-2 text-xs text-muted-foreground">
        <div className="flex items-center gap-1.5">
          <div className="h-3 w-3 rounded bg-green-500" />
          <span>受注確定</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="h-3 w-3 rounded bg-blue-400" />
          <span>加重予測（確度×金額）</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="h-3 w-3 rounded bg-blue-100 border border-blue-200" />
          <span>商談総額</span>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// スケルトン
// ---------------------------------------------------------------------------

function ForecastSkeleton() {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="rounded-lg border p-4 space-y-2">
            <div className="h-3 w-20 animate-pulse rounded bg-muted" />
            <div className="h-7 w-24 animate-pulse rounded bg-muted" />
          </div>
        ))}
      </div>
      <div className="rounded-lg border p-4 space-y-3">
        {[1, 2, 3, 4, 5, 6].map((i) => (
          <div key={i} className="flex items-center gap-3">
            <div className="h-4 w-16 animate-pulse rounded bg-muted" />
            <div className="flex-1 h-6 animate-pulse rounded bg-muted" />
            <div className="h-4 w-20 animate-pulse rounded bg-muted" />
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ページ
// ---------------------------------------------------------------------------

export default function ForecastPage() {
  const { session } = useAuth();
  const [summary, setSummary] = useState<ForecastSummary | null>(null);
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchForecast = useCallback(async () => {
    if (!session?.access_token) return;
    const token = session.access_token;
    setLoading(true);
    setError(null);

    try {
      const [forecastRes, oppsRes] = await Promise.allSettled([
        apiFetch<ForecastSummary>("/sales/opportunities/forecast", { token }),
        apiFetch<{ items: Opportunity[]; total: number }>(
          "/sales/opportunities",
          {
            token,
            params: { limit: "50", exclude_lost: "true", exclude_won: "false" },
          }
        ),
      ]);

      if (forecastRes.status === "fulfilled") {
        setSummary(forecastRes.value);
      }
      if (oppsRes.status === "fulfilled") {
        setOpportunities(oppsRes.value.items);
      }
      if (
        forecastRes.status === "rejected" &&
        oppsRes.status === "rejected"
      ) {
        setError(
          "データの取得に失敗しました。しばらく経ってから再度お試しください。"
        );
      }
    } finally {
      setLoading(false);
    }
  }, [session?.access_token]);

  useEffect(() => {
    fetchForecast();
  }, [fetchForecast]);

  // フォールバック: APIがない場合はopportunitiesから計算
  const computedForecasts: MonthlyForecast[] = (() => {
    if (summary?.monthly_forecasts?.length) return summary.monthly_forecasts;

    // opportunities から月別に集計
    const byMonth: Record<string, MonthlyForecast> = {};
    opportunities.forEach((opp) => {
      const closeDate = opp.expected_close_date
        ? opp.expected_close_date.slice(0, 7)
        : new Date().toISOString().slice(0, 7);
      if (!byMonth[closeDate]) {
        byMonth[closeDate] = {
          month: closeDate,
          weighted_amount: 0,
          pipeline_amount: 0,
          won_amount: 0,
          opportunity_count: 0,
        };
      }
      const entry = byMonth[closeDate];
      entry.pipeline_amount += opp.monthly_amount;
      entry.weighted_amount +=
        (opp.monthly_amount * opp.probability) / 100;
      if (opp.stage === "won") {
        entry.won_amount += opp.monthly_amount;
      }
      entry.opportunity_count += 1;
    });

    return Object.values(byMonth).sort((a, b) =>
      a.month.localeCompare(b.month)
    );
  })();

  const maxAmount = Math.max(
    ...computedForecasts.map((f) => f.pipeline_amount),
    1
  );

  const totalWeighted =
    summary?.current_pipeline_weighted ??
    opportunities
      .filter((o) => o.stage !== "won")
      .reduce((s, o) => s + (o.monthly_amount * o.probability) / 100, 0);

  const totalWonMRR =
    summary?.total_won_mrr ??
    opportunities
      .filter((o) => o.stage === "won")
      .reduce((s, o) => s + o.monthly_amount, 0);

  const winRate = summary?.win_rate ?? null;
  const avgDealSize = summary?.average_deal_size ?? null;

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      {/* ヘッダー */}
      <div>
        <h1 className="text-2xl font-bold">売上予測</h1>
        <p className="text-sm text-muted-foreground">
          商談金額×確度の加重予測で今後の売上を見通します。
        </p>
      </div>

      {/* エラー */}
      {error && (
        <div className="rounded-md border border-destructive/50 bg-destructive/5 p-3">
          <p className="text-sm text-destructive">{error}</p>
        </div>
      )}

      {loading ? (
        <ForecastSkeleton />
      ) : (
        <>
          {/* KPI カード */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Card>
              <CardHeader className="pb-1 pt-3 px-3">
                <p className="text-xs text-muted-foreground">受注済み月額合計</p>
              </CardHeader>
              <CardContent className="px-3 pb-3">
                <p className="text-xl font-bold text-green-600">
                  {formatAmount(totalWonMRR)}/月
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-1 pt-3 px-3">
                <p className="text-xs text-muted-foreground">加重予測合計</p>
              </CardHeader>
              <CardContent className="px-3 pb-3">
                <p className="text-xl font-bold text-blue-600">
                  {formatAmount(Math.round(totalWeighted))}/月
                </p>
                <p className="text-[11px] text-muted-foreground">
                  確度×金額で算出
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-1 pt-3 px-3">
                <p className="text-xs text-muted-foreground">平均受注金額</p>
              </CardHeader>
              <CardContent className="px-3 pb-3">
                <p className="text-xl font-bold">
                  {avgDealSize !== null
                    ? `${formatAmount(avgDealSize)}/月`
                    : "-"}
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-1 pt-3 px-3">
                <p className="text-xs text-muted-foreground">受注率</p>
              </CardHeader>
              <CardContent className="px-3 pb-3">
                <p className="text-xl font-bold">
                  {winRate !== null ? `${Math.round(winRate * 100)}%` : "-"}
                </p>
              </CardContent>
            </Card>
          </div>

          {/* 月別予測グラフ */}
          {computedForecasts.length > 0 ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-base font-semibold">
                  月別売上予測
                </CardTitle>
                <CardDescription>
                  受注確定金額と確度加重予測を月別に表示します
                </CardDescription>
              </CardHeader>
              <CardContent>
                <ForecastBar
                  forecasts={computedForecasts}
                  maxAmount={maxAmount}
                />
              </CardContent>
            </Card>
          ) : (
            <Card>
              <CardContent className="flex flex-col items-center gap-4 py-10 text-center">
                <p className="text-sm text-muted-foreground">
                  まだ予測データがありません。
                </p>
                <p className="text-xs text-muted-foreground">
                  商談管理に商談が登録されると、ここに予測グラフが表示されます。
                </p>
              </CardContent>
            </Card>
          )}

          {/* 商談一覧（受注予定日順） */}
          {opportunities.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base font-semibold">
                  進行中の商談
                  <span className="ml-2 text-sm font-normal text-muted-foreground">
                    {opportunities.length}件
                  </span>
                </CardTitle>
                <CardDescription>
                  受注予定日が近い順に表示
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-2">
                {[...opportunities]
                  .sort((a, b) => {
                    if (!a.expected_close_date && !b.expected_close_date)
                      return 0;
                    if (!a.expected_close_date) return 1;
                    if (!b.expected_close_date) return -1;
                    return (
                      new Date(a.expected_close_date).getTime() -
                      new Date(b.expected_close_date).getTime()
                    );
                  })
                  .map((opp) => {
                    const weightedMR = Math.round(
                      (opp.monthly_amount * opp.probability) / 100
                    );
                    return (
                      <div
                        key={opp.id}
                        className="flex flex-col gap-1 rounded-md border p-3 sm:flex-row sm:items-center"
                      >
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium truncate">
                            {opp.target_company_name}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {opp.title}
                          </p>
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge
                            className={`text-[11px] ${
                              STAGE_COLORS[opp.stage] ??
                              "bg-muted text-muted-foreground"
                            }`}
                          >
                            {STAGE_LABELS[opp.stage] ?? opp.stage}
                          </Badge>
                          <span className="text-sm font-semibold">
                            {formatAmount(opp.monthly_amount)}/月
                          </span>
                          <span className="text-xs text-muted-foreground">
                            確度 {opp.probability}%
                          </span>
                          <span className="text-xs text-blue-700 font-medium">
                            予測 {formatAmount(weightedMR)}/月
                          </span>
                          {opp.expected_close_date && (
                            <span className="text-xs text-muted-foreground">
                              予定日: {formatDate(opp.expected_close_date)}
                            </span>
                          )}
                        </div>
                      </div>
                    );
                  })}
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
