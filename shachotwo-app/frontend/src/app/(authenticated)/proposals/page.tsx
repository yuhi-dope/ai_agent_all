"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch, type PaginatedResponse } from "@/lib/api";
import {
  descriptionLooksNested,
  getProactiveParseDebugTrace,
  parseProactiveDescription,
  shouldOfferTechnicalDetails,
  type ParsedProposalItem,
} from "@/lib/parse-proposal-description";
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

/** 一覧 GET は短めにして、未応答時の待ちを抑える */
const PROPOSALS_LIST_TIMEOUT_MS = 12_000;

type TabValue = "all" | "pending" | "accepted" | "rejected";

function isAbortError(err: unknown): boolean {
  if (err instanceof Error && err.name === "AbortError") return true;
  if (typeof DOMException !== "undefined" && err instanceof DOMException && err.name === "AbortError") {
    return true;
  }
  return false;
}

function isTimeoutError(err: unknown): boolean {
  if (err instanceof Error && err.name === "TimeoutError") return true;
  if (
    typeof DOMException !== "undefined" &&
    err instanceof DOMException &&
    err.name === "TimeoutError"
  ) {
    return true;
  }
  const msg = err instanceof Error ? err.message : String(err);
  return /timeout|timed out/i.test(msg);
}

function proposalsListErrorMessage(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err);
  if (isTimeoutError(err)) {
    return (
      "API の応答がタイムアウトしました（一覧は約12秒で打ち切ります）。" +
      "FastAPI（既定: http://127.0.0.1:8000）が起動しているか、Network で " +
      "`/api/v1/proactive/proposals` が pending のままになっていないか確認してください。"
    );
  }
  const network = /failed to fetch|load failed|networkerror|aborted|fetch/i.test(msg);
  if (network) {
    return (
      "API に接続できませんでした。バックエンドが起動しているか確認してください。" +
      "提案の取得に失敗しました。しばらく経ってから再度お試しください。"
    );
  }
  return "提案の取得に失敗しました。しばらく経ってから再度お試しください";
}

function badgeForProposalType(
  t: string | undefined
): { label: string; variant: "destructive" | "default" | "secondary" | "outline" } {
  if (!t) return { label: "提案", variant: "secondary" };
  const key = t as ProposalType;
  if (key in PROPOSAL_TYPE_CONFIG) {
    return PROPOSAL_TYPE_CONFIG[key];
  }
  return { label: t, variant: "outline" };
}

function ImpactEstimateBlockJson({
  ie,
}: {
  ie: Record<string, unknown>;
}) {
  const time = ie.time_saved_hours;
  const costRaw = ie.cost_saved_yen ?? ie.cost_reduction_yen;
  const basis = ie.calculation_basis;
  const conf = ie.confidence;

  const hasNumeric =
    (typeof time === "number" && !Number.isNaN(time)) ||
    (typeof costRaw === "number" && !Number.isNaN(costRaw));

  if (!hasNumeric && typeof basis !== "string" && typeof conf !== "number") {
    return null;
  }

  return (
    <div className="flex flex-wrap gap-3 rounded-lg border bg-muted/40 p-3 text-sm">
      {typeof time === "number" && !Number.isNaN(time) && (
        <span>
          時間削減: <span className="font-medium">{time}h</span>
        </span>
      )}
      {typeof costRaw === "number" && !Number.isNaN(costRaw) && (
        <span>
          コスト削減:{" "}
          <span className="font-medium">&yen;{costRaw.toLocaleString()}</span>
        </span>
      )}
      {typeof conf === "number" && !Number.isNaN(conf) && (
        <span className="text-muted-foreground">信頼度: {(conf * 100).toFixed(0)}%</span>
      )}
      {typeof basis === "string" && basis.trim() !== "" && (
        <span className="w-full text-muted-foreground">{basis}</span>
      )}
    </div>
  );
}

const MAX_STRUCTURED_NEST_DEPTH = 2;

function StructuredProposalItems({
  items,
  nestedLevel = 0,
}: {
  items: ParsedProposalItem[];
  nestedLevel?: number;
}) {
  return (
    <div className="space-y-4">
      {items.map((item, idx) => {
        const typeCfg = badgeForProposalType(item.type);
        const desc = item.description;
        const canTryNested =
          nestedLevel < MAX_STRUCTURED_NEST_DEPTH &&
          Boolean(desc && descriptionLooksNested(desc));
        const reparsed =
          canTryNested && desc ? parseProactiveDescription(desc) : null;
        const nestedStructured =
          reparsed?.kind === "structured" &&
          reparsed.items.length > 0;

        return (
          <div
            key={idx}
            className="space-y-2 rounded-lg border border-border bg-muted/20 p-3"
          >
            <div className="flex flex-wrap items-center gap-2">
              {item.type && (
                <Badge variant={typeCfg.variant}>{typeCfg.label}</Badge>
              )}
              {item.title && (
                <span className="text-sm font-semibold">{item.title}</span>
              )}
              {item.priority && (
                <span className="text-xs text-muted-foreground">
                  優先度: {item.priority}
                </span>
              )}
            </div>
            {item.description &&
              (nestedStructured ? (
                <div
                  className={
                    nestedLevel > 0
                      ? "space-y-2 border-l-2 border-border pl-3"
                      : "space-y-2"
                  }
                >
                  {reparsed.preamble ? (
                    <p className="text-sm leading-relaxed whitespace-pre-wrap text-muted-foreground">
                      {reparsed.preamble}
                    </p>
                  ) : null}
                  <StructuredProposalItems
                    items={reparsed.items}
                    nestedLevel={nestedLevel + 1}
                  />
                </div>
              ) : (
                <p className="text-sm leading-relaxed whitespace-pre-wrap">
                  {item.description}
                </p>
              ))}
            {item.impact_estimate && (
              <ImpactEstimateBlockJson ie={item.impact_estimate} />
            )}
          </div>
        );
      })}
    </div>
  );
}

function ProposalDescriptionSection({
  description,
  debugProposalId,
}: {
  description: string;
  debugProposalId?: string;
}) {
  /** 本番では off。開発時は既定で on（.env で 0 にすると抑制）。明示的に 1 でも on。 */
  const debugParse =
    process.env.NEXT_PUBLIC_DEBUG_PROPOSAL_PARSE === "1" ||
    (process.env.NODE_ENV === "development" &&
      process.env.NEXT_PUBLIC_DEBUG_PROPOSAL_PARSE !== "0");

  useEffect(() => {
    if (!debugParse) return;
    const trace = getProactiveParseDebugTrace(description);
    const id = debugProposalId ?? "(no id)";
    console.info("[proactive-parse]", id, trace);
    console.info("[proactive-parse/extraction]", id, trace.jsonExtraction);
    console.info("[proactive-parse/fence+chars]", id, {
      fenceRegionHex: trace.fenceRegionHex,
      jsonCandidateFirstCharHex: trace.jsonCandidateFirstCharHex,
      jsonCandidateEqualsFullTrimmed: trace.jsonCandidateEqualsFullTrimmed,
      fullwidthGraveCountInRaw: trace.fullwidthGraveCountInRaw,
    });
  }, [description, debugProposalId, debugParse]);

  const parsed = parseProactiveDescription(description);
  if (parsed.kind === "structured") {
    return (
      <div className="space-y-3">
        {parsed.preamble ? (
          <p className="text-sm leading-relaxed whitespace-pre-wrap text-muted-foreground">
            {parsed.preamble}
          </p>
        ) : null}
        <StructuredProposalItems items={parsed.items} />
      </div>
    );
  }
  const showTech = shouldOfferTechnicalDetails(parsed) && description.trim().length > 0;
  if (showTech) {
    return (
      <div className="space-y-2">
        <p className="text-sm text-muted-foreground">
          内容を自動で整形できませんでした。必要に応じて下の「技術的な詳細」をご確認ください。
        </p>
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer hover:underline">
            技術的な詳細（元データ）
          </summary>
          <pre className="mt-2 max-h-48 overflow-auto rounded-md bg-muted p-2 text-[11px] leading-snug">
            {description}
          </pre>
        </details>
      </div>
    );
  }
  return (
    <p className="text-sm leading-relaxed whitespace-pre-wrap">{parsed.text}</p>
  );
}

export default function ProposalsPage() {
  const { session, loading: authLoading } = useAuth();
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [offset, setOffset] = useState(0);
  const [activeTab, setActiveTab] = useState<TabValue>("all");
  const [fetching, setFetching] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** 並行取得のうち最新のみで fetching を false にする */
  const fetchSeqRef = useRef(0);

  const fetchProposals = useCallback(
    async (
      status: TabValue,
      pageOffset: number,
      opts?: { signal?: AbortSignal }
    ) => {
      const signal = opts?.signal;
      if (!session?.access_token) {
        setFetching(false);
        return;
      }
      const seq = ++fetchSeqRef.current;
      setFetching(true);
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
            token: session.access_token,
            params,
            timeoutMs: PROPOSALS_LIST_TIMEOUT_MS,
            signal,
          }
        );
        if (seq !== fetchSeqRef.current) return;
        setProposals(res.items);
        setTotal(res.total);
        setHasMore(res.has_more);
      } catch (err) {
        if (signal?.aborted || isAbortError(err)) {
          return;
        }
        setError(proposalsListErrorMessage(err));
      } finally {
        if (seq === fetchSeqRef.current) {
          setFetching(false);
        }
      }
    },
    [session?.access_token]
  );

  useEffect(() => {
    if (authLoading) return;
    if (!session?.access_token) return;
    const ac = new AbortController();
    void fetchProposals(activeTab, offset, { signal: ac.signal });
    return () => ac.abort();
  }, [authLoading, activeTab, offset, fetchProposals, session?.access_token]);

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
          <ProposalDescriptionSection
            description={proposal.description}
            debugProposalId={proposal.id}
          />

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
        { token: session.access_token, method: "POST", body: {}, timeoutMs: 120_000 }
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
        <Button onClick={handleAnalyze} disabled={analyzing || authLoading}>
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
            {authLoading || fetching ? (
              <div className="flex flex-col items-center gap-2 py-12">
                <div className="flex items-center justify-center">
                  <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                  <span className="ml-2 text-sm text-muted-foreground">
                    {authLoading
                      ? "認証を確認しています…"
                      : "一覧を取得しています…"}
                  </span>
                </div>
                {!authLoading && fetching ? (
                  <p className="max-w-md text-center text-xs text-muted-foreground">
                    応答が遅い場合は Network タブで{" "}
                    <code className="rounded bg-muted px-1">/api/v1/proactive/proposals</code>{" "}
                    を確認してください（未起動のバックエンドへプロキシすると pending のままになることがあります）。
                  </p>
                ) : null}
              </div>
            ) : error ? (
              <div className="space-y-3 rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
                <p className="whitespace-pre-wrap">{error}</p>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="border-destructive/40 text-destructive hover:bg-destructive/10"
                  disabled={authLoading || fetching || !session?.access_token}
                  onClick={() => fetchProposals(activeTab, offset)}
                >
                  再試行
                </Button>
              </div>
            ) : proposals.length === 0 ? (
              <div className="py-12 text-center space-y-3">
                <p className="text-sm text-muted-foreground">
                  {tab === "all"
                    ? "まだAI提案はありません。「分析を実行」ボタンを押すと、登録済みのナレッジからリスクや改善案を自動で検出します。"
                    : "該当する提案はありません。"}
                </p>
                {tab === "all" && (
                  <Button variant="outline" size="sm" onClick={handleAnalyze} disabled={analyzing || authLoading}>
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
