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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

// ---------- 型定義 ----------

interface RevenueSummary {
  mrr: number;
  arr: number;
  nrr: number;               // 例: 1.12 = 112%
  churn_rate: number;        // 例: 0.025 = 2.5%
  new_mrr: number;
  expansion_mrr: number;
  contraction_mrr: number;
  churned_mrr: number;
  active_customers: number;
  at_risk_customers: number;
}

interface MonthlyRevenue {
  month: string;             // "2025-01"
  mrr: number;
  new_mrr: number;
  expansion_mrr: number;
  contraction_mrr: number;
  churned_mrr: number;
  customer_count: number;
}

interface CohortRow {
  cohort_month: string;      // "2024-10"
  customer_count: number;
  months: number[];          // [100, 92, 85, 78, ...] — 各月の残存率 (%)
}

// ---------- ヘルパー ----------

function pct(val: number, digits = 1): string {
  return `${(val * 100).toFixed(digits)}%`;
}

function formatMrr(val: number): string {
  if (val >= 1_000_000) return `¥${(val / 1_000_000).toFixed(1)}M`;
  if (val >= 1_000) return `¥${(val / 1_000).toFixed(0)}K`;
  return `¥${val.toLocaleString()}`;
}

function nrrColor(nrr: number): string {
  if (nrr >= 1.1) return "text-green-600";
  if (nrr >= 1.0) return "text-yellow-600";
  return "text-destructive";
}

function churnColor(rate: number): string {
  if (rate <= 0.03) return "text-green-600";
  if (rate <= 0.05) return "text-yellow-600";
  return "text-destructive";
}

function cohortCellColor(pctVal: number): string {
  if (pctVal >= 90) return "bg-green-600 text-white";
  if (pctVal >= 80) return "bg-green-400 text-white";
  if (pctVal >= 70) return "bg-yellow-300 text-yellow-900";
  if (pctVal >= 60) return "bg-orange-300 text-orange-900";
  return "bg-red-400 text-white";
}

// ---------- スケルトン ----------

function KpiSkeleton() {
  return (
    <Card>
      <CardContent className="pt-4">
        <div className="h-4 w-24 animate-pulse rounded bg-muted mb-2" />
        <div className="h-8 w-20 animate-pulse rounded bg-muted" />
      </CardContent>
    </Card>
  );
}

// ---------- MRR推移チャート ----------

function MrrChart({ data }: { data: MonthlyRevenue[] }) {
  if (data.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        売上データがありません。顧客が契約すると自動で記録されます。
      </p>
    );
  }

  const maxMrr = Math.max(...data.map((d) => d.mrr), 1);

  return (
    <div className="space-y-4">
      {/* バーチャート */}
      <div className="flex items-end gap-1 h-40 overflow-x-auto pb-1">
        {data.map((d) => {
          const pctH = Math.max((d.mrr / maxMrr) * 100, 2);
          return (
            <div
              key={d.month}
              className="flex shrink-0 w-10 flex-col items-center gap-1"
              title={`${d.month}: ${formatMrr(d.mrr)}`}
            >
              <span className="text-[11px] text-muted-foreground">{formatMrr(d.mrr)}</span>
              <div className="w-full flex-1 flex items-end">
                {/* スタックバー */}
                <div className="w-full rounded-t overflow-hidden" style={{ height: `${pctH}%` }}>
                  <div
                    className="w-full bg-green-500"
                    style={{ height: `${Math.round((d.new_mrr / Math.max(d.mrr, 1)) * 100)}%` }}
                    title={`新規: ${formatMrr(d.new_mrr)}`}
                  />
                  <div
                    className="w-full bg-blue-500"
                    style={{ height: `${Math.round((d.expansion_mrr / Math.max(d.mrr, 1)) * 100)}%` }}
                    title={`拡張: ${formatMrr(d.expansion_mrr)}`}
                  />
                  <div className="w-full flex-1 bg-primary" />
                </div>
              </div>
              <span className="text-[11px] text-muted-foreground">{d.month.slice(5)}</span>
            </div>
          );
        })}
      </div>

      {/* 凡例 */}
      <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1">
          <span className="inline-block h-3 w-3 rounded-sm bg-green-500" />新規MRR
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-3 w-3 rounded-sm bg-blue-500" />拡張MRR
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-3 w-3 rounded-sm bg-primary" />継続MRR
        </span>
      </div>

      {/* 詳細テーブル（スマホはスクロール） */}
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b bg-muted/50">
              <th className="px-3 py-2 text-left font-medium">月</th>
              <th className="px-3 py-2 text-right font-medium">MRR</th>
              <th className="px-3 py-2 text-right font-medium">新規</th>
              <th className="px-3 py-2 text-right font-medium">拡張</th>
              <th className="px-3 py-2 text-right font-medium">縮小</th>
              <th className="px-3 py-2 text-right font-medium">解約</th>
              <th className="px-3 py-2 text-right font-medium">顧客数</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.month} className="border-b last:border-0 hover:bg-muted/30">
                <td className="px-3 py-2 font-medium">{d.month}</td>
                <td className="px-3 py-2 text-right font-medium">{formatMrr(d.mrr)}</td>
                <td className="px-3 py-2 text-right text-green-600">
                  {d.new_mrr > 0 ? `+${formatMrr(d.new_mrr)}` : "—"}
                </td>
                <td className="px-3 py-2 text-right text-blue-600">
                  {d.expansion_mrr > 0 ? `+${formatMrr(d.expansion_mrr)}` : "—"}
                </td>
                <td className="px-3 py-2 text-right text-yellow-600">
                  {d.contraction_mrr > 0 ? `-${formatMrr(d.contraction_mrr)}` : "—"}
                </td>
                <td className="px-3 py-2 text-right text-destructive">
                  {d.churned_mrr > 0 ? `-${formatMrr(d.churned_mrr)}` : "—"}
                </td>
                <td className="px-3 py-2 text-right">{d.customer_count}社</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------- コホート分析テーブル ----------

function CohortTable({ cohorts }: { cohorts: CohortRow[] }) {
  if (cohorts.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        コホートデータがありません。複数月の契約データが蓄積されると表示されます。
      </p>
    );
  }

  const maxMonths = Math.max(...cohorts.map((c) => c.months.length));
  const monthLabels = Array.from({ length: maxMonths }, (_, i) =>
    i === 0 ? "契約月" : `+${i}ヶ月`
  );

  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b bg-muted/50">
            <th className="px-3 py-2 text-left font-medium whitespace-nowrap">コホート</th>
            <th className="px-3 py-2 text-right font-medium whitespace-nowrap">顧客数</th>
            {monthLabels.map((lbl) => (
              <th key={lbl} className="px-2 py-2 text-center font-medium whitespace-nowrap">
                {lbl}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {cohorts.map((row) => (
            <tr key={row.cohort_month} className="border-b last:border-0">
              <td className="px-3 py-2 font-medium whitespace-nowrap">{row.cohort_month}</td>
              <td className="px-3 py-2 text-right whitespace-nowrap">{row.customer_count}社</td>
              {row.months.map((val, i) => (
                <td key={i} className="px-1 py-1 text-center">
                  <span
                    className={`inline-block rounded px-1.5 py-0.5 font-medium ${cohortCellColor(val)}`}
                  >
                    {val}%
                  </span>
                </td>
              ))}
              {/* 空セルを埋める */}
              {Array.from({ length: maxMonths - row.months.length }).map((_, i) => (
                <td key={`empty-${i}`} className="px-1 py-1" />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------- ページ ----------

export default function CrmRevenuePage() {
  const { session } = useAuth();
  const [summary, setSummary] = useState<RevenueSummary | null>(null);
  const [monthly, setMonthly] = useState<MonthlyRevenue[]>([]);
  const [cohorts, setCohorts] = useState<CohortRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!session?.access_token) return;

    async function fetchData() {
      setLoading(true);
      setError(null);
      try {
        const token = session?.access_token;

        const [summaryRes, monthlyRes, cohortRes] = await Promise.allSettled([
          apiFetch<RevenueSummary>("/crm/revenue/summary", { token }),
          apiFetch<{ items: MonthlyRevenue[] }>("/crm/revenue/monthly", {
            token,
            params: { limit: "12" },
          }),
          apiFetch<{ items: CohortRow[] }>("/crm/revenue/cohort", { token }),
        ]);

        if (summaryRes.status === "fulfilled") {
          setSummary(summaryRes.value);
        }
        if (monthlyRes.status === "fulfilled") {
          setMonthly(monthlyRes.value.items);
        }
        if (cohortRes.status === "fulfilled") {
          setCohorts(cohortRes.value.items);
        }

        if (
          summaryRes.status === "rejected" &&
          monthlyRes.status === "rejected"
        ) {
          setError("売上データの取得に失敗しました。しばらく経ってから再度お試しください。");
        }
      } catch {
        setError("売上データの取得に失敗しました。しばらく経ってから再度お試しください。");
      } finally {
        setLoading(false);
      }
    }

    fetchData();
  }, [session?.access_token]);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      {/* ページヘッダー */}
      <div>
        <h1 className="text-2xl font-bold">売上ダッシュボード</h1>
        <p className="text-sm text-muted-foreground">
          MRR・NRR・チャーン率・コホート分析をリアルタイムで確認できます。
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

      {/* KPIカード */}
      {loading ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <KpiSkeleton key={i} />
          ))}
        </div>
      ) : summary ? (
        <>
          {/* 主要KPI */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Card>
              <CardHeader className="pb-1 pt-4">
                <CardDescription className="text-xs">MRR（月次経常収益）</CardDescription>
              </CardHeader>
              <CardContent className="pb-4">
                <p className="text-2xl font-bold">{formatMrr(summary.mrr)}</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-1 pt-4">
                <CardDescription className="text-xs">ARR（年次換算）</CardDescription>
              </CardHeader>
              <CardContent className="pb-4">
                <p className="text-2xl font-bold">{formatMrr(summary.arr)}</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-1 pt-4">
                <CardDescription className="text-xs">NRR（売上維持率）</CardDescription>
              </CardHeader>
              <CardContent className="pb-4">
                <p className={`text-2xl font-bold ${nrrColor(summary.nrr)}`}>
                  {pct(summary.nrr, 0)}
                </p>
                <p className="text-xs text-muted-foreground">目標: 110%以上</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-1 pt-4">
                <CardDescription className="text-xs">月次チャーン率</CardDescription>
              </CardHeader>
              <CardContent className="pb-4">
                <p className={`text-2xl font-bold ${churnColor(summary.churn_rate)}`}>
                  {pct(summary.churn_rate)}
                </p>
                <p className="text-xs text-muted-foreground">目標: 3%以下</p>
              </CardContent>
            </Card>
          </div>

          {/* MRR内訳 */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Card>
              <CardHeader className="pb-1 pt-4">
                <CardDescription className="text-xs">新規MRR</CardDescription>
              </CardHeader>
              <CardContent className="pb-4">
                <p className="text-xl font-bold text-green-600">
                  +{formatMrr(summary.new_mrr)}
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-1 pt-4">
                <CardDescription className="text-xs">拡張MRR（アップセル）</CardDescription>
              </CardHeader>
              <CardContent className="pb-4">
                <p className="text-xl font-bold text-blue-600">
                  +{formatMrr(summary.expansion_mrr)}
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-1 pt-4">
                <CardDescription className="text-xs">縮小MRR（ダウンセル）</CardDescription>
              </CardHeader>
              <CardContent className="pb-4">
                <p className="text-xl font-bold text-yellow-600">
                  -{formatMrr(summary.contraction_mrr)}
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-1 pt-4">
                <CardDescription className="text-xs">解約MRR</CardDescription>
              </CardHeader>
              <CardContent className="pb-4">
                <p className="text-xl font-bold text-destructive">
                  -{formatMrr(summary.churned_mrr)}
                </p>
              </CardContent>
            </Card>
          </div>

          {/* 顧客数バッジ */}
          <div className="flex flex-wrap gap-3">
            <div className="flex items-center gap-2 rounded-lg border px-3 py-2">
              <span className="text-sm text-muted-foreground">契約中</span>
              <span className="font-bold">{summary.active_customers}社</span>
            </div>
            {summary.at_risk_customers > 0 && (
              <div className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2">
                <span className="text-sm text-muted-foreground">解約リスク</span>
                <span className="font-bold text-destructive">
                  {summary.at_risk_customers}社
                </span>
                <Badge variant="destructive" className="text-xs">
                  要対応
                </Badge>
              </div>
            )}
          </div>
        </>
      ) : null}

      {/* 詳細グラフ */}
      <Tabs defaultValue="monthly">
        <TabsList>
          <TabsTrigger value="monthly">月次推移</TabsTrigger>
          <TabsTrigger value="cohort">コホート分析</TabsTrigger>
        </TabsList>

        <TabsContent value="monthly">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">MRR月次推移（直近12ヶ月）</CardTitle>
              <CardDescription>
                新規・拡張・縮小・解約の内訳を確認できます。
              </CardDescription>
            </CardHeader>
            <CardContent>
              {loading ? (
                <div className="flex flex-col items-center gap-3 py-8">
                  <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
                  <p className="text-sm text-muted-foreground">売上データを読み込んでいます...</p>
                </div>
              ) : (
                <MrrChart data={monthly} />
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="cohort">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">コホート分析（契約月別の継続率）</CardTitle>
              <CardDescription>
                同じ月に契約した顧客が何ヶ月後もサービスを継続しているかを示します。
                緑が濃いほど高い継続率です。
              </CardDescription>
            </CardHeader>
            <CardContent>
              {loading ? (
                <div className="flex flex-col items-center gap-3 py-8">
                  <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
                  <p className="text-sm text-muted-foreground">コホートデータを読み込んでいます...</p>
                </div>
              ) : (
                <CohortTable cohorts={cohorts} />
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
