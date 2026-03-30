"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
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
import { Button } from "@/components/ui/button";

// ---------- Types ----------

interface LearningDashboard {
  // スコアリング精度推移
  scoring_accuracy: {
    period: string;          // "2025-11" など
    accuracy_rate: number;   // 0-1 (スコア70以上で受注した割合)
    total_qualified: number;
    won: number;
    lost: number;
  }[];

  // アウトリーチPDCA
  outreach_pdca: {
    period: string;
    sent_count: number;
    open_rate: number;       // 0-1
    reply_rate: number;      // 0-1
    meeting_rate: number;    // 0-1
    top_performing_industry: string | null;
    top_performing_subject: string | null;
  }[];

  // CS品質推移
  cs_quality: {
    period: string;
    ai_resolution_rate: number;  // 0-1
    avg_csat: number | null;
    avg_frt_min: number | null;
    escalation_rate: number;     // 0-1
  }[];

  // 受注パターン
  won_patterns: {
    pattern: string;
    count: number;
    win_rate: number;   // 0-1
    description: string;
  }[];

  // 失注パターン
  lost_patterns: {
    reason: string;
    count: number;
    percentage: number;  // 0-1
    suggested_improvement: string;
  }[];

  // 学習ループステータス
  learning_loop_status: {
    last_model_updated_at: string | null;
    scoring_model_version: string;
    total_training_samples: number;
    improvement_since_last_update: number | null;  // % ポイント変化
  };
}

// ---------- Helpers ----------

function formatPeriod(period: string): string {
  const [y, m] = period.split("-");
  return `${y}年${parseInt(m)}月`;
}

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function formatMinutes(min: number | null): string {
  if (min === null) return "-";
  if (min < 60) return `${Math.round(min)}分`;
  const h = Math.floor(min / 60);
  const m = Math.round(min % 60);
  if (m === 0) return `${h}時間`;
  return `${h}時間${m}分`;
}

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

// ---------- グラフコンポーネント ----------

/**
 * シンプルな横棒グラフ（CSSのみ）
 */
function SimpleBar({
  value,
  max,
  label,
  sublabel,
  colorClass = "bg-primary",
}: {
  value: number;
  max: number;
  label: string;
  sublabel?: string;
  colorClass?: string;
}) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-sm">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-medium">
          {sublabel ?? value.toLocaleString()}
        </span>
      </div>
      <div className="h-2 rounded-full bg-muted">
        <div
          className={`h-2 rounded-full transition-all ${colorClass}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

/**
 * パーセント棒グラフ（0-1の値を受け取り、目標ラインも表示可能）
 */
function PercentBar({
  value,
  label,
  targetValue,
  colorClass,
}: {
  value: number;
  label: string;
  targetValue?: number;
  colorClass?: string;
}) {
  const pct = Math.round(value * 100);
  const targetPct = targetValue !== undefined ? Math.round(targetValue * 100) : undefined;

  // 値に応じて自動で色を決定（colorClassが指定されない場合）
  const autoColor =
    colorClass ??
    (value >= 0.8
      ? "bg-green-500"
      : value >= 0.5
      ? "bg-yellow-500"
      : "bg-destructive");

  return (
    <div className="space-y-1">
      <div className="flex justify-between text-sm">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-semibold">{pct}%</span>
      </div>
      <div className="relative h-3 rounded-full bg-muted">
        <div
          className={`h-3 rounded-full transition-all ${autoColor}`}
          style={{ width: `${pct}%` }}
        />
        {/* 目標ライン */}
        {targetPct !== undefined && (
          <div
            className="absolute top-0 h-3 w-0.5 bg-foreground/40"
            style={{ left: `${targetPct}%` }}
            title={`目標: ${targetPct}%`}
          />
        )}
      </div>
      {targetPct !== undefined && (
        <p className="text-[11px] text-muted-foreground">
          目標ライン: {targetPct}%
        </p>
      )}
    </div>
  );
}

/**
 * 月次推移グラフ（複数の棒グラフを横並びで表示）
 */
function MonthlyTrendChart({
  items,
  valueKey,
  labelFormatter,
  colorFn,
  targetValue,
}: {
  items: { period: string; [key: string]: number | string | null }[];
  valueKey: string;
  labelFormatter?: (val: number) => string;
  colorFn?: (val: number) => string;
  targetValue?: number;
}) {
  const values = items.map((item) => {
    const v = item[valueKey];
    return typeof v === "number" ? v : 0;
  });
  const maxVal = Math.max(...values, targetValue ?? 0, 0.01);

  return (
    <div className="flex items-end gap-2 h-32">
      {items.map((item) => {
        const val = typeof item[valueKey] === "number" ? (item[valueKey] as number) : 0;
        const heightPct = (val / maxVal) * 100;
        const color = colorFn
          ? colorFn(val)
          : val >= (targetValue ?? 0)
          ? "bg-green-500"
          : "bg-yellow-400";

        return (
          <div key={item.period} className="flex flex-1 flex-col items-center gap-1">
            <span className="text-[11px] font-medium text-foreground">
              {labelFormatter ? labelFormatter(val) : `${Math.round(val * 100)}%`}
            </span>
            <div className="relative w-full flex flex-col justify-end" style={{ height: "72px" }}>
              {/* 目標ライン */}
              {targetValue !== undefined && (
                <div
                  className="absolute left-0 right-0 border-t-2 border-dashed border-muted-foreground/40"
                  style={{ bottom: `${(targetValue / maxVal) * 72}px` }}
                />
              )}
              <div
                className={`w-full rounded-t transition-all ${color}`}
                style={{ height: `${Math.max(heightPct, 2)}%` }}
              />
            </div>
            <span className="text-[11px] text-muted-foreground truncate w-full text-center">
              {formatPeriod(item.period).replace(/\d{4}年/, "")}
            </span>
          </div>
        );
      })}
      {items.length === 0 && (
        <div className="flex w-full items-center justify-center h-full">
          <p className="text-sm text-muted-foreground">データがありません</p>
        </div>
      )}
    </div>
  );
}

/**
 * アウトリーチ指標カード（数値+バー）
 */
function OutreachMetricCard({
  label,
  value,
  sublabel,
  colorClass,
}: {
  label: string;
  value: number | string;
  sublabel?: string;
  colorClass?: string;
}) {
  return (
    <div className="rounded-lg border bg-card p-3 text-center space-y-1">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={`text-xl font-bold ${colorClass ?? "text-foreground"}`}>
        {value}
      </p>
      {sublabel && (
        <p className="text-[11px] text-muted-foreground">{sublabel}</p>
      )}
    </div>
  );
}

// ---------- Skeleton ----------

function MetricRowSkeleton() {
  return (
    <div className="flex items-center justify-between py-2 border-b last:border-0">
      <div className="h-4 w-24 animate-pulse rounded bg-muted" />
      <div className="flex gap-4">
        <div className="h-4 w-12 animate-pulse rounded bg-muted" />
        <div className="h-4 w-12 animate-pulse rounded bg-muted" />
        <div className="h-4 w-12 animate-pulse rounded bg-muted" />
      </div>
    </div>
  );
}

function ChartSkeleton() {
  return (
    <div className="space-y-3">
      <div className="h-32 w-full animate-pulse rounded-lg bg-muted" />
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-16 animate-pulse rounded-lg bg-muted" />
        ))}
      </div>
      <p className="text-sm text-muted-foreground text-center">
        データを読み込んでいます...
      </p>
    </div>
  );
}

// ---------- Page ----------

export default function LearningDashboardPage() {
  const { session } = useAuth();
  const router = useRouter();
  const [data, setData] = useState<LearningDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!session?.access_token) return;
    setLoading(true);
    setError(null);
    apiFetch<LearningDashboard>("/learning/dashboard", {
      token: session.access_token,
    })
      .then(setData)
      .catch(() =>
        setError(
          "学習データの取得に失敗しました。しばらく経ってから再度お試しください"
        )
      )
      .finally(() => setLoading(false));
  }, [session?.access_token]);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold">AI学習レポート</h1>
        <p className="text-sm text-muted-foreground">
          AI のスコアリング精度・アウトリーチ成果・CS 品質の推移と、受注/失注パターンを分析します。
        </p>
      </div>

      {error && (
        <Card className="border-destructive/50 bg-destructive/5">
          <CardContent className="pt-4">
            <p className="text-sm text-destructive">{error}</p>
          </CardContent>
        </Card>
      )}

      {/* 学習ループステータス */}
      {!loading && data && (
        <Card className="border-primary/20 bg-primary/5">
          <CardContent className="flex flex-col gap-3 pt-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="space-y-0.5">
              <p className="text-sm font-semibold text-primary">
                学習フィードバックループ
              </p>
              <p className="text-xs text-muted-foreground">
                スコアリングモデル v{data.learning_loop_status.scoring_model_version} ·
                学習サンプル数:{" "}
                {data.learning_loop_status.total_training_samples.toLocaleString()}件
              </p>
              {data.learning_loop_status.last_model_updated_at && (
                <p className="text-xs text-muted-foreground">
                  最終更新:{" "}
                  {new Date(
                    data.learning_loop_status.last_model_updated_at
                  ).toLocaleDateString("ja-JP", {
                    year: "numeric",
                    month: "2-digit",
                    day: "2-digit",
                  })}
                </p>
              )}
            </div>
            {data.learning_loop_status.improvement_since_last_update !== null && (
              <div className="text-center sm:text-right">
                <p className="text-xs text-muted-foreground">
                  前回更新からの改善
                </p>
                <p
                  className={`text-2xl font-bold ${
                    data.learning_loop_status.improvement_since_last_update >= 0
                      ? "text-green-600"
                      : "text-destructive"
                  }`}
                >
                  {data.learning_loop_status.improvement_since_last_update >= 0
                    ? "+"
                    : ""}
                  {data.learning_loop_status.improvement_since_last_update.toFixed(
                    1
                  )}
                  pt
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      <Tabs defaultValue="scoring">
        <TabsList className="flex-wrap h-auto gap-1">
          <TabsTrigger value="scoring">スコアリング精度</TabsTrigger>
          <TabsTrigger value="outreach">アウトリーチPDCA</TabsTrigger>
          <TabsTrigger value="cs">CS品質</TabsTrigger>
          <TabsTrigger value="patterns">受注/失注パターン</TabsTrigger>
        </TabsList>

        {/* ===== スコアリング精度推移 ===== */}
        <TabsContent value="scoring" className="mt-4 space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-lg font-semibold">スコアリング精度推移</CardTitle>
              <CardDescription>
                スコア 70 以上と判定されたリードの実際の受注率を月次で追跡します。目標ライン: 80%
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              {loading ? (
                <ChartSkeleton />
              ) : !data || data.scoring_accuracy.length === 0 ? (
                <div className="py-10 text-center space-y-3">
                  <p className="text-sm text-muted-foreground">
                    まだデータがありません。商談が蓄積されると精度推移が表示されます。
                  </p>
                  <Button
                    variant="outline"
                    size="lg"
                    className="w-full sm:w-auto"
                    onClick={() => router.push("/sales")}
                  >
                    商談データを入力する
                  </Button>
                </div>
              ) : (
                <>
                  {/* 棒グラフ（月次精度推移） */}
                  <div>
                    <p className="text-xs text-muted-foreground mb-3">月次受注率の推移</p>
                    <MonthlyTrendChart
                      items={data.scoring_accuracy}
                      valueKey="accuracy_rate"
                      targetValue={0.8}
                      colorFn={(val) =>
                        val >= 0.8
                          ? "bg-green-500"
                          : val >= 0.5
                          ? "bg-yellow-400"
                          : "bg-destructive"
                      }
                    />
                  </div>

                  {/* 直近月のサマリー指標 */}
                  {data.scoring_accuracy.length > 0 && (() => {
                    const latest = data.scoring_accuracy[data.scoring_accuracy.length - 1];
                    return (
                      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                        <OutreachMetricCard
                          label="直近の受注率"
                          value={formatPercent(latest.accuracy_rate)}
                          colorClass={
                            latest.accuracy_rate >= 0.8
                              ? "text-green-600"
                              : latest.accuracy_rate >= 0.5
                              ? "text-yellow-600"
                              : "text-destructive"
                          }
                        />
                        <OutreachMetricCard
                          label="受注件数"
                          value={`${latest.won}件`}
                          colorClass="text-green-600"
                        />
                        <OutreachMetricCard
                          label="失注件数"
                          value={`${latest.lost}件`}
                          colorClass="text-muted-foreground"
                        />
                        <OutreachMetricCard
                          label="対象件数"
                          value={`${latest.total_qualified}件`}
                        />
                      </div>
                    );
                  })()}

                  {/* 全期間の一覧（PC テーブル） */}
                  <div className="hidden sm:block overflow-x-auto">
                    <p className="text-xs text-muted-foreground mb-2">全期間の詳細</p>
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b text-left">
                          <th className="pb-2 pr-4 font-medium text-muted-foreground">期間</th>
                          <th className="pb-2 pr-4 font-medium text-muted-foreground">精度</th>
                          <th className="pb-2 pr-4 font-medium text-muted-foreground">受注</th>
                          <th className="pb-2 pr-4 font-medium text-muted-foreground">失注</th>
                          <th className="pb-2 font-medium text-muted-foreground">対象数</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.scoring_accuracy.map((row) => (
                          <tr key={row.period} className="border-b last:border-0">
                            <td className="py-2 pr-4 text-muted-foreground">
                              {formatPeriod(row.period)}
                            </td>
                            <td className="py-2 pr-4">
                              <span
                                className={`font-semibold ${
                                  row.accuracy_rate >= 0.8
                                    ? "text-green-700"
                                    : row.accuracy_rate >= 0.5
                                    ? "text-yellow-700"
                                    : "text-destructive"
                                }`}
                              >
                                {formatPercent(row.accuracy_rate)}
                              </span>
                            </td>
                            <td className="py-2 pr-4 text-green-700">{row.won}件</td>
                            <td className="py-2 pr-4 text-muted-foreground">{row.lost}件</td>
                            <td className="py-2 text-muted-foreground">{row.total_qualified}件</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  {/* モバイル カード */}
                  <div className="space-y-2 sm:hidden">
                    {data.scoring_accuracy.map((row) => (
                      <div
                        key={row.period}
                        className="flex items-center justify-between rounded-lg border px-3 py-2 text-sm"
                      >
                        <span className="text-muted-foreground">
                          {formatPeriod(row.period)}
                        </span>
                        <div className="flex gap-3">
                          <span
                            className={`font-semibold ${
                              row.accuracy_rate >= 0.8
                                ? "text-green-700"
                                : row.accuracy_rate >= 0.5
                                ? "text-yellow-700"
                                : "text-destructive"
                            }`}
                          >
                            精度 {formatPercent(row.accuracy_rate)}
                          </span>
                          <span className="text-green-700">受注{row.won}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ===== アウトリーチ PDCA ===== */}
        <TabsContent value="outreach" className="mt-4 space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-lg font-semibold">アウトリーチ PDCA</CardTitle>
              <CardDescription>
                月次のアウトリーチ件数・開封率・返信率・商談化率の推移です。
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              {loading ? (
                <ChartSkeleton />
              ) : !data || data.outreach_pdca.length === 0 ? (
                <div className="py-10 text-center space-y-3">
                  <p className="text-sm text-muted-foreground">
                    まだアウトリーチデータがありません。アウトリーチを実行すると成果が表示されます。
                  </p>
                  <Button
                    variant="outline"
                    size="lg"
                    className="w-full sm:w-auto"
                    onClick={() => router.push("/marketing")}
                  >
                    アウトリーチを開始する
                  </Button>
                </div>
              ) : (
                <>
                  {/* 棒グラフ：商談化率の月次推移 */}
                  <div>
                    <p className="text-xs text-muted-foreground mb-3">商談化率の月次推移（目標: 2%）</p>
                    <MonthlyTrendChart
                      items={data.outreach_pdca}
                      valueKey="meeting_rate"
                      targetValue={0.02}
                      colorFn={(val) =>
                        val >= 0.05
                          ? "bg-green-500"
                          : val >= 0.02
                          ? "bg-yellow-400"
                          : "bg-muted-foreground"
                      }
                    />
                  </div>

                  {/* 直近月の主要指標 */}
                  {data.outreach_pdca.length > 0 && (() => {
                    const latest = data.outreach_pdca[data.outreach_pdca.length - 1];
                    return (
                      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                        <OutreachMetricCard
                          label="送信数"
                          value={latest.sent_count.toLocaleString()}
                          sublabel="件"
                        />
                        <OutreachMetricCard
                          label="開封率"
                          value={formatPercent(latest.open_rate)}
                          colorClass={
                            latest.open_rate >= 0.3
                              ? "text-green-600"
                              : latest.open_rate >= 0.1
                              ? "text-yellow-600"
                              : "text-muted-foreground"
                          }
                        />
                        <OutreachMetricCard
                          label="返信率"
                          value={formatPercent(latest.reply_rate)}
                          colorClass={
                            latest.reply_rate >= 0.05
                              ? "text-green-600"
                              : latest.reply_rate >= 0.03
                              ? "text-yellow-600"
                              : "text-muted-foreground"
                          }
                        />
                        <OutreachMetricCard
                          label="商談化率"
                          value={formatPercent(latest.meeting_rate)}
                          colorClass={
                            latest.meeting_rate >= 0.02
                              ? "text-green-600 font-bold"
                              : "text-muted-foreground"
                          }
                        />
                      </div>
                    );
                  })()}

                  {/* 開封率・返信率・商談化率のバー比較 */}
                  {data.outreach_pdca.length > 0 && (() => {
                    const latest = data.outreach_pdca[data.outreach_pdca.length - 1];
                    return (
                      <div className="space-y-3 rounded-lg border bg-muted/20 p-4">
                        <p className="text-xs font-medium text-muted-foreground">
                          直近月のファネル比較
                        </p>
                        <SimpleBar
                          value={latest.sent_count}
                          max={latest.sent_count}
                          label="送信"
                          sublabel={`${latest.sent_count.toLocaleString()}件`}
                          colorClass="bg-primary"
                        />
                        <SimpleBar
                          value={Math.round(latest.sent_count * latest.open_rate)}
                          max={latest.sent_count}
                          label="開封"
                          sublabel={`${Math.round(latest.sent_count * latest.open_rate).toLocaleString()}件 (${formatPercent(latest.open_rate)})`}
                          colorClass="bg-blue-400"
                        />
                        <SimpleBar
                          value={Math.round(latest.sent_count * latest.reply_rate)}
                          max={latest.sent_count}
                          label="返信"
                          sublabel={`${Math.round(latest.sent_count * latest.reply_rate).toLocaleString()}件 (${formatPercent(latest.reply_rate)})`}
                          colorClass="bg-yellow-400"
                        />
                        <SimpleBar
                          value={Math.round(latest.sent_count * latest.meeting_rate)}
                          max={latest.sent_count}
                          label="商談化"
                          sublabel={`${Math.round(latest.sent_count * latest.meeting_rate).toLocaleString()}件 (${formatPercent(latest.meeting_rate)})`}
                          colorClass="bg-green-500"
                        />
                      </div>
                    );
                  })()}

                  {/* 効果の高かったパターン */}
                  {data.outreach_pdca.some(
                    (r) => r.top_performing_industry || r.top_performing_subject
                  ) && (
                    <div className="rounded-lg border bg-muted/30 p-3 space-y-2">
                      <p className="text-sm font-medium">直近で最も効果が高かったパターン</p>
                      {data.outreach_pdca[data.outreach_pdca.length - 1]
                        ?.top_performing_industry && (
                        <p className="text-xs text-muted-foreground">
                          業種:{" "}
                          {INDUSTRY_LABELS[
                            data.outreach_pdca[data.outreach_pdca.length - 1]
                              .top_performing_industry!
                          ] ??
                            data.outreach_pdca[data.outreach_pdca.length - 1]
                              .top_performing_industry}
                        </p>
                      )}
                      {data.outreach_pdca[data.outreach_pdca.length - 1]
                        ?.top_performing_subject && (
                        <p className="text-xs text-muted-foreground">
                          件名:{" "}
                          {
                            data.outreach_pdca[data.outreach_pdca.length - 1]
                              .top_performing_subject
                          }
                        </p>
                      )}
                    </div>
                  )}

                  {/* 全期間テーブル（PC） */}
                  <div className="hidden sm:block overflow-x-auto">
                    <p className="text-xs text-muted-foreground mb-2">全期間の詳細</p>
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b text-left">
                          <th className="pb-2 pr-4 font-medium text-muted-foreground">期間</th>
                          <th className="pb-2 pr-4 font-medium text-muted-foreground">送信数</th>
                          <th className="pb-2 pr-4 font-medium text-muted-foreground">開封率</th>
                          <th className="pb-2 pr-4 font-medium text-muted-foreground">返信率</th>
                          <th className="pb-2 font-medium text-muted-foreground">商談化率</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.outreach_pdca.map((row) => (
                          <tr key={row.period} className="border-b last:border-0">
                            <td className="py-2 pr-4 text-muted-foreground">
                              {formatPeriod(row.period)}
                            </td>
                            <td className="py-2 pr-4">{row.sent_count.toLocaleString()}件</td>
                            <td className="py-2 pr-4">
                              <span
                                className={
                                  row.open_rate >= 0.1
                                    ? "text-green-700"
                                    : "text-yellow-700"
                                }
                              >
                                {formatPercent(row.open_rate)}
                              </span>
                            </td>
                            <td className="py-2 pr-4">
                              <span
                                className={
                                  row.reply_rate >= 0.03
                                    ? "text-green-700"
                                    : "text-yellow-700"
                                }
                              >
                                {formatPercent(row.reply_rate)}
                              </span>
                            </td>
                            <td className="py-2">
                              <span
                                className={
                                  row.meeting_rate >= 0.02
                                    ? "text-green-700 font-semibold"
                                    : "text-yellow-700"
                                }
                              >
                                {formatPercent(row.meeting_rate)}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  {/* モバイルカード */}
                  <div className="space-y-2 sm:hidden">
                    {data.outreach_pdca.map((row) => (
                      <div
                        key={row.period}
                        className="rounded-lg border px-3 py-2 text-sm space-y-1"
                      >
                        <p className="font-medium">{formatPeriod(row.period)}</p>
                        <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                          <span>送信: {row.sent_count.toLocaleString()}件</span>
                          <span>開封: {formatPercent(row.open_rate)}</span>
                          <span>返信: {formatPercent(row.reply_rate)}</span>
                          <span className="text-green-700 font-medium">
                            商談化: {formatPercent(row.meeting_rate)}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ===== CS 品質推移 ===== */}
        <TabsContent value="cs" className="mt-4 space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-lg font-semibold">CS 品質推移</CardTitle>
              <CardDescription>
                月次のAI対応率・CSAT・初回応答時間・エスカレーション率の変化を確認します。
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              {loading ? (
                <ChartSkeleton />
              ) : !data || data.cs_quality.length === 0 ? (
                <div className="py-10 text-center space-y-3">
                  <p className="text-sm text-muted-foreground">
                    まだ CS データがありません。お客様対応が蓄積されると品質推移が表示されます。
                  </p>
                  <Button
                    variant="outline"
                    size="lg"
                    className="w-full sm:w-auto"
                    onClick={() => router.push("/support")}
                  >
                    お客様対応を確認する
                  </Button>
                </div>
              ) : (
                <>
                  {/* AI対応率の棒グラフ（月次推移） */}
                  <div>
                    <p className="text-xs text-muted-foreground mb-3">AI対応率の月次推移（目標: 60%）</p>
                    <MonthlyTrendChart
                      items={data.cs_quality}
                      valueKey="ai_resolution_rate"
                      targetValue={0.6}
                      colorFn={(val) =>
                        val >= 0.8
                          ? "bg-green-500"
                          : val >= 0.6
                          ? "bg-yellow-400"
                          : "bg-muted-foreground"
                      }
                    />
                  </div>

                  {/* 直近月のサマリー */}
                  {data.cs_quality.length > 0 && (() => {
                    const latest = data.cs_quality[data.cs_quality.length - 1];
                    return (
                      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                        <OutreachMetricCard
                          label="AI対応率"
                          value={formatPercent(latest.ai_resolution_rate)}
                          colorClass={
                            latest.ai_resolution_rate >= 0.6
                              ? "text-green-600"
                              : "text-yellow-600"
                          }
                        />
                        <OutreachMetricCard
                          label="顧客満足度"
                          value={latest.avg_csat !== null ? latest.avg_csat.toFixed(1) : "-"}
                          sublabel="/ 5.0"
                          colorClass={
                            latest.avg_csat !== null
                              ? latest.avg_csat >= 4.2
                                ? "text-green-600"
                                : latest.avg_csat >= 3.5
                                ? "text-yellow-600"
                                : "text-destructive"
                              : "text-muted-foreground"
                          }
                        />
                        <OutreachMetricCard
                          label="初回応答時間"
                          value={formatMinutes(latest.avg_frt_min)}
                          colorClass={
                            latest.avg_frt_min !== null && latest.avg_frt_min <= 5
                              ? "text-green-600"
                              : latest.avg_frt_min !== null && latest.avg_frt_min <= 15
                              ? "text-yellow-600"
                              : "text-destructive"
                          }
                        />
                        <OutreachMetricCard
                          label="エスカレーション率"
                          value={formatPercent(latest.escalation_rate)}
                          colorClass={
                            latest.escalation_rate <= 0.1
                              ? "text-green-600"
                              : "text-yellow-600"
                          }
                        />
                      </div>
                    );
                  })()}

                  {/* 品質指標のバー表示 */}
                  {data.cs_quality.length > 0 && (() => {
                    const latest = data.cs_quality[data.cs_quality.length - 1];
                    return (
                      <div className="space-y-3 rounded-lg border bg-muted/20 p-4">
                        <p className="text-xs font-medium text-muted-foreground">
                          直近月の品質スコア
                        </p>
                        <PercentBar
                          value={latest.ai_resolution_rate}
                          label="AI対応率"
                          targetValue={0.6}
                          colorClass="bg-primary"
                        />
                        {latest.avg_csat !== null && (
                          <PercentBar
                            value={latest.avg_csat / 5}
                            label={`顧客満足度 (${latest.avg_csat.toFixed(1)}/5.0)`}
                            targetValue={4.2 / 5}
                            colorClass="bg-green-500"
                          />
                        )}
                        <PercentBar
                          value={1 - latest.escalation_rate}
                          label={`自己解決率 (エスカレーション: ${formatPercent(latest.escalation_rate)})`}
                          targetValue={0.9}
                          colorClass="bg-blue-400"
                        />
                      </div>
                    );
                  })()}

                  {/* 全期間テーブル（PC） */}
                  <div className="hidden sm:block overflow-x-auto">
                    <p className="text-xs text-muted-foreground mb-2">全期間の詳細</p>
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b text-left">
                          <th className="pb-2 pr-4 font-medium text-muted-foreground">期間</th>
                          <th className="pb-2 pr-4 font-medium text-muted-foreground">AI対応率</th>
                          <th className="pb-2 pr-4 font-medium text-muted-foreground">CSAT</th>
                          <th className="pb-2 pr-4 font-medium text-muted-foreground">初回応答時間</th>
                          <th className="pb-2 font-medium text-muted-foreground">エスカレーション率</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.cs_quality.map((row) => (
                          <tr key={row.period} className="border-b last:border-0">
                            <td className="py-2 pr-4 text-muted-foreground">
                              {formatPeriod(row.period)}
                            </td>
                            <td className="py-2 pr-4">
                              <span
                                className={
                                  row.ai_resolution_rate >= 0.6
                                    ? "text-green-700"
                                    : "text-yellow-700"
                                }
                              >
                                {formatPercent(row.ai_resolution_rate)}
                              </span>
                            </td>
                            <td className="py-2 pr-4">
                              {row.avg_csat !== null ? (
                                <span
                                  className={
                                    row.avg_csat >= 4.2
                                      ? "text-green-700"
                                      : row.avg_csat >= 3.5
                                      ? "text-yellow-700"
                                      : "text-destructive"
                                  }
                                >
                                  {row.avg_csat.toFixed(1)}
                                </span>
                              ) : (
                                <span className="text-muted-foreground">-</span>
                              )}
                            </td>
                            <td className="py-2 pr-4">
                              <span
                                className={
                                  row.avg_frt_min !== null && row.avg_frt_min <= 5
                                    ? "text-green-700"
                                    : row.avg_frt_min !== null && row.avg_frt_min <= 15
                                    ? "text-yellow-700"
                                    : "text-destructive"
                                }
                              >
                                {formatMinutes(row.avg_frt_min)}
                              </span>
                            </td>
                            <td className="py-2">
                              <span
                                className={
                                  row.escalation_rate <= 0.1
                                    ? "text-green-700"
                                    : "text-yellow-700"
                                }
                              >
                                {formatPercent(row.escalation_rate)}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  {/* モバイルカード */}
                  <div className="space-y-2 sm:hidden">
                    {data.cs_quality.map((row) => (
                      <div
                        key={row.period}
                        className="rounded-lg border px-3 py-2 text-sm space-y-1"
                      >
                        <p className="font-medium">{formatPeriod(row.period)}</p>
                        <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                          <span>AI対応: {formatPercent(row.ai_resolution_rate)}</span>
                          <span>CSAT: {row.avg_csat?.toFixed(1) ?? "-"}</span>
                          <span>応答: {formatMinutes(row.avg_frt_min)}</span>
                          <span>エスカレ: {formatPercent(row.escalation_rate)}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ===== 受注/失注パターン ===== */}
        <TabsContent value="patterns" className="mt-4 space-y-4">
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {/* 受注パターン */}
            <Card>
              <CardHeader>
                <CardTitle className="text-lg font-semibold">受注パターン Top5</CardTitle>
                <CardDescription>成約につながりやすい共通要因です。</CardDescription>
              </CardHeader>
              <CardContent>
                {loading ? (
                  <div className="space-y-3">
                    {Array.from({ length: 4 }).map((_, i) => (
                      <div key={i} className="h-16 w-full animate-pulse rounded-lg bg-muted" />
                    ))}
                    <p className="text-sm text-muted-foreground text-center">
                      データを読み込んでいます...
                    </p>
                  </div>
                ) : !data || data.won_patterns.length === 0 ? (
                  <div className="py-10 text-center space-y-3">
                    <p className="text-sm text-muted-foreground">
                      まだデータがありません。受注が蓄積されるとパターンが表示されます。
                    </p>
                    <Button
                      variant="outline"
                      size="lg"
                      className="w-full sm:w-auto"
                      onClick={() => router.push("/sales")}
                    >
                      商談データを入力する
                    </Button>
                  </div>
                ) : (
                  <div className="space-y-4">
                    {/* 受注率のバーグラフ */}
                    <div className="space-y-3 rounded-lg border bg-muted/20 p-4">
                      <p className="text-xs font-medium text-muted-foreground">受注率の比較</p>
                      {data.won_patterns.slice(0, 5).map((pat, i) => (
                        <SimpleBar
                          key={i}
                          value={pat.win_rate}
                          max={1}
                          label={pat.pattern}
                          sublabel={`${formatPercent(pat.win_rate)} (${pat.count}件)`}
                          colorClass="bg-green-500"
                        />
                      ))}
                    </div>

                    {/* パターン詳細 */}
                    <div className="space-y-2">
                      {data.won_patterns.map((pat, i) => (
                        <div
                          key={i}
                          className="flex flex-col gap-1 rounded-lg border p-3"
                        >
                          <div className="flex items-center justify-between">
                            <p className="text-sm font-medium">{pat.pattern}</p>
                            <div className="flex items-center gap-2">
                              <Badge className="bg-green-100 text-green-800 text-[11px]">
                                受注率 {formatPercent(pat.win_rate)}
                              </Badge>
                              <span className="text-xs text-muted-foreground">
                                {pat.count}件
                              </span>
                            </div>
                          </div>
                          <p className="text-xs text-muted-foreground">{pat.description}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* 失注パターン */}
            <Card>
              <CardHeader>
                <CardTitle className="text-lg font-semibold">失注パターン Top5</CardTitle>
                <CardDescription>失注の主な理由と改善ヒントです。</CardDescription>
              </CardHeader>
              <CardContent>
                {loading ? (
                  <div className="space-y-3">
                    {Array.from({ length: 4 }).map((_, i) => (
                      <div key={i} className="h-16 w-full animate-pulse rounded-lg bg-muted" />
                    ))}
                    <p className="text-sm text-muted-foreground text-center">
                      データを読み込んでいます...
                    </p>
                  </div>
                ) : !data || data.lost_patterns.length === 0 ? (
                  <div className="py-10 text-center space-y-3">
                    <p className="text-sm text-muted-foreground">
                      まだデータがありません。失注データが蓄積されると改善ヒントが表示されます。
                    </p>
                    <Button
                      variant="outline"
                      size="lg"
                      className="w-full sm:w-auto"
                      onClick={() => router.push("/sales")}
                    >
                      商談データを入力する
                    </Button>
                  </div>
                ) : (
                  <div className="space-y-4">
                    {/* 失注割合のバーグラフ */}
                    <div className="space-y-3 rounded-lg border bg-muted/20 p-4">
                      <p className="text-xs font-medium text-muted-foreground">失注理由の割合</p>
                      {data.lost_patterns.slice(0, 5).map((pat, i) => (
                        <SimpleBar
                          key={i}
                          value={pat.percentage}
                          max={1}
                          label={pat.reason}
                          sublabel={`${formatPercent(pat.percentage)} (${pat.count}件)`}
                          colorClass="bg-yellow-400"
                        />
                      ))}
                    </div>

                    {/* パターン詳細 */}
                    <div className="space-y-2">
                      {data.lost_patterns.map((pat, i) => (
                        <div
                          key={i}
                          className="flex flex-col gap-1 rounded-lg border p-3"
                        >
                          <div className="flex items-center justify-between">
                            <p className="text-sm font-medium">{pat.reason}</p>
                            <div className="flex items-center gap-2">
                              <Badge variant="secondary" className="text-[11px]">
                                {formatPercent(pat.percentage)}
                              </Badge>
                              <span className="text-xs text-muted-foreground">
                                {pat.count}件
                              </span>
                            </div>
                          </div>
                          <p className="text-xs text-primary">
                            改善ヒント: {pat.suggested_improvement}
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
