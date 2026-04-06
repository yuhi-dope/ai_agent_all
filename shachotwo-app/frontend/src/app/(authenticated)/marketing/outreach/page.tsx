"use client";

import { useEffect, useState, useCallback } from "react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardHeader,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

// ---------- Types ----------

interface OutreachSummary {
  sent_today: number;
  replied_today: number;
  hot_leads_today: number;
  reply_rate: number;
  hot_lead_rate: number;
}

interface IndustryPerformance {
  industry: string;
  industry_label: string;
  sent: number;
  replied: number;
  hot_leads: number;
  reply_rate: number;
}

interface HotLead {
  id: string;
  company_name: string;
  contact_name: string | null;
  industry: string;
  industry_label: string;
  score: number;
  last_activity_at: string;
  pain_points: string[];
}

interface OutreachDashboard {
  summary: OutreachSummary;
  industry_performance: IndustryPerformance[];
  hot_leads: HotLead[];
}

interface RunResult {
  message: string;
  queued_count: number;
}

// ---------- Helpers ----------

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("ja-JP", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function rateColor(rate: number): string {
  if (rate >= 0.15) return "text-green-600";
  if (rate >= 0.08) return "text-yellow-600";
  return "text-muted-foreground";
}

function scoreColor(score: number): string {
  if (score >= 80) return "text-green-600 font-bold";
  if (score >= 60) return "text-yellow-600 font-semibold";
  return "text-muted-foreground";
}

// ---------- Skeletons ----------

function StatCardSkeleton() {
  return (
    <Card>
      <CardHeader>
        <div className="h-4 w-24 animate-pulse rounded bg-muted" />
      </CardHeader>
      <CardContent>
        <div className="h-8 w-16 animate-pulse rounded bg-muted" />
        <div className="mt-2 h-3 w-20 animate-pulse rounded bg-muted" />
      </CardContent>
    </Card>
  );
}

function RowSkeleton() {
  return (
    <div className="flex items-center gap-3 rounded-lg border p-4">
      <div className="h-4 w-32 animate-pulse rounded bg-muted" />
      <div className="ml-auto h-4 w-16 animate-pulse rounded bg-muted" />
    </div>
  );
}

// ---------- Page ----------

export default function OutreachDashboardPage() {
  const { session } = useAuth();
  const [data, setData] = useState<OutreachDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    if (!session?.access_token) return;
    setLoading(true);
    setError(null);
    try {
      const result = await apiFetch<OutreachDashboard>(
        "/marketing/outreach/dashboard",
        { token: session.access_token }
      );
      setData(result);
    } catch {
      setError(
        "データの取得に失敗しました。しばらく経ってから再度お試しください。"
      );
    } finally {
      setLoading(false);
    }
  }, [session?.access_token]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  async function handleRun() {
    if (!session?.access_token) return;
    setRunning(true);
    setRunResult(null);
    try {
      const result = await apiFetch<RunResult>(
        "/marketing/outreach/run",
        {
          token: session.access_token,
          method: "POST",
          body: { target_count: 400 },
        }
      );
      setRunResult(
        `アウトリーチを開始しました。${result.queued_count}件をキューに追加しました。`
      );
      // データを再取得
      await fetchData();
    } catch {
      setRunResult(
        "アウトリーチの開始に失敗しました。しばらく経ってから再度お試しください。"
      );
    } finally {
      setRunning(false);
    }
  }

  const summary = data?.summary;
  const industries = data?.industry_performance ?? [];
  const hotLeads = data?.hot_leads ?? [];

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold">アウトリーチ管理</h1>
          <p className="text-sm text-muted-foreground">
            AIが企業をリサーチし、メールを自動送信します。本日の成果と反応を確認してください。
          </p>
        </div>
        <Button
          size="lg"
          className="w-full sm:w-auto"
          onClick={handleRun}
          disabled={running}
        >
          {running ? (
            <>
              <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
              送信中...
            </>
          ) : (
            "本日のアウトリーチを実行する"
          )}
        </Button>
      </div>

      {/* Run result feedback */}
      {runResult && (
        <Card
          className={
            runResult.includes("失敗")
              ? "border-destructive/50 bg-destructive/5"
              : "border-green-200 bg-green-50 dark:border-green-900 dark:bg-green-950/30"
          }
        >
          <CardContent className="pt-4">
            <p
              className={`text-sm ${
                runResult.includes("失敗")
                  ? "text-destructive"
                  : "text-green-700 dark:text-green-400"
              }`}
            >
              {runResult}
            </p>
          </CardContent>
        </Card>
      )}

      {/* Error */}
      {error && (
        <Card className="border-destructive/50 bg-destructive/5">
          <CardContent className="pt-4">
            <p className="text-sm text-destructive">{error}</p>
          </CardContent>
        </Card>
      )}

      {/* KPI cards */}
      <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
        {loading ? (
          <>
            <StatCardSkeleton />
            <StatCardSkeleton />
            <StatCardSkeleton />
          </>
        ) : (
          <>
            <Card>
              <CardHeader>
                <CardDescription>本日の送信数</CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-3xl font-bold">
                  {summary ? summary.sent_today.toLocaleString() : "-"}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">目標: 400件/日</p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardDescription>本日の反応数</CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-3xl font-bold">
                  {summary ? summary.replied_today.toLocaleString() : "-"}
                </p>
                {summary && (
                  <p
                    className={`mt-1 text-xs ${rateColor(summary.reply_rate)}`}
                  >
                    反応率:{" "}
                    {summary.sent_today > 0
                      ? `${Math.round(summary.reply_rate * 100)}%`
                      : "-"}
                  </p>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardDescription>本日のホットリード</CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-3xl font-bold text-green-600">
                  {summary ? summary.hot_leads_today.toLocaleString() : "-"}
                </p>
                {summary && summary.hot_leads_today > 0 && (
                  <p className="mt-1 text-xs text-green-600">
                    スコア80以上の有望企業
                  </p>
                )}
              </CardContent>
            </Card>
          </>
        )}
      </div>

      {/* Hot leads */}
      <div className="space-y-3">
        <h2 className="text-lg font-semibold">ホットリード（要対応）</h2>

        {loading ? (
          <div className="space-y-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <RowSkeleton key={i} />
            ))}
          </div>
        ) : hotLeads.length === 0 ? (
          <Card>
            <CardContent className="py-8 text-center">
              <p className="text-sm text-muted-foreground">
                本日のホットリードはまだありません。
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                アウトリーチを実行すると、反応した企業がここに表示されます。
              </p>
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-3">
            {hotLeads.map((lead) => (
              <Card key={lead.id}>
                <CardContent className="flex flex-col gap-2 pt-4 sm:flex-row sm:items-start sm:justify-between">
                  <div className="flex flex-col gap-1.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge className="bg-green-100 text-green-800">
                        ホットリード
                      </Badge>
                      <Badge variant="secondary">
                        {lead.industry_label}
                      </Badge>
                      <span className="font-medium">{lead.company_name}</span>
                      {lead.contact_name && (
                        <span className="text-sm text-muted-foreground">
                          {lead.contact_name} 様
                        </span>
                      )}
                    </div>
                    {lead.pain_points.length > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {lead.pain_points.map((pain, idx) => (
                          <span
                            key={idx}
                            className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground"
                          >
                            {pain}
                          </span>
                        ))}
                      </div>
                    )}
                    <p className="text-xs text-muted-foreground">
                      最終活動: {formatDate(lead.last_activity_at)}
                    </p>
                  </div>
                  <div className="flex items-center gap-3 sm:flex-col sm:items-end sm:gap-1">
                    <p className="text-xs text-muted-foreground">スコア</p>
                    <p className={`text-2xl ${scoreColor(lead.score)}`}>
                      {lead.score}
                    </p>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>

      {/* Industry performance */}
      <div className="space-y-3">
        <h2 className="text-lg font-semibold">業種別パフォーマンス</h2>

        {loading ? (
          <div className="space-y-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <RowSkeleton key={i} />
            ))}
          </div>
        ) : industries.length === 0 ? (
          <Card>
            <CardContent className="py-8 text-center">
              <p className="text-sm text-muted-foreground">
                業種別データがまだありません。
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                アウトリーチを実行すると、業種ごとの成果が表示されます。
              </p>
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-3 grid-cols-1 sm:grid-cols-2">
            {industries.map((ind) => (
              <Card key={ind.industry}>
                <CardContent className="pt-4">
                  <div className="flex items-center justify-between">
                    <span className="text-base font-medium">
                      {ind.industry_label}
                    </span>
                    <Badge variant="outline">
                      反応率{" "}
                      {ind.sent > 0
                        ? `${Math.round(ind.reply_rate * 100)}%`
                        : "-"}
                    </Badge>
                  </div>
                  <div className="mt-3 grid grid-cols-3 gap-2 text-center">
                    <div>
                      <p className="text-lg font-bold">
                        {ind.sent.toLocaleString()}
                      </p>
                      <p className="text-xs text-muted-foreground">送信</p>
                    </div>
                    <div>
                      <p className="text-lg font-bold">
                        {ind.replied.toLocaleString()}
                      </p>
                      <p className="text-xs text-muted-foreground">反応</p>
                    </div>
                    <div>
                      <p className={`text-lg font-bold ${ind.hot_leads > 0 ? "text-green-600" : ""}`}>
                        {ind.hot_leads.toLocaleString()}
                      </p>
                      <p className="text-xs text-muted-foreground">ホット</p>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
