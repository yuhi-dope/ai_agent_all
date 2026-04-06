"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch, type PaginatedResponse } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

// ---------- Onboarding types ----------

interface OnboardingStatus {
  industry: string | null;
  template_applied: boolean;
  knowledge_count: number;
  onboarding_progress: number;
  suggested_questions: string[];
}

// ---------- Types ----------

interface KnowledgeItem {
  id: string;
  title: string;
  content: string;
  department: string;
  category: string;
  item_type: string;
  confidence: number;
  version: number;
  created_at: string;
}

interface Proposal {
  id: string;
  proposal_type: string;
  title: string;
  description: string;
  impact_estimate: string;
  evidence: string;
  priority: string;
  status: string;
  created_at: string;
}

interface NextStep {
  feature: string;
  reason: string;
  expected_benefit: string;
  action_url: string;
  priority: number;
}

interface ExpansionResult {
  company_id: string;
  current_stage: string;
  usage_score: number;
  next_steps: NextStep[];
  computed_at: string;
}

interface BenchmarkMetric {
  metric_name: string;
  my_value: number;
  industry_avg: number;
  industry_percentile: number;
  unit: string;
  insight: string;
}

interface BenchmarkResult {
  company_id: string;
  industry: string;
  company_count: number | null;
  metrics: BenchmarkMetric[];
  computed_at: string;
  is_available: boolean;
  unavailable_reason: string | null;
}

interface TwinSnapshot {
  id: string;
  snapshot_at: string;
  people_state: Record<string, unknown>;
  process_state: Record<string, unknown>;
  cost_state: Record<string, unknown>;
  tool_state: Record<string, unknown>;
  risk_state: Record<string, unknown>;
}

// ---------- Helpers ----------

const proposalTypeBadge: Record<string, { label: string; variant: "destructive" | "default" | "secondary" | "outline" }> = {
  risk_alert: { label: "リスク", variant: "destructive" },
  improvement: { label: "改善", variant: "default" },
  rule_challenge: { label: "ルール見直し", variant: "secondary" },
  opportunity: { label: "機会", variant: "outline" },
};

const priorityBadge: Record<string, { label: string; variant: "destructive" | "default" | "secondary" | "outline" }> = {
  critical: { label: "最重要", variant: "destructive" },
  high: { label: "高", variant: "destructive" },
  medium: { label: "中", variant: "secondary" },
  low: { label: "低", variant: "outline" },
};

const categoryLabels: Record<string, string> = {
  rule: "ルール",
  process: "プロセス",
  decision: "意思決定",
  knowledge: "ナレッジ",
  policy: "ポリシー",
};

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("ja-JP", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatShortDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("ja-JP", {
    month: "short",
    day: "numeric",
  });
}

function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return text.slice(0, max) + "...";
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
      </CardContent>
    </Card>
  );
}

function ProposalCardSkeleton() {
  return (
    <Card>
      <CardContent className="flex flex-col gap-2 pt-4">
        <div className="flex items-center gap-2">
          <div className="h-5 w-14 animate-pulse rounded-full bg-muted" />
          <div className="h-4 w-48 animate-pulse rounded bg-muted" />
        </div>
        <div className="h-3 w-full animate-pulse rounded bg-muted" />
        <div className="h-3 w-2/3 animate-pulse rounded bg-muted" />
      </CardContent>
    </Card>
  );
}

function KnowledgeCardSkeleton() {
  return (
    <Card>
      <CardContent className="flex flex-col gap-2 pt-4">
        <div className="flex items-center gap-2">
          <div className="h-5 w-16 animate-pulse rounded-full bg-muted" />
          <div className="h-4 w-40 animate-pulse rounded bg-muted" />
        </div>
        <div className="h-3 w-full animate-pulse rounded bg-muted" />
      </CardContent>
    </Card>
  );
}

function NextStepCardSkeleton() {
  return (
    <Card>
      <CardContent className="flex flex-col gap-2 pt-4">
        <div className="h-4 w-32 animate-pulse rounded bg-muted" />
        <div className="h-3 w-full animate-pulse rounded bg-muted" />
        <div className="h-3 w-2/3 animate-pulse rounded bg-muted" />
        <div className="mt-2 h-8 w-28 animate-pulse rounded bg-muted" />
      </CardContent>
    </Card>
  );
}

function BenchmarkCardSkeleton() {
  return (
    <Card>
      <CardContent className="flex flex-col gap-3 pt-4">
        <div className="h-4 w-40 animate-pulse rounded bg-muted" />
        <div className="h-3 w-full animate-pulse rounded bg-muted" />
        <div className="h-3 w-3/4 animate-pulse rounded bg-muted" />
        <div className="h-3 w-full animate-pulse rounded bg-muted" />
        <div className="h-3 w-2/3 animate-pulse rounded bg-muted" />
      </CardContent>
    </Card>
  );
}

// ---------- メトリクスラベル ----------

const metricLabels: Record<string, string> = {
  pipeline_run_monthly_avg: "業務自動化の実行回数",
  qa_usage_monthly_avg: "AIへの質問回数",
  connector_sync_monthly_avg: "外部連携の同期回数",
};

// ---------- BenchmarkCard component ----------

function BenchmarkCard({
  benchmark,
  loading,
}: {
  benchmark: BenchmarkResult | null;
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="space-y-3">
        <h2 className="text-lg font-semibold">同業他社との比較</h2>
        <BenchmarkCardSkeleton />
        <p className="text-xs text-muted-foreground">
          AIが分析しています。少しお待ちください...
        </p>
      </div>
    );
  }

  if (!benchmark || !benchmark.is_available) {
    return (
      <div className="space-y-3">
        <h2 className="text-lg font-semibold">同業他社との比較</h2>
        <Card className="border-yellow-200 bg-yellow-50 dark:border-yellow-900 dark:bg-yellow-950/30">
          <CardContent className="flex flex-col gap-2 py-6">
            <p className="text-sm font-medium text-yellow-800 dark:text-yellow-300">
              データ収集中
            </p>
            <p className="text-sm text-yellow-700 dark:text-yellow-400">
              {benchmark?.unavailable_reason ??
                "同業種の企業データが蓄積されると、業界平均との比較が表示されます。"}
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">同業他社との比較</h2>
        {benchmark.company_count !== null && (
          <span className="text-xs text-muted-foreground">
            同業種{benchmark.company_count}社のデータをもとに集計
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {benchmark.metrics.map((metric) => {
          const isAboveAvg = metric.my_value >= metric.industry_avg;
          const diffPct =
            metric.industry_avg > 0
              ? Math.round(
                  ((metric.my_value - metric.industry_avg) /
                    metric.industry_avg) *
                    100
                )
              : 0;
          const label =
            metricLabels[metric.metric_name] ?? metric.metric_name;

          return (
            <Card key={metric.metric_name} className="flex flex-col">
              <CardHeader className="pb-2">
                <CardTitle className="text-base font-medium">{label}</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-1 flex-col gap-3">
                {/* 自社値 vs 業界平均 */}
                <div className="flex items-end gap-4">
                  <div>
                    <p className="text-xs text-muted-foreground">自社</p>
                    <p
                      className={`text-2xl font-bold ${
                        isAboveAvg ? "text-green-600" : "text-yellow-600"
                      }`}
                    >
                      {metric.my_value.toLocaleString("ja-JP", {
                        maximumFractionDigits: 1,
                      })}
                      <span className="ml-1 text-sm font-normal text-muted-foreground">
                        {metric.unit}
                      </span>
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">業界平均</p>
                    <p className="text-lg font-semibold text-muted-foreground">
                      {metric.industry_avg.toLocaleString("ja-JP", {
                        maximumFractionDigits: 1,
                      })}
                      <span className="ml-1 text-sm font-normal">
                        {metric.unit}
                      </span>
                    </p>
                  </div>
                </div>

                {/* バーグラフ */}
                <div className="space-y-1">
                  <div className="flex justify-between text-xs text-muted-foreground">
                    <span>自社</span>
                    <span
                      className={
                        isAboveAvg ? "text-green-600" : "text-yellow-600"
                      }
                    >
                      {diffPct >= 0 ? `+${diffPct}%` : `${diffPct}%`}
                    </span>
                  </div>
                  <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                    <div
                      className={`h-full rounded-full transition-all ${
                        isAboveAvg ? "bg-green-500" : "bg-yellow-500"
                      }`}
                      style={{
                        width: `${Math.min(metric.industry_percentile, 100)}%`,
                      }}
                    />
                  </div>
                  <p className="text-xs text-muted-foreground">
                    同業他社の上位{100 - metric.industry_percentile}%
                  </p>
                </div>

                {/* LLM生成のinsight */}
                <p className="rounded-md bg-muted/50 px-3 py-2 text-xs text-foreground">
                  {metric.insight}
                </p>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

// ---------- NextStepsCard component ----------

function NextStepsCard({
  expansion,
  loading,
}: {
  expansion: ExpansionResult | null;
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="space-y-3">
        <h2 className="text-lg font-semibold">次のステップ</h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <NextStepCardSkeleton key={i} />
          ))}
        </div>
        <p className="text-xs text-muted-foreground">
          AIが分析しています。少しお待ちください...
        </p>
      </div>
    );
  }

  if (!expansion || expansion.next_steps.length === 0) {
    return (
      <div className="space-y-3">
        <h2 className="text-lg font-semibold">次のステップ</h2>
        <Card>
          <CardContent className="flex flex-col items-center gap-4 py-8 text-center">
            <p className="text-sm text-muted-foreground">
              まだ提案はありません。業務自動化を使い始めると、次のステップが自動で表示されます。
            </p>
            <Link href="/bpo">
              <Button>業務自動化を試してみる</Button>
            </Link>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <h2 className="text-lg font-semibold">次のステップ</h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {expansion.next_steps.map((step, index) => (
          <Card key={index} className="flex flex-col">
            <CardHeader className="pb-2">
              <CardTitle className="text-base font-medium">{step.feature}</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-1 flex-col gap-3">
              <p className="text-sm text-muted-foreground">{step.reason}</p>
              <p className="rounded-md bg-muted/50 px-3 py-2 text-xs text-foreground">
                {step.expected_benefit}
              </p>
              <div className="mt-auto pt-1">
                <Link href={step.action_url}>
                  <Button size="sm" className="w-full sm:w-auto">
                    {step.feature}を見る
                  </Button>
                </Link>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}

// ---------- Page ----------

export default function DashboardPage() {
  const { user, session } = useAuth();
  const [knowledgeCount, setKnowledgeCount] = useState<number | null>(null);
  const [proposalCount, setProposalCount] = useState<number | null>(null);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [recentKnowledge, setRecentKnowledge] = useState<KnowledgeItem[]>([]);
  const [snapshotDate, setSnapshotDate] = useState<string | null>(null);
  const [wauRate, setWauRate] = useState<number | null>(null);
  const [expansion, setExpansion] = useState<ExpansionResult | null>(null);
  const [expansionLoading, setExpansionLoading] = useState(true);
  const [benchmark, setBenchmark] = useState<BenchmarkResult | null>(null);
  const [benchmarkLoading, setBenchmarkLoading] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedKnowledgeId, setExpandedKnowledgeId] = useState<string | null>(null);
  const [onboardingStatus, setOnboardingStatus] = useState<OnboardingStatus | null>(null);

  useEffect(() => {
    if (!session?.access_token) return;

    const token = session.access_token;

    // Fetch onboarding status independently
    apiFetch<OnboardingStatus>("/onboarding/status", { token })
      .then(setOnboardingStatus)
      .catch(() => {});

    // Fetch expansion independently (non-blocking)
    setExpansionLoading(true);
    apiFetch<ExpansionResult>("/dashboard/expansion", { token })
      .then(setExpansion)
      .catch(() => {})
      .finally(() => setExpansionLoading(false));

    // Fetch benchmark independently (non-blocking)
    setBenchmarkLoading(true);
    apiFetch<BenchmarkResult>("/dashboard/benchmark", { token })
      .then(setBenchmark)
      .catch(() => {})
      .finally(() => setBenchmarkLoading(false));

    async function fetchData() {
      setLoading(true);
      setError(null);
      try {
        const [
          knowledgeCountRes,
          proposalCountRes,
          recentKnowledgeRes,
          proposalRes,
          snapshotRes,
          summaryRes,
        ] = await Promise.allSettled([
          apiFetch<PaginatedResponse<KnowledgeItem>>("/knowledge/items", {
            token,
            params: { limit: "1" },
          }),
          apiFetch<PaginatedResponse<Proposal>>("/proactive/proposals", {
            token,
            params: { status: "proposed", limit: "1" },
          }),
          apiFetch<PaginatedResponse<KnowledgeItem>>("/knowledge/items", {
            token,
            params: { limit: "3" },
          }),
          apiFetch<PaginatedResponse<Proposal>>("/proactive/proposals", {
            token,
            params: { status: "proposed", limit: "3" },
          }),
          apiFetch<TwinSnapshot>("/twin/snapshot", { token }),
          apiFetch<{
            wau_rate: number | null;
            wau_active_users: number | null;
            wau_total_users: number | null;
          }>("/dashboard/summary", { token }),
        ]);

        if (knowledgeCountRes.status === "fulfilled") {
          setKnowledgeCount(knowledgeCountRes.value.total);
        }

        if (proposalCountRes.status === "fulfilled") {
          setProposalCount(proposalCountRes.value.total);
        }

        if (recentKnowledgeRes.status === "fulfilled") {
          setRecentKnowledge(recentKnowledgeRes.value.items);
        }

        if (proposalRes.status === "fulfilled") {
          setProposals(proposalRes.value.items);
        }

        if (snapshotRes.status === "fulfilled") {
          setSnapshotDate(snapshotRes.value.snapshot_at);
        }

        if (summaryRes.status === "fulfilled" && summaryRes.value.wau_rate !== null) {
          setWauRate(summaryRes.value.wau_rate);
        }
      } catch {
        setError("データの取得に失敗しました。しばらく経ってから再度お試しください");
      } finally {
        setLoading(false);
      }
    }

    fetchData();
  }, [session?.access_token]);

  const displayName =
    user?.user_metadata?.full_name || user?.email || "ユーザー";

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      {/* Welcome */}
      <div>
        <h1 className="text-2xl font-bold">ようこそ、{displayName} さん</h1>
        <p className="text-sm text-muted-foreground">
          {onboardingStatus !== null &&
          onboardingStatus.onboarding_progress >= 1.0 &&
          onboardingStatus.knowledge_count >= 3
            ? "最新の状況をご確認ください。"
            : "AIがあなたの会社を学習しています。以下のステップで始めましょう。"}
        </p>
      </div>

      {/* Getting started guide panel */}
      {!(
        onboardingStatus !== null &&
        onboardingStatus.onboarding_progress >= 1.0 &&
        (knowledgeCount ?? 0) > 0
      ) && (
        <Card className="border-primary/30 bg-primary/5">
          <CardHeader className="pb-3">
            <CardTitle className="text-base font-semibold">AIを使い始める3ステップ</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <ol className="space-y-3">
              <li className="flex items-start gap-3">
                <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-primary/40 text-xs font-bold text-primary">
                  {onboardingStatus !== null && onboardingStatus.onboarding_progress >= 0.5 ? "✓" : "1"}
                </span>
                <div>
                  <p className={`text-sm font-medium ${onboardingStatus !== null && onboardingStatus.onboarding_progress >= 0.5 ? "text-muted-foreground line-through" : ""}`}>
                    業種を選んでテンプレートを適用する
                  </p>
                  {onboardingStatus !== null && onboardingStatus.onboarding_progress >= 0.5 && (
                    <p className="text-xs text-muted-foreground">完了しました</p>
                  )}
                </div>
              </li>
              <li className="flex items-start gap-3">
                <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-primary/40 text-xs font-bold text-primary">
                  {(knowledgeCount ?? 0) > 0 ? "✓" : "2"}
                </span>
                <div>
                  <p className={`text-sm font-medium ${(knowledgeCount ?? 0) > 0 ? "text-muted-foreground line-through" : ""}`}>
                    会社のルール・ノウハウ（ナレッジ）を入力する
                  </p>
                  {(knowledgeCount ?? 0) > 0 && (
                    <p className="text-xs text-muted-foreground">完了しました</p>
                  )}
                </div>
              </li>
              <li className="flex items-start gap-3">
                <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-primary/40 text-xs font-bold text-primary">
                  3
                </span>
                <div>
                  <p className="text-sm font-medium">AIに質問して答えを確かめる</p>
                  <p className="text-xs text-muted-foreground">ナレッジを入力したら、AIに質問してみましょう</p>
                </div>
              </li>
            </ol>
            <div className="flex flex-wrap gap-2 pt-1">
              {(knowledgeCount ?? 0) === 0 && (
                <Link href="/knowledge/input">
                  <Button size="sm">ナレッジを入力する</Button>
                </Link>
              )}
              <Link href="/knowledge/qa">
                <Button size="sm" variant={((knowledgeCount ?? 0) === 0) ? "outline" : "default"}>
                  AIに質問する
                </Button>
              </Link>
            </div>
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

      {/* Stat cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {loading ? (
          <>
            <StatCardSkeleton />
            <StatCardSkeleton />
            <StatCardSkeleton />
            <StatCardSkeleton />
          </>
        ) : (
          <>
            <Card>
              <CardHeader>
                <CardDescription>ナレッジ数</CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-3xl font-bold">
                  {knowledgeCount !== null ? knowledgeCount.toLocaleString() : "-"}
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardDescription>提案数（未対応）</CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-3xl font-bold">
                  {proposalCount !== null ? proposalCount.toLocaleString() : "-"}
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardDescription>最新スナップショット</CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-lg font-semibold">
                  {snapshotDate ? formatDate(snapshotDate) : "未取得"}
                </p>
              </CardContent>
            </Card>

            {/* 今月のAIコストは管理者のみ表示 */}

            <Card>
              <CardHeader>
                <CardDescription>週次利用率（WAU）</CardDescription>
              </CardHeader>
              <CardContent>
                <p className={`text-3xl font-bold ${
                  wauRate !== null && wauRate >= 0.6
                    ? "text-green-600"
                    : wauRate !== null && wauRate >= 0.3
                    ? "text-yellow-600"
                    : "text-muted-foreground"
                }`}>
                  {wauRate !== null ? `${Math.round(wauRate * 100)}%` : "-"}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  目標: 60%以上
                </p>
              </CardContent>
            </Card>
          </>
        )}
      </div>

      {/* Recent knowledge items */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">最近のナレッジ</h2>
          {knowledgeCount !== null && knowledgeCount > 3 && (
            <Link href="/knowledge">
              <Button variant="ghost" size="sm">
                一覧を見る ({knowledgeCount}件)
              </Button>
            </Link>
          )}
        </div>

        {loading ? (
          <div className="space-y-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <KnowledgeCardSkeleton key={i} />
            ))}
          </div>
        ) : recentKnowledge.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center gap-4 py-8 text-center">
              <p className="text-sm text-muted-foreground">まだナレッジが登録されていません。</p>
              <Link href="/knowledge/input">
                <Button>ナレッジを入力する</Button>
              </Link>
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-3">
            {recentKnowledge.map((item) => {
              const isExpanded = expandedKnowledgeId === item.id;
              return (
                <Card key={item.id}>
                  <CardContent className="flex flex-col gap-1.5 pt-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="secondary">
                        {item.department}
                      </Badge>
                      <Badge variant="outline">
                        {categoryLabels[item.category] || item.category}
                      </Badge>
                      <span className="font-medium">{item.title}</span>
                      <span className="ml-auto text-xs text-muted-foreground">
                        {formatShortDate(item.created_at)}
                      </span>
                    </div>
                    <button
                      type="button"
                      className="mt-1 text-left text-xs text-muted-foreground hover:text-foreground transition-colors"
                      onClick={() =>
                        setExpandedKnowledgeId(isExpanded ? null : item.id)
                      }
                    >
                      {isExpanded ? "閉じる" : "内容を表示"}
                    </button>
                    {isExpanded && (
                      <p className="mt-1 whitespace-pre-wrap rounded-md border bg-muted/50 p-3 text-sm">
                        {item.content}
                      </p>
                    )}
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}
      </div>

      {/* Recent proposals */}
      <div className="space-y-3">
        <h2 className="text-lg font-semibold">最近の提案</h2>

        {loading ? (
          <div className="space-y-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <ProposalCardSkeleton key={i} />
            ))}
          </div>
        ) : proposals.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center gap-4 py-8 text-center">
              <p className="text-sm text-muted-foreground">
                まだAI提案はありません。ナレッジを入力後、分析を実行すると自動で提案が届きます。
              </p>
              {(knowledgeCount ?? 0) === 0 ? (
                <Link href="/knowledge/input">
                  <Button variant="outline">ナレッジを入力する</Button>
                </Link>
              ) : (
                <Link href="/proposals">
                  <Button>分析を実行する</Button>
                </Link>
              )}
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-3">
            {proposals.map((proposal) => {
              const typeBadge = proposalTypeBadge[proposal.proposal_type] || {
                label: proposal.proposal_type,
                variant: "outline" as const,
              };
              const prioBadge = priorityBadge[proposal.priority] || {
                label: proposal.priority || "中",
                variant: "secondary" as const,
              };
              return (
                <Card key={proposal.id}>
                  <CardContent className="flex flex-col gap-1.5 pt-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant={typeBadge.variant}>{typeBadge.label}</Badge>
                      <Badge variant={prioBadge.variant}>
                        優先度: {prioBadge.label}
                      </Badge>
                      <span className="font-medium">{proposal.title}</span>
                    </div>
                    <p className="text-sm text-muted-foreground">
                      {truncate(proposal.description, 120)}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {formatDate(proposal.created_at)}
                    </p>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}
      </div>

      {/* BPO CTA card — shown when knowledge >= 3 and data is loaded */}
      {!loading && (knowledgeCount ?? 0) >= 3 && (
        <Card className="border-green-200 bg-green-50 dark:border-green-900 dark:bg-green-950/30">
          <CardContent className="flex flex-col gap-4 py-6 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-3">
              <span className="mt-0.5 text-2xl" aria-hidden="true">&#x1F916;</span>
              <div>
                <p className="font-semibold text-green-800 dark:text-green-300">業務自動化を試してみましょう</p>
                <p className="mt-0.5 text-sm text-green-700 dark:text-green-400">
                  ナレッジが蓄積されました。AIを使った業務自動化をお試しください。
                </p>
              </div>
            </div>
            <Link href="/bpo" className="shrink-0">
              <Button className="w-full sm:w-auto">業務自動化を試す</Button>
            </Link>
          </CardContent>
        </Card>
      )}

      {/* Next steps card (Land and Expand) */}
      <NextStepsCard expansion={expansion} loading={expansionLoading} />

      {/* Benchmark card (REQ-2004: ネットワーク効果・匿名ベンチマーク) */}
      <BenchmarkCard benchmark={benchmark} loading={benchmarkLoading} />
    </div>
  );
}
