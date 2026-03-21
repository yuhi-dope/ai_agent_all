"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch, type PaginatedResponse } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

type ProposalType = "risk_alert" | "improvement" | "rule_challenge" | "opportunity";
type ProposalStatus = "pending" | "accepted" | "rejected";
type ProposalPriority = "low" | "medium" | "high" | "critical";

interface ImpactEstimate {
  time_saved_hours?: number;
  cost_saved_yen?: number;
  description?: string;
}

interface Proposal {
  id: string;
  proposal_type: ProposalType;
  status: ProposalStatus;
  title: string;
  description: string;
  impact_estimate?: ImpactEstimate;
  priority: ProposalPriority;
  created_at: string;
}

const PROPOSAL_TYPE_CONFIG: Record<
  ProposalType,
  { label: string; variant: "destructive" | "default" | "secondary" | "outline" }
> = {
  risk_alert: { label: "リスク", variant: "destructive" },
  improvement: { label: "改善", variant: "default" },
  rule_challenge: { label: "ルール見直し", variant: "secondary" },
  opportunity: { label: "機会", variant: "outline" },
};

const PRIORITY_CONFIG: Record<
  ProposalPriority,
  { label: string; variant: "destructive" | "default" | "secondary" | "outline" }
> = {
  critical: { label: "最重要", variant: "destructive" },
  high: { label: "高", variant: "destructive" },
  medium: { label: "中", variant: "secondary" },
  low: { label: "低", variant: "outline" },
};

const PAGE_SIZE = 10;

type TabValue = "all" | "pending" | "accepted" | "rejected";

export default function ProposalsPage() {
  const { session } = useAuth();
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [offset, setOffset] = useState(0);
  const [activeTab, setActiveTab] = useState<TabValue>("all");
  const [loading, setLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchProposals = useCallback(
    async (status: TabValue, pageOffset: number) => {
      if (!session?.access_token) return;
      setLoading(true);
      setError(null);
      try {
        const params: Record<string, string> = {
          limit: String(PAGE_SIZE),
          offset: String(pageOffset),
        };
        if (status !== "all") {
          params.status = status;
        }
        const res = await apiFetch<PaginatedResponse<Proposal>>(
          "/proactive/proposals",
          {
            token: session?.access_token,
            params,
          }
        );
        setProposals(res.items);
        setTotal(res.total);
        setHasMore(res.has_more);
      } catch (err) {
        setError("提案の取得に失敗しました。しばらく経ってから再度お試しください");
      } finally {
        setLoading(false);
      }
    },
    [session?.access_token]
  );

  useEffect(() => {
    if (!session?.access_token) return;
    fetchProposals(activeTab, offset);
  }, [activeTab, offset, fetchProposals, session?.access_token]);

  function handleTabChange(value: TabValue) {
    setActiveTab(value);
    setOffset(0);
  }

  function formatDate(dateString: string): string {
    return new Date(dateString).toLocaleDateString("ja-JP", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
  }

  function renderProposalCard(proposal: Proposal) {
    const typeConfig = PROPOSAL_TYPE_CONFIG[proposal.proposal_type] ?? PROPOSAL_TYPE_CONFIG.improvement;
    const priorityConfig = PRIORITY_CONFIG[proposal.priority] ?? PRIORITY_CONFIG.medium;

    return (
      <Card key={proposal.id}>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2 text-base">
            <Badge variant={typeConfig.variant}>{typeConfig.label}</Badge>
            <span>{proposal.title}</span>
          </CardTitle>
          <CardDescription className="flex items-center gap-2">
            <Badge variant={priorityConfig.variant}>
              優先度: {priorityConfig.label}
            </Badge>
            <span>{formatDate(proposal.created_at)}</span>
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm whitespace-pre-wrap">{proposal.description}</p>

          {proposal.impact_estimate && (
            <div className="flex flex-wrap gap-3 rounded-lg border bg-muted/50 p-3 text-sm">
              {proposal.impact_estimate.time_saved_hours != null && (
                <span>
                  時間削減:{" "}
                  <span className="font-medium">
                    {proposal.impact_estimate.time_saved_hours}h
                  </span>
                </span>
              )}
              {proposal.impact_estimate.cost_saved_yen != null && (
                <span>
                  コスト削減:{" "}
                  <span className="font-medium">
                    &yen;{proposal.impact_estimate.cost_saved_yen.toLocaleString()}
                  </span>
                </span>
              )}
              {proposal.impact_estimate.description && (
                <span className="text-muted-foreground">
                  {proposal.impact_estimate.description}
                </span>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    );
  }

  async function handleAnalyze() {
    if (!session?.access_token) return;
    setAnalyzing(true);
    setError(null);
    try {
      await apiFetch<{ proposals_created: number; model_used: string; knowledge_analyzed: number }>(
        "/proactive/analyze",
        { token: session.access_token, method: "POST", body: {} }
      );
      // Refresh proposals list after analysis
      await fetchProposals(activeTab, 0);
      setOffset(0);
    } catch {
      setError("分析に失敗しました。しばらく経ってから再度お試しください");
    } finally {
      setAnalyzing(false);
    }
  }

  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;
  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">AI提案</h1>
          <p className="text-muted-foreground">
            AIが自動で検出したリスク・改善アイデア・機会の一覧です。
          </p>
        </div>
        <Button onClick={handleAnalyze} disabled={analyzing}>
          {analyzing ? "分析中..." : "AI分析を開始する"}
        </Button>
      </div>

      <Tabs
        defaultValue="all"
        onValueChange={(value) => handleTabChange(value as TabValue)}
      >
        <TabsList>
          <TabsTrigger value="all">全て</TabsTrigger>
          <TabsTrigger value="pending">未対応</TabsTrigger>
          <TabsTrigger value="accepted">承認済</TabsTrigger>
          <TabsTrigger value="rejected">却下</TabsTrigger>
        </TabsList>

        {/* All tab values share the same content rendering */}
        {(["all", "pending", "accepted", "rejected"] as const).map((tab) => (
          <TabsContent key={tab} value={tab}>
            {loading ? (
              <div className="flex items-center justify-center py-12">
                <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                <span className="ml-2 text-sm text-muted-foreground">
                  読み込み中...
                </span>
              </div>
            ) : error ? (
              <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
                {error}
              </div>
            ) : proposals.length === 0 ? (
              <div className="py-12 text-center space-y-3">
                <p className="text-sm text-muted-foreground">
                  {tab === "all"
                    ? "まだAI提案はありません。「分析を実行」ボタンを押すと、登録済みのナレッジからリスクや改善案を自動で検出します。"
                    : "該当する提案はありません。"}
                </p>
                {tab === "all" && (
                  <Button variant="outline" size="sm" onClick={handleAnalyze} disabled={analyzing}>
                    {analyzing ? "分析中..." : "分析を実行する"}
                  </Button>
                )}
              </div>
            ) : (
              <div className="space-y-4">
                {proposals.map(renderProposalCard)}
              </div>
            )}
          </TabsContent>
        ))}
      </Tabs>

      {/* Pagination */}
      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between">
          <Button
            variant="outline"
            size="sm"
            disabled={offset === 0}
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
            disabled={!hasMore}
            onClick={() => setOffset((prev) => prev + PAGE_SIZE)}
          >
            次へ
          </Button>
        </div>
      )}
    </div>
  );
}
