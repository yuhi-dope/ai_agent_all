"use client";

import { useEffect, useState, useCallback } from "react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";

// ---------- Types ----------

interface Variant {
  id: string;
  label: string;
  content: string;
  sent_count: number;
  open_count: number;
  reply_count: number;
  hot_lead_count: number;
  open_rate: number;
  reply_rate: number;
  hot_lead_rate: number;
  is_winner: boolean;
}

interface ABTest {
  id: string;
  name: string;
  test_type: "subject" | "body";
  status: "running" | "completed" | "paused";
  industry: string;
  industry_label: string;
  started_at: string;
  ended_at: string | null;
  variants: Variant[];
  total_sent: number;
  winner_variant_id: string | null;
  insight: string | null;
}

interface ABTestListResponse {
  items: ABTest[];
  total: number;
}

// ---------- Helpers ----------

const statusConfig: Record<
  ABTest["status"],
  { label: string; badgeClass: string }
> = {
  running: { label: "実施中", badgeClass: "bg-green-100 text-green-800" },
  completed: { label: "完了", badgeClass: "bg-muted text-muted-foreground" },
  paused: { label: "一時停止", badgeClass: "bg-yellow-100 text-yellow-800" },
};

const testTypeLabel: Record<ABTest["test_type"], string> = {
  subject: "件名テスト",
  body: "本文テスト",
};

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("ja-JP", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function rateToPercent(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

function rateClass(rate: number, baseline: number): string {
  if (rate > baseline * 1.1) return "text-green-600 font-semibold";
  if (rate < baseline * 0.9) return "text-destructive";
  return "";
}

// ---------- Skeletons ----------

function TestCardSkeleton() {
  return (
    <Card>
      <CardContent className="flex flex-col gap-3 pt-4">
        <div className="flex items-start justify-between gap-2">
          <div className="flex flex-col gap-2">
            <div className="h-5 w-48 animate-pulse rounded bg-muted" />
            <div className="flex gap-2">
              <div className="h-4 w-16 animate-pulse rounded-full bg-muted" />
              <div className="h-4 w-20 animate-pulse rounded-full bg-muted" />
            </div>
          </div>
          <div className="h-6 w-16 animate-pulse rounded-full bg-muted" />
        </div>
        <div className="h-32 animate-pulse rounded bg-muted" />
      </CardContent>
    </Card>
  );
}

// ---------- VariantRow component ----------

function VariantRow({
  variant,
  baseline,
  isWinner,
}: {
  variant: Variant;
  baseline: number;
  isWinner: boolean;
}) {
  return (
    <div
      className={`rounded-lg border p-4 ${
        isWinner
          ? "border-green-300 bg-green-50 dark:border-green-800 dark:bg-green-950/30"
          : "border-border bg-card"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{variant.label}</span>
        {isWinner && (
          <Badge className="bg-green-100 text-green-800">優勝</Badge>
        )}
      </div>
      {/* Content preview */}
      <p className="mt-2 rounded-md bg-muted/50 px-3 py-2 text-sm text-muted-foreground [display:-webkit-box] [-webkit-line-clamp:2] [-webkit-box-orient:vertical] overflow-hidden">
        {variant.content}
      </p>
      {/* Metrics */}
      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
        <div>
          <p className="text-xs text-muted-foreground">送信数</p>
          <p className="text-base font-semibold">
            {variant.sent_count.toLocaleString()}
          </p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">開封率</p>
          <p
            className={`text-base font-semibold ${rateClass(
              variant.open_rate,
              baseline
            )}`}
          >
            {rateToPercent(variant.open_rate)}
          </p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">反応率</p>
          <p
            className={`text-base font-semibold ${rateClass(
              variant.reply_rate,
              baseline
            )}`}
          >
            {rateToPercent(variant.reply_rate)}
          </p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">ホットリード</p>
          <p
            className={`text-base font-semibold ${
              variant.hot_lead_count > 0 ? "text-green-600" : ""
            }`}
          >
            {variant.hot_lead_count}件
          </p>
        </div>
      </div>
    </div>
  );
}

// ---------- Page ----------

export default function ABTestsPage() {
  const { session } = useAuth();
  const [tests, setTests] = useState<ABTest[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"running" | "completed">("running");

  const fetchData = useCallback(async () => {
    if (!session?.access_token) return;
    setLoading(true);
    setError(null);
    try {
      const result = await apiFetch<ABTestListResponse>(
        "/marketing/ab-tests",
        {
          token: session.access_token,
          params: { status: activeTab, limit: "30" },
        }
      );
      setTests(result.items);
      setTotal(result.total);
    } catch {
      setError(
        "A/Bテストデータの取得に失敗しました。しばらく経ってから再度お試しください。"
      );
    } finally {
      setLoading(false);
    }
  }, [session?.access_token, activeTab]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">メール A/B テスト</h1>
        <p className="text-sm text-muted-foreground">
          件名・本文のバリアント別の成果を比較して、最もよく反応されるメールを特定します。
        </p>
      </div>

      {/* Error */}
      {error && (
        <Card className="border-destructive/50 bg-destructive/5">
          <CardContent className="pt-4">
            <p className="text-sm text-destructive">{error}</p>
          </CardContent>
        </Card>
      )}

      {/* Tabs */}
      <Tabs
        value={activeTab}
        onValueChange={(v) => {
          setActiveTab(v as "running" | "completed");
        }}
      >
        <TabsList>
          <TabsTrigger value="running">実施中</TabsTrigger>
          <TabsTrigger value="completed">完了済み</TabsTrigger>
        </TabsList>

        <TabsContent value="running" className="mt-4 space-y-4">
          {loading ? (
            <div className="space-y-4">
              {Array.from({ length: 3 }).map((_, i) => (
                <TestCardSkeleton key={i} />
              ))}
            </div>
          ) : tests.length === 0 ? (
            <Card>
              <CardContent className="flex flex-col items-center gap-4 py-10 text-center">
                <p className="text-sm text-muted-foreground">
                  現在実施中の A/B テストはありません。
                </p>
                <p className="text-xs text-muted-foreground">
                  アウトリーチを実行すると、AIが自動でバリアントを作成してテストを開始します。
                </p>
              </CardContent>
            </Card>
          ) : (
            <TestList tests={tests} />
          )}
        </TabsContent>

        <TabsContent value="completed" className="mt-4 space-y-4">
          {loading ? (
            <div className="space-y-4">
              {Array.from({ length: 3 }).map((_, i) => (
                <TestCardSkeleton key={i} />
              ))}
            </div>
          ) : tests.length === 0 ? (
            <Card>
              <CardContent className="flex flex-col items-center gap-4 py-10 text-center">
                <p className="text-sm text-muted-foreground">
                  完了した A/B テストはまだありません。
                </p>
                <p className="text-xs text-muted-foreground">
                  テストが完了すると、ここに結果と学習内容が表示されます。
                </p>
              </CardContent>
            </Card>
          ) : (
            <TestList tests={tests} />
          )}
        </TabsContent>
      </Tabs>

      {!loading && total > 0 && (
        <p className="text-xs text-muted-foreground text-center">
          合計 {total} 件のテスト
        </p>
      )}
    </div>
  );
}

// ---------- TestList sub-component ----------

function TestList({ tests }: { tests: ABTest[] }) {
  return (
    <div className="space-y-4">
      {tests.map((test) => {
        const statusConf = statusConfig[test.status];
        // ベースライン: バリアント全体の平均反応率
        const avgReplyRate =
          test.variants.length > 0
            ? test.variants.reduce((sum, v) => sum + v.reply_rate, 0) /
              test.variants.length
            : 0;

        return (
          <Card key={test.id}>
            <CardContent className="flex flex-col gap-4 pt-4">
              {/* Test header */}
              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div className="flex flex-col gap-1.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-base font-medium">{test.name}</span>
                    <Badge className={statusConf.badgeClass}>
                      {statusConf.label}
                    </Badge>
                    <Badge variant="outline">
                      {testTypeLabel[test.test_type]}
                    </Badge>
                    <Badge variant="secondary">
                      {test.industry_label || test.industry}
                    </Badge>
                  </div>
                  <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                    <span>開始: {formatDate(test.started_at)}</span>
                    {test.ended_at && (
                      <span>終了: {formatDate(test.ended_at)}</span>
                    )}
                    <span>
                      総送信数: {test.total_sent.toLocaleString()}件
                    </span>
                  </div>
                </div>
              </div>

              {/* Variants */}
              <div className="space-y-3">
                {test.variants.map((variant) => (
                  <VariantRow
                    key={variant.id}
                    variant={variant}
                    baseline={avgReplyRate}
                    isWinner={variant.id === test.winner_variant_id}
                  />
                ))}
              </div>

              {/* AI insight */}
              {test.insight && (
                <div className="rounded-lg border border-primary/20 bg-primary/5 px-4 py-3">
                  <p className="mb-1 text-xs font-medium text-primary">
                    AIの分析コメント
                  </p>
                  <p className="text-sm text-foreground">{test.insight}</p>
                </div>
              )}

              {/* Rate comparison bar */}
              {test.variants.length >= 2 && (
                <div className="space-y-2">
                  <p className="text-xs font-medium text-muted-foreground">
                    反応率の比較
                  </p>
                  <div className="space-y-1.5">
                    {test.variants.map((variant) => {
                      const maxRate = Math.max(
                        ...test.variants.map((v) => v.reply_rate),
                        0.01
                      );
                      const barWidth = Math.round(
                        (variant.reply_rate / maxRate) * 100
                      );
                      const isWinner = variant.id === test.winner_variant_id;
                      return (
                        <div key={variant.id} className="flex items-center gap-2">
                          <span className="w-20 shrink-0 text-xs text-muted-foreground truncate">
                            {variant.label}
                          </span>
                          <div className="flex-1 overflow-hidden rounded-full bg-muted h-4">
                            <div
                              className={`h-full rounded-full transition-all ${
                                isWinner ? "bg-green-500" : "bg-primary/40"
                              }`}
                              style={{ width: `${barWidth}%` }}
                            />
                          </div>
                          <span className="w-12 shrink-0 text-right text-xs font-medium">
                            {rateToPercent(variant.reply_rate)}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
