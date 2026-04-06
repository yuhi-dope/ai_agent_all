"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/hooks/use-auth";
import { useOnboardingIndustry } from "@/hooks/use-onboarding-industry";
import type { PendingApprovalItem } from "@/hooks/use-pending-approvals";
import { usePendingApprovals } from "@/hooks/use-pending-approvals";
import { apiFetch } from "@/lib/api";
import { AccuracyBadge } from "@/components/accuracy-indicator";
import { getBpoIndustryDisplayLabel } from "@/lib/bpo-industry-labels";
import {
  MVP_PIPELINES,
  PIPELINE_CONFIDENCE,
  bpoDashboardTabFromSearchParams,
  getPipelineCardHref,
  industryPipelineBadgeClassName,
  type BpoDashboardTab,
  type BpoPipelineDefinition,
} from "@/lib/bpo-pipeline-catalog";

// ---------- パイプラインレジストリ ----------

type Pipeline = BpoPipelineDefinition;

// パイロット対象外（医療系・要配慮個人情報）の準備中セット
const COMING_SOON_KEYS = new Set<string>();

// 業界キーごとの「はじめての方へ」見積ページパス
const INDUSTRY_FIRST_STEP_ROUTE: Record<string, { href: string; label: string }> = {
  construction: { href: "/bpo/estimation", label: "建設業の見積AIを試す" },
  manufacturing: { href: "/bpo/manufacturing", label: "製造業の見積AIを試す" },
};

const PIPELINE_REGISTRY: Pipeline[] = MVP_PIPELINES;

// ---------- 確度ラベル変換 ----------

function confidenceLabel(confidence: number): { label: string; isLow: boolean } {
  if (confidence >= 0.8) return { label: "確度：高", isLow: false };
  if (confidence >= 0.5) return { label: "確度：中", isLow: true };
  return { label: "参考情報", isLow: true };
}

// ---------- 承認待ちカード ----------

interface PendingApprovalCardProps {
  item: PendingApprovalItem;
  onApprove: (id: string) => Promise<void>;
  onReject: (id: string, reason: string) => Promise<void>;
}

function PendingApprovalCard({ item, onApprove, onReject }: PendingApprovalCardProps) {
  const [isDetailOpen, setIsDetailOpen] = useState(false);
  const [isApproveConfirmOpen, setIsApproveConfirmOpen] = useState(false);
  const [isRejectOpen, setIsRejectOpen] = useState(false);
  const [rejectionReason, setRejectionReason] = useState("");
  const [isApproving, setIsApproving] = useState(false);
  const [isRejecting, setIsRejecting] = useState(false);

  const { label, isLow } = confidenceLabel(item.confidence);
  const isVeryLowConfidence = item.confidence < 0.6;

  const formattedDate = (() => {
    try {
      const d = new Date(item.created_at);
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    } catch {
      return item.created_at;
    }
  })();

  async function handleApprove() {
    setIsApproving(true);
    try {
      await onApprove(item.id);
      setIsApproveConfirmOpen(false);
    } finally {
      setIsApproving(false);
    }
  }

  async function handleRejectSubmit() {
    if (!rejectionReason.trim()) return;
    setIsRejecting(true);
    try {
      await onReject(item.id, rejectionReason.trim());
      setIsRejectOpen(false);
      setRejectionReason("");
    } finally {
      setIsRejecting(false);
    }
  }

  return (
    <>
      <div className="flex flex-col gap-3 rounded-md border bg-background p-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-col gap-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary" className="shrink-0 text-xs">
              {item.pipeline_label}
            </Badge>
            <span
              className={`text-xs font-medium ${
                isLow ? "text-amber-600" : "text-muted-foreground"
              }`}
            >
              {isLow && <span className="mr-1" aria-hidden="true">⚠</span>}
              {label}
            </span>
            <span className="text-xs text-muted-foreground">{formattedDate}</span>
          </div>
          <p className="text-sm font-medium leading-snug truncate">{item.summary}</p>
          {isVeryLowConfidence && (
            <p className="text-xs text-amber-700 mt-0.5">
              AIが自動判定できなかった箇所があります。内容をご確認ください。
            </p>
          )}
        </div>
        <div className="flex flex-wrap gap-2 shrink-0">
          {/* 「内容を確認する」を primary、先頭に配置 */}
          <Button
            size="sm"
            onClick={() => setIsDetailOpen(true)}
          >
            内容を確認する
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={isApproving}
            onClick={() => setIsApproveConfirmOpen(true)}
          >
            承認して送付する
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="text-destructive hover:text-destructive"
            onClick={() => setIsRejectOpen(true)}
          >
            却下する
          </Button>
        </div>
      </div>

      {/* 内容確認ダイアログ */}
      <Dialog open={isDetailOpen} onOpenChange={setIsDetailOpen}>
        <DialogContent className="sm:max-w-lg w-full mx-2">
          <DialogHeader>
            <DialogTitle>{item.pipeline_label}の実行結果</DialogTitle>
            <DialogDescription>{item.summary}</DialogDescription>
          </DialogHeader>
          <div className="rounded-md border bg-muted/40 p-4 text-sm whitespace-pre-wrap leading-relaxed max-h-80 overflow-y-auto">
            {item.output_detail ?? "詳細データを読み込めませんでした。"}
          </div>
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => setIsDetailOpen(false)}>
              閉じる
            </Button>
            <Button
              onClick={() => {
                setIsDetailOpen(false);
                setIsApproveConfirmOpen(true);
              }}
            >
              承認して送付する
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 承認確認ダイアログ */}
      <Dialog open={isApproveConfirmOpen} onOpenChange={setIsApproveConfirmOpen}>
        <DialogContent className="sm:max-w-md w-full mx-2">
          <DialogHeader>
            <DialogTitle>この実行結果を承認しますか？</DialogTitle>
            <DialogDescription>
              「{item.summary}」を承認して送付します。内容をご確認のうえ実行してください。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2">
            <Button
              variant="outline"
              onClick={() => setIsApproveConfirmOpen(false)}
            >
              キャンセル
            </Button>
            <Button
              disabled={isApproving}
              onClick={handleApprove}
            >
              {isApproving ? (
                <>
                  <span className="mr-2 h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  送付中...
                </>
              ) : (
                "承認して送付する"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 却下理由入力ダイアログ */}
      <Dialog open={isRejectOpen} onOpenChange={setIsRejectOpen}>
        <DialogContent className="sm:max-w-md w-full mx-2">
          <DialogHeader>
            <DialogTitle>この実行結果を却下しますか？</DialogTitle>
            <DialogDescription>
              「{item.summary}」を却下します。却下理由を入力してください。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor={`rejection-reason-${item.id}`}>却下理由</Label>
            <Textarea
              id={`rejection-reason-${item.id}`}
              placeholder="例: 金額に誤りがあるため修正が必要"
              value={rejectionReason}
              onChange={(e) => setRejectionReason(e.target.value)}
              rows={3}
            />
          </div>
          <DialogFooter className="gap-2">
            <Button
              variant="outline"
              onClick={() => {
                setIsRejectOpen(false);
                setRejectionReason("");
              }}
            >
              キャンセル
            </Button>
            <Button
              variant="destructive"
              disabled={!rejectionReason.trim() || isRejecting}
              onClick={handleRejectSubmit}
            >
              {isRejecting ? (
                <>
                  <span className="mr-2 h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  却下中...
                </>
              ) : (
                "却下する"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

// ---------- パイプラインカード ----------

interface PipelineCardProps {
  pipeline: Pipeline;
}

function PipelineCard({ pipeline }: PipelineCardProps) {
  const isComingSoon = COMING_SOON_KEYS.has(pipeline.key);
  const href = getPipelineCardHref(pipeline.key);
  const cardName = pipeline.displayName ?? pipeline.name;
  const confidence = PIPELINE_CONFIDENCE[pipeline.key];

  return (
    <Card className={`flex flex-col${isComingSoon ? " opacity-60" : ""}`}>
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="text-2xl">{pipeline.icon}</span>
            <div>
              <CardTitle className="text-sm font-semibold leading-tight">
                {cardName}
              </CardTitle>
              <Badge
                variant="outline"
                className={`mt-0.5 w-fit px-1.5 py-0 text-[10px] font-medium ${industryPipelineBadgeClassName(pipeline.industryKey)}`}
              >
                {pipeline.industry}
              </Badge>
            </div>
          </div>
          <div className="flex flex-col items-end gap-1 shrink-0">
            {isComingSoon ? (
              <Badge variant="secondary" className="text-xs">準備中</Badge>
            ) : pipeline.maturity === "beta" ? (
              <Badge variant="secondary" className="text-xs">ベータ</Badge>
            ) : (
              <Badge variant="outline" className="text-xs">稼働中</Badge>
            )}
            {!isComingSoon && confidence !== undefined && (
              <AccuracyBadge confidence={confidence} />
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col justify-between gap-3">
        <CardDescription className="text-xs leading-relaxed">
          {pipeline.description}
        </CardDescription>
        {isComingSoon ? (
          <Button size="sm" className="w-full" disabled>
            準備中
          </Button>
        ) : (
          <Link href={href}>
            <Button size="sm" className="w-full">
              この自動化を実行する
            </Button>
          </Link>
        )}
      </CardContent>
    </Card>
  );
}

// ---------- ファーストステップ案内カード ----------

interface FirstStepBannerProps {
  industryKey: string;
  executionCount: number;
}

function FirstStepBanner({ industryKey, executionCount }: FirstStepBannerProps) {
  const route = INDUSTRY_FIRST_STEP_ROUTE[industryKey];
  if (!route || executionCount >= 3) return null;

  return (
    <div className="rounded-lg border-2 border-blue-200 bg-blue-50 p-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
      <div className="flex items-start gap-3">
        <span className="text-2xl shrink-0" aria-hidden="true">💡</span>
        <div>
          <p className="text-sm font-semibold text-blue-900">はじめての方へ</p>
          <p className="text-sm text-blue-800 mt-0.5">
            まずは「見積AI」をお試しください。数分で自動見積書が完成します。
          </p>
        </div>
      </div>
      <Link href={route.href} className="shrink-0">
        <Button size="sm" className="w-full sm:w-auto bg-blue-600 hover:bg-blue-700 text-white">
          {route.label}
        </Button>
      </Link>
    </div>
  );
}

// ---------- Page ----------

export function BPODashboardPageInner() {
  const { session } = useAuth();
  const {
    items: pendingApprovals,
    count: pendingCount,
    loading: pendingLoading,
    removeItemLocally,
  } = usePendingApprovals();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const {
    industry: userIndustryKey,
    isLoading: industryLoading,
    fetchFailed: industryFetchFailed,
  } = useOnboardingIndustry();
  const [executionCount, setExecutionCount] = useState<number>(0);

  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [approvalError, setApprovalError] = useState<string | null>(null);

  // 検索クエリ
  const [searchQuery, setSearchQuery] = useState("");

  // 成功メッセージを3秒後に自動消去
  function showSuccess(message: string) {
    setSuccessMessage(message);
    setTimeout(() => setSuccessMessage(null), 3000);
  }

  // 承認処理
  async function handleApprove(id: string) {
    setApprovalError(null);
    try {
      await apiFetch<unknown>(`/execution/${id}/approve`, {
        token: session?.access_token,
        method: "POST",
      });
      removeItemLocally(id);
      showSuccess("承認しました。送付処理を開始しました。");
    } catch {
      setApprovalError("承認に失敗しました。しばらく経ってからもう一度お試しください。");
    }
  }

  // 却下処理
  async function handleReject(id: string, reason: string) {
    setApprovalError(null);
    try {
      await apiFetch<unknown>(`/execution/${id}/reject`, {
        token: session?.access_token,
        method: "POST",
        body: { reason },
      });
      removeItemLocally(id);
      showSuccess("却下しました。");
    } catch {
      setApprovalError("却下に失敗しました。しばらく経ってからもう一度お試しください。");
    }
  }

  useEffect(() => {
    if (!session?.access_token) return;

    apiFetch<{ count: number }>("/execution/count", { token: session.access_token })
      .then((data) => setExecutionCount(data.count ?? 0))
      .catch(() => setExecutionCount(0));
  }, [session?.access_token]);

  // ユーザーの業種に対応する日本語ラベルを解決
  const userIndustryLabel = getBpoIndustryDisplayLabel(userIndustryKey);

  const recommendedPipelines = userIndustryKey
    ? PIPELINE_REGISTRY.filter((p) => p.industryKey === userIndustryKey)
    : [];

  const commonPipelines = PIPELINE_REGISTRY.filter((p) => p.industryKey === "common");

  const normalizedQuery = searchQuery.trim().toLowerCase();
  function matchesSearch(p: Pipeline): boolean {
    if (!normalizedQuery) return true;
    const cardName = (p.displayName ?? p.name).toLowerCase();
    return (
      cardName.includes(normalizedQuery) ||
      p.description.toLowerCase().includes(normalizedQuery) ||
      p.industry.toLowerCase().includes(normalizedQuery)
    );
  }

  const filteredRecommended = recommendedPipelines.filter(matchesSearch);
  const filteredCommon = commonPipelines.filter(matchesSearch);

  const dashboardTab = bpoDashboardTabFromSearchParams(searchParams);
  const effectiveTab: BpoDashboardTab =
    dashboardTab === "industry" && !userIndustryKey && !industryLoading
      ? "all"
      : dashboardTab;

  useEffect(() => {
    if (industryLoading || userIndustryKey) return;
    if (searchParams.get("tab") === "industry") {
      const params = new URLSearchParams(searchParams.toString());
      params.delete("tab");
      const q = params.toString();
      router.replace(q ? `${pathname}?${q}` : pathname, { scroll: false });
    }
  }, [industryLoading, userIndustryKey, searchParams, pathname, router]);

  const showIndustrySection =
    Boolean(userIndustryLabel) &&
    (effectiveTab === "all" || effectiveTab === "industry") &&
    filteredRecommended.length > 0;

  const showCommonSection =
    (effectiveTab === "all" || effectiveTab === "common") &&
    filteredCommon.length > 0;

  const noSearchResults =
    Boolean(normalizedQuery) &&
    ((effectiveTab === "all" &&
      filteredRecommended.length === 0 &&
      filteredCommon.length === 0) ||
      (effectiveTab === "industry" && filteredRecommended.length === 0) ||
      (effectiveTab === "common" && filteredCommon.length === 0));

  return (
    <div className="space-y-8">
      {/* ヘッダー */}
      <div>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-bold">業務自動化</h1>
          {!pendingLoading && pendingCount > 0 && (
            <Badge variant="destructive" className="text-sm px-2 py-0.5">
              確認待ち: {pendingCount}件
            </Badge>
          )}
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          AIが書類作成・集計・確認などの繰り返し業務を自動でこなします。自社の業種向けと全社共通の自動化から選べます。
        </p>
      </div>

      {!industryLoading && industryFetchFailed && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          会社の業種情報を読み込めませんでした。しばらくしてから再度お試しください。
        </div>
      )}

      {!industryLoading && !userIndustryKey && !industryFetchFailed && (
        <div className="rounded-lg border border-dashed border-muted-foreground/40 bg-muted/30 p-4 text-sm">
          <p className="font-medium text-foreground">業種が未設定です</p>
          <p className="mt-1 text-muted-foreground">
            初期セットアップで業種を選ぶと、その業種向けの自動化がここに表示されます。
          </p>
          <Link href="/onboarding" className="mt-3 inline-block">
            <Button variant="secondary" size="sm">
              初期セットアップへ
            </Button>
          </Link>
        </div>
      )}

      {/* はじめての方へ案内カード（承認待ちセクションの上） */}
      {userIndustryKey && !industryLoading && (
        <FirstStepBanner industryKey={userIndustryKey} executionCount={executionCount} />
      )}

      {/* 成功バナー */}
      {successMessage && (
        <div className="rounded-md bg-green-50 border border-green-200 px-4 py-3 text-sm text-green-700">
          {successMessage}
        </div>
      )}

      {/* エラーバナー */}
      {approvalError && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          {approvalError}
        </div>
      )}

      {/* 確認が必要な実行結果セクション */}
      {!pendingLoading && pendingApprovals.length > 0 && (
        <div className="rounded-lg border-2 border-amber-200 bg-amber-50 p-4 space-y-3">
          <div className="flex items-center gap-2">
            <span className="text-lg" aria-hidden="true">⚠</span>
            <h2 className="text-base font-semibold text-amber-800">
              確認が必要な実行結果があります
            </h2>
          </div>
          <p className="text-xs text-amber-700">
            以下の自動化結果を確認し、内容に問題がなければ「内容を確認する」から詳細を確認のうえ送付してください。
          </p>
          <div className="space-y-2">
            {pendingApprovals.map((item) => (
              <PendingApprovalCard
                key={item.id}
                item={item}
                onApprove={handleApprove}
                onReject={handleReject}
              />
            ))}
          </div>
        </div>
      )}

      {/* 検索バー */}
      <div className="relative">
        <Input
          type="search"
          placeholder="業務を検索..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="pl-9"
        />
        <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground text-sm" aria-hidden="true">
          🔍
        </span>
      </div>

      {/* あなたの業種（おすすめ）セクション */}
      {showIndustrySection && userIndustryLabel && (
        <div className="rounded-lg border-2 border-primary/30 bg-primary/5 p-4 space-y-3">
          <div className="flex items-center gap-2">
            <Badge className="bg-primary text-primary-foreground">あなたの業種</Badge>
            <h2 className="text-base font-semibold">{userIndustryLabel}のおすすめ</h2>
          </div>
          <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
            {filteredRecommended.map((pipeline) => (
              <PipelineCard key={pipeline.key} pipeline={pipeline} />
            ))}
          </div>
        </div>
      )}

      {/* 全業種共通 */}
      {showCommonSection && (
        <div id="common-bpo" className="scroll-mt-4 space-y-3">
          <h2 className="text-base font-semibold text-muted-foreground border-b pb-1">
            全業種共通
          </h2>
          <p className="text-xs text-muted-foreground">
            バックオフィスなど、どの業種でも利用できる自動化です。
          </p>
          <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
            {filteredCommon.map((pipeline) => (
              <PipelineCard key={pipeline.key} pipeline={pipeline} />
            ))}
          </div>
        </div>
      )}

      {/* 検索結果ゼロ */}
      {noSearchResults && (
        <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
          「{searchQuery}」に一致する業務が見つかりませんでした。
        </div>
      )}
    </div>
  );
}

