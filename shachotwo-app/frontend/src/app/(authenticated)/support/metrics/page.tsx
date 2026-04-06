"use client";

import { useEffect, useState } from "react";
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

// ---------- Types ----------

interface CSMetrics {
  // CSAT
  csat_score: number | null;           // 1.0-5.0 平均
  csat_response_count: number;

  // 応答時間
  avg_first_response_min: number | null;  // 分
  avg_resolution_min: number | null;      // 分

  // AI対応率
  ai_handled_rate: number | null;         // 0-1
  ai_handled_count: number;
  total_ticket_count: number;

  // SLA
  sla_achievement_rate: number | null;    // 0-1
  sla_breached_count: number;
  sla_total_count: number;

  // 優先度別内訳
  priority_breakdown: {
    urgent: number;
    high: number;
    medium: number;
    low: number;
  };

  // 直近30日のトレンド（日次）
  daily_trend: {
    date: string;
    total: number;
    ai_handled: number;
    escalated: number;
  }[];
}

// ---------- Helpers ----------

function formatMinutes(min: number | null): string {
  if (min === null) return "-";
  if (min < 60) return `${Math.round(min)}分`;
  const h = Math.floor(min / 60);
  const m = Math.round(min % 60);
  if (m === 0) return `${h}時間`;
  return `${h}時間${m}分`;
}

function getStatusColor(
  value: number | null,
  thresholds: { good: number; warn: number },
  higherIsBetter = true
): string {
  if (value === null) return "text-muted-foreground";
  const isGood = higherIsBetter
    ? value >= thresholds.good
    : value <= thresholds.good;
  const isWarn = higherIsBetter
    ? value >= thresholds.warn
    : value <= thresholds.warn;
  if (isGood) return "text-green-600";
  if (isWarn) return "text-yellow-600";
  return "text-destructive";
}

// ---------- Skeleton ----------

function KpiCardSkeleton() {
  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="h-4 w-28 animate-pulse rounded bg-muted" />
      </CardHeader>
      <CardContent>
        <div className="h-10 w-20 animate-pulse rounded bg-muted" />
        <div className="mt-2 h-3 w-32 animate-pulse rounded bg-muted" />
      </CardContent>
    </Card>
  );
}

// ---------- Page ----------

export default function SupportMetricsPage() {
  const { session } = useAuth();
  const [metrics, setMetrics] = useState<CSMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!session?.access_token) return;
    setLoading(true);
    setError(null);
    apiFetch<CSMetrics>("/support/metrics", {
      token: session.access_token,
      params: { period: "30d" },
    })
      .then(setMetrics)
      .catch(() =>
        setError(
          "KPI の取得に失敗しました。しばらく経ってから再度お試しください"
        )
      )
      .finally(() => setLoading(false));
  }, [session?.access_token]);

  // SLA達成率の色判定（目標95%以上）
  const slaColor = getStatusColor(
    metrics?.sla_achievement_rate !== null && metrics?.sla_achievement_rate !== undefined
      ? metrics.sla_achievement_rate * 100
      : null,
    { good: 95, warn: 80 }
  );

  // AI対応率の色判定（目標60%以上）
  const aiRateColor = getStatusColor(
    metrics?.ai_handled_rate !== null && metrics?.ai_handled_rate !== undefined
      ? metrics.ai_handled_rate * 100
      : null,
    { good: 60, warn: 40 }
  );

  // CSAT の色判定（目標4.2以上）
  const csatColor = getStatusColor(metrics?.csat_score ?? null, {
    good: 4.2,
    warn: 3.5,
  });

  // 平均初回応答時間の色判定（目標5分以下）
  const frtColor = getStatusColor(
    metrics?.avg_first_response_min ?? null,
    { good: 5, warn: 15 },
    false
  );

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <div>
        <h1 className="text-2xl font-bold">CS KPI</h1>
        <p className="text-sm text-muted-foreground">
          直近 30 日間のカスタマーサポートパフォーマンスを確認できます。
        </p>
      </div>

      {/* エラー */}
      {error && (
        <Card className="border-destructive/50 bg-destructive/5">
          <CardContent className="pt-4">
            <p className="text-sm text-destructive">{error}</p>
          </CardContent>
        </Card>
      )}

      {/* KPI カード群 */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {loading ? (
          <>
            <KpiCardSkeleton />
            <KpiCardSkeleton />
            <KpiCardSkeleton />
            <KpiCardSkeleton />
          </>
        ) : (
          <>
            {/* CSAT */}
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>顧客満足度（CSAT）</CardDescription>
              </CardHeader>
              <CardContent>
                <p className={`text-3xl font-bold ${csatColor}`}>
                  {metrics?.csat_score !== null && metrics?.csat_score !== undefined
                    ? metrics.csat_score.toFixed(1)
                    : "-"}
                  {metrics?.csat_score !== null && (
                    <span className="text-base font-normal text-muted-foreground">
                      {" "}/ 5.0
                    </span>
                  )}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  目標: 4.2 以上 ·{" "}
                  {metrics?.csat_response_count ?? 0}件の回答
                </p>
              </CardContent>
            </Card>

            {/* 平均初回応答時間 */}
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>平均初回応答時間</CardDescription>
              </CardHeader>
              <CardContent>
                <p className={`text-3xl font-bold ${frtColor}`}>
                  {formatMinutes(metrics?.avg_first_response_min ?? null)}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  目標: 5分以内
                </p>
              </CardContent>
            </Card>

            {/* AI 対応率 */}
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>AI 自動対応率</CardDescription>
              </CardHeader>
              <CardContent>
                <p className={`text-3xl font-bold ${aiRateColor}`}>
                  {metrics?.ai_handled_rate !== null && metrics?.ai_handled_rate !== undefined
                    ? `${Math.round(metrics.ai_handled_rate * 100)}%`
                    : "-"}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  目標: 60% 以上 · {metrics?.ai_handled_count ?? 0}/
                  {metrics?.total_ticket_count ?? 0}件
                </p>
              </CardContent>
            </Card>

            {/* SLA 達成率 */}
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>SLA 達成率</CardDescription>
              </CardHeader>
              <CardContent>
                <p className={`text-3xl font-bold ${slaColor}`}>
                  {metrics?.sla_achievement_rate !== null && metrics?.sla_achievement_rate !== undefined
                    ? `${Math.round(metrics.sla_achievement_rate * 100)}%`
                    : "-"}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  目標: 95% 以上 · 超過{" "}
                  {metrics?.sla_breached_count ?? 0}件
                </p>
              </CardContent>
            </Card>
          </>
        )}
      </div>

      {/* 平均解決時間 */}
      {!loading && metrics && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Card>
            <CardHeader className="pb-2">
              <CardDescription>平均解決時間</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold">
                {formatMinutes(metrics.avg_resolution_min)}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">目標: 4時間以内</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardDescription>チケット総数（直近30日）</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold">
                {metrics.total_ticket_count.toLocaleString()}件
              </p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* 優先度別内訳 */}
      {!loading && metrics && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg font-semibold">優先度別チケット数</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <div className="text-center">
                <Badge className="bg-red-100 text-red-800 text-sm px-3 py-1">
                  緊急
                </Badge>
                <p className="mt-2 text-2xl font-bold">
                  {metrics.priority_breakdown.urgent}
                </p>
              </div>
              <div className="text-center">
                <Badge className="bg-orange-100 text-orange-800 text-sm px-3 py-1">
                  高
                </Badge>
                <p className="mt-2 text-2xl font-bold">
                  {metrics.priority_breakdown.high}
                </p>
              </div>
              <div className="text-center">
                <Badge className="bg-yellow-100 text-yellow-800 text-sm px-3 py-1">
                  中
                </Badge>
                <p className="mt-2 text-2xl font-bold">
                  {metrics.priority_breakdown.medium}
                </p>
              </div>
              <div className="text-center">
                <Badge className="bg-gray-100 text-gray-700 text-sm px-3 py-1">
                  低
                </Badge>
                <p className="mt-2 text-2xl font-bold">
                  {metrics.priority_breakdown.low}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* 日次トレンド（簡易テキスト表） */}
      {!loading && metrics && metrics.daily_trend.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg font-semibold">直近14日間 チケット推移</CardTitle>
          </CardHeader>
          <CardContent>
            {/* スマホではカード、PCでは表 */}
            <div className="hidden sm:block overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left">
                    <th className="pb-2 pr-4 font-medium text-muted-foreground">日付</th>
                    <th className="pb-2 pr-4 font-medium text-muted-foreground">総数</th>
                    <th className="pb-2 pr-4 font-medium text-muted-foreground">AI対応</th>
                    <th className="pb-2 font-medium text-muted-foreground">エスカレーション</th>
                  </tr>
                </thead>
                <tbody>
                  {metrics.daily_trend.slice(-14).map((row) => (
                    <tr key={row.date} className="border-b last:border-0">
                      <td className="py-2 pr-4 text-muted-foreground">
                        {new Date(row.date).toLocaleDateString("ja-JP", {
                          month: "2-digit",
                          day: "2-digit",
                        })}
                      </td>
                      <td className="py-2 pr-4 font-medium">{row.total}</td>
                      <td className="py-2 pr-4">
                        <span className="text-green-700">{row.ai_handled}</span>
                      </td>
                      <td className="py-2">
                        <span className={row.escalated > 0 ? "text-orange-700" : "text-muted-foreground"}>
                          {row.escalated}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* モバイル: カード形式 */}
            <div className="space-y-2 sm:hidden">
              {metrics.daily_trend.slice(-7).map((row) => (
                <div
                  key={row.date}
                  className="flex items-center justify-between rounded-lg border px-3 py-2 text-sm"
                >
                  <span className="text-muted-foreground">
                    {new Date(row.date).toLocaleDateString("ja-JP", {
                      month: "2-digit",
                      day: "2-digit",
                    })}
                  </span>
                  <div className="flex gap-3">
                    <span>総数: <strong>{row.total}</strong></span>
                    <span className="text-green-700">AI: {row.ai_handled}</span>
                    {row.escalated > 0 && (
                      <span className="text-orange-700">拡大: {row.escalated}</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
