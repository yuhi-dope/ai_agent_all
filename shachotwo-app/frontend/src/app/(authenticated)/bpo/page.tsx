"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
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
import { apiFetch } from "@/lib/api";

// ---------- パイプラインレジストリ ----------

interface Pipeline {
  key: string;
  industry: string;
  industryKey: string;
  name: string;
  displayName?: string; // simplified display name when the technical name is too jargon-heavy
  description: string;
  icon: string;
}

// industry キー → 日本語ラベルのマッピング（onboarding/status の industry 値に対応）
const INDUSTRY_KEY_MAP: Record<string, string> = {
  construction: "建設業",
  manufacturing: "製造業",
  dental: "歯科クリニック",
  restaurant: "飲食業",
  beauty: "美容業",
  logistics: "物流業",
  ecommerce: "EC・小売",
  nursing: "介護",
  staffing: "人材派遣",
  clinic: "医療クリニック",
  pharmacy: "薬局",
  hotel: "ホテル・宿泊",
  realestate: "不動産",
  auto_repair: "自動車修理",
  professional: "士業事務所",
  common: "共通",
};

// パイロット対象外（医療系・要配慮個人情報）の準備中セット
const COMING_SOON_KEYS = new Set([
  "dental/receipt_check",
  "clinic/medical_receipt",
  "pharmacy/dispensing_billing",
  "nursing/care_billing",
]);

// 業界キーごとの「はじめての方へ」見積ページパス
const INDUSTRY_FIRST_STEP_ROUTE: Record<string, { href: string; label: string }> = {
  construction: { href: "/bpo/estimation", label: "建設業の見積AIを試す" },
  manufacturing: { href: "/bpo/manufacturing", label: "製造業の見積AIを試す" },
  dental: { href: "/bpo/run?pipeline=dental/receipt_check", label: "レセプト点検AIを試す" },
  restaurant: { href: "/bpo/run?pipeline=restaurant/fl_cost", label: "FLコスト管理AIを試す" },
  beauty: { href: "/bpo/run?pipeline=beauty/booking_recall", label: "予約・再来促進AIを試す" },
  logistics: { href: "/bpo/run?pipeline=logistics/dispatch", label: "配車管理AIを試す" },
  ecommerce: { href: "/bpo/run?pipeline=ecommerce/product_listing", label: "商品登録AIを試す" },
  nursing: { href: "/bpo/run?pipeline=nursing/care_billing", label: "介護報酬請求AIを試す" },
  staffing: { href: "/bpo/run?pipeline=staffing/dispatch_contract", label: "派遣契約管理AIを試す" },
  clinic: { href: "/bpo/run?pipeline=clinic/medical_receipt", label: "医療レセプトAIを試す" },
  pharmacy: { href: "/bpo/run?pipeline=pharmacy/dispensing_billing", label: "調剤報酬請求AIを試す" },
  hotel: { href: "/bpo/run?pipeline=hotel/revenue_mgmt", label: "客室稼働率・料金最適化AIを試す" },
  realestate: { href: "/bpo/run?pipeline=realestate/rent_collection", label: "家賃回収管理AIを試す" },
  auto_repair: { href: "/bpo/run?pipeline=auto_repair/repair_quoting", label: "修理見積AIを試す" },
  professional: { href: "/bpo/run?pipeline=professional/deadline_mgmt", label: "期限管理AIを試す" },
};

const PIPELINE_REGISTRY: Pipeline[] = [
  {
    key: "construction/estimation",
    industry: "建設業",
    industryKey: "construction",
    name: "積算・見積",
    description: "図面・仕様書から工種・数量を自動積算し見積書を作成します",
    icon: "🏗️",
  },
  {
    key: "construction/billing",
    industry: "建設業",
    industryKey: "construction",
    name: "出来高・請求",
    description: "出来高管理と請求書の自動作成・送付を行います",
    icon: "📋",
  },
  {
    key: "construction/safety_docs",
    industry: "建設業",
    industryKey: "construction",
    name: "安全書類",
    description: "グリーンファイル等の安全書類を自動生成・管理します",
    icon: "🦺",
  },
  {
    key: "construction/construction_plan",
    industry: "建設業",
    industryKey: "construction",
    name: "施工計画書AI",
    description: "工事情報を入力するだけで、施工方針・安全管理計画・品質管理計画などを自動生成します",
    icon: "📐",
  },
  {
    key: "manufacturing/quoting",
    industry: "製造業",
    industryKey: "manufacturing",
    name: "見積作成",
    description: "図面・仕様から原価計算し見積書を自動作成します",
    icon: "🏭",
  },
  {
    key: "dental/receipt_check",
    industry: "歯科クリニック",
    industryKey: "dental",
    name: "レセプト点検",
    description: "診療報酬明細書の自動点検・エラー検出を行います",
    icon: "🦷",
  },
  {
    key: "restaurant/fl_cost",
    industry: "飲食業",
    industryKey: "restaurant",
    name: "FLコスト管理",
    description: "食材費・人件費の自動集計とFL比率の分析を行います",
    icon: "🍽️",
  },
  {
    key: "beauty/booking_recall",
    industry: "美容業",
    industryKey: "beauty",
    name: "予約・再来促進",
    description: "予約管理と顧客への再来促進メッセージ送付を自動化します",
    icon: "💇",
  },
  {
    key: "logistics/dispatch",
    industry: "物流業",
    industryKey: "logistics",
    name: "配車管理",
    description: "配送依頼から最適ルート計算と配車指示を自動化します",
    icon: "🚚",
  },
  {
    key: "ecommerce/product_listing",
    industry: "EC・小売",
    industryKey: "ecommerce",
    name: "商品登録",
    description: "商品情報から複数モールへの一括登録・更新を行います",
    icon: "🛒",
  },
  {
    key: "nursing/care_billing",
    industry: "介護",
    industryKey: "nursing",
    name: "介護報酬請求",
    description: "サービス実績から国保連請求データを自動生成します",
    icon: "🏥",
  },
  {
    key: "staffing/dispatch_contract",
    industry: "人材派遣",
    industryKey: "staffing",
    name: "派遣契約管理",
    description: "派遣契約書の作成・更新・期限管理を自動化します",
    icon: "👥",
  },
  {
    key: "clinic/medical_receipt",
    industry: "医療クリニック",
    industryKey: "clinic",
    name: "医療レセプト",
    description: "医療機関の診療報酬請求業務を自動化します",
    icon: "🩺",
  },
  {
    key: "pharmacy/dispensing_billing",
    industry: "薬局",
    industryKey: "pharmacy",
    name: "調剤報酬請求",
    description: "調剤実績から請求データを自動作成します",
    icon: "💊",
  },
  {
    key: "hotel/revenue_mgmt",
    industry: "ホテル・宿泊",
    industryKey: "hotel",
    name: "レベニューマネジメント",
    displayName: "客室稼働率・料金の最適化",
    description: "稼働率・ADR分析と最適料金の自動設定を行います",
    icon: "🏨",
  },
  {
    key: "realestate/rent_collection",
    industry: "不動産",
    industryKey: "realestate",
    name: "家賃回収管理",
    description: "家賃入金確認・督促・滞納管理を自動化します",
    icon: "🏢",
  },
  {
    key: "auto_repair/repair_quoting",
    industry: "自動車修理",
    industryKey: "auto_repair",
    name: "修理見積",
    description: "車両状態から修理項目を特定し見積書を自動作成します",
    icon: "🔧",
  },
  {
    key: "professional/deadline_mgmt",
    industry: "士業事務所",
    industryKey: "professional",
    name: "期限管理",
    description: "申告・届出の期限を一元管理し自動リマインドします",
    icon: "⚖️",
  },
  {
    key: "common/expense",
    industry: "共通",
    industryKey: "common",
    name: "経費精算",
    description: "領収書OCR・承認フロー・仕訳計上を自動化します",
    icon: "💴",
  },
  {
    key: "common/payroll",
    industry: "共通",
    industryKey: "common",
    name: "給与計算",
    description: "勤怠データから給与計算・明細発行を自動化します",
    icon: "💰",
  },
  {
    key: "common/attendance",
    industry: "共通",
    industryKey: "common",
    name: "勤怠管理",
    description: "打刻データの集計・残業管理・36協定チェックを行います",
    icon: "⏰",
  },
  {
    key: "common/contract",
    industry: "共通",
    industryKey: "common",
    name: "契約管理",
    description: "契約書の作成・電子署名・更新期限管理を自動化します",
    icon: "📝",
  },
];

// 業界ごとにグループ化（表示順を維持するためにキー出現順を保持）
function groupByIndustry(pipelines: Pipeline[]): Map<string, Pipeline[]> {
  const map = new Map<string, Pipeline[]>();
  for (const p of pipelines) {
    if (!map.has(p.industry)) map.set(p.industry, []);
    map.get(p.industry)!.push(p);
  }
  return map;
}

// ---------- 確度ラベル変換 ----------

function confidenceLabel(confidence: number): { label: string; isLow: boolean } {
  if (confidence >= 0.8) return { label: "確度：高", isLow: false };
  if (confidence >= 0.5) return { label: "確度：中", isLow: true };
  return { label: "参考情報", isLow: true };
}

// ---------- 承認待ちアイテム型 ----------

interface PendingApproval {
  id: string;
  pipeline_key: string;
  pipeline_label: string;
  created_at: string;
  summary: string;
  confidence: number;
  output_detail?: string;
}

interface PendingApprovalsResponse {
  count: number;
  items: PendingApproval[];
}

// ---------- 承認待ちカード ----------

interface PendingApprovalCardProps {
  item: PendingApproval;
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

// ---------- パイプライン専用ページのルートマップ ----------
// key → 専用ページのパス。未定義の場合は汎用 /bpo/run を使う。

const PIPELINE_ROUTES: Record<string, string> = {
  "construction/estimation": "/bpo/estimation",
  "construction/construction_plan": "/bpo/construction-plan",
  "manufacturing/quoting": "/bpo/manufacturing",
};

// ---------- パイプラインカード ----------

interface PipelineCardProps {
  pipeline: Pipeline;
}

function PipelineCard({ pipeline }: PipelineCardProps) {
  const isComingSoon = COMING_SOON_KEYS.has(pipeline.key);
  const dedicatedRoute = PIPELINE_ROUTES[pipeline.key];
  const href = dedicatedRoute ?? `/bpo/run?pipeline=${pipeline.key}`;
  const cardName = pipeline.displayName ?? pipeline.name;

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
              <p className="text-xs text-muted-foreground">{pipeline.industry}</p>
            </div>
          </div>
          {isComingSoon ? (
            <Badge variant="secondary" className="shrink-0 text-xs">準備中</Badge>
          ) : (
            <Badge variant="outline" className="shrink-0 text-xs">稼働中</Badge>
          )}
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

// ---------- onboarding レスポンス型 ----------

interface OnboardingStatus {
  industry: string | null;
  template_applied: boolean;
  knowledge_count: number;
  onboarding_progress: number;
  suggested_questions: string[];
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

export default function BPODashboardPage() {
  const { session } = useAuth();
  const [userIndustryKey, setUserIndustryKey] = useState<string | null>(null);
  const [executionCount, setExecutionCount] = useState<number>(0);

  // 承認待ち関連 state
  const [pendingApprovals, setPendingApprovals] = useState<PendingApproval[]>([]);
  const [pendingCount, setPendingCount] = useState(0);
  const [pendingLoading, setPendingLoading] = useState(true);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [approvalError, setApprovalError] = useState<string | null>(null);

  // 検索クエリ
  const [searchQuery, setSearchQuery] = useState("");

  // 成功メッセージを3秒後に自動消去
  function showSuccess(message: string) {
    setSuccessMessage(message);
    setTimeout(() => setSuccessMessage(null), 3000);
  }

  // 承認待ち一覧を取得
  const fetchPendingApprovals = useCallback(async () => {
    if (!session?.access_token) return;
    setPendingLoading(true);
    try {
      const data = await apiFetch<PendingApprovalsResponse>("/execution/pending-approvals", {
        token: session.access_token,
      });
      setPendingApprovals(data.items ?? []);
      setPendingCount(data.count ?? 0);
    } catch {
      // 取得失敗時は承認待ちセクションを非表示にする（ページ全体は壊さない）
      setPendingApprovals([]);
      setPendingCount(0);
    } finally {
      setPendingLoading(false);
    }
  }, [session?.access_token]);

  // 承認処理
  async function handleApprove(id: string) {
    setApprovalError(null);
    try {
      await apiFetch<unknown>(`/execution/${id}/approve`, {
        token: session?.access_token,
        method: "POST",
      });
      setPendingApprovals((prev) => prev.filter((item) => item.id !== id));
      setPendingCount((prev) => Math.max(0, prev - 1));
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
        body: { rejection_reason: reason },
      });
      setPendingApprovals((prev) => prev.filter((item) => item.id !== id));
      setPendingCount((prev) => Math.max(0, prev - 1));
      showSuccess("却下しました。");
    } catch {
      setApprovalError("却下に失敗しました。しばらく経ってからもう一度お試しください。");
    }
  }

  useEffect(() => {
    if (!session?.access_token) return;
    apiFetch<OnboardingStatus>("/onboarding/status", { token: session.access_token })
      .then((data) => {
        setUserIndustryKey(data.industry ?? null);
      })
      .catch(() => {
        // 取得失敗時は「あなたの業種」セクションなしで表示
      });

    // 過去の実行件数を取得してファーストステップ案内の表示判定に使う
    apiFetch<{ count: number }>("/execution/count", { token: session.access_token })
      .then((data) => setExecutionCount(data.count ?? 0))
      .catch(() => setExecutionCount(0));

    fetchPendingApprovals();
  }, [session, fetchPendingApprovals]);

  // ユーザーの業種に対応する日本語ラベルを解決
  const userIndustryLabel = userIndustryKey ? (INDUSTRY_KEY_MAP[userIndustryKey] ?? null) : null;

  // 「あなたの業種」セクション用パイプライン
  const recommendedPipelines = userIndustryKey
    ? PIPELINE_REGISTRY.filter((p) => p.industryKey === userIndustryKey)
    : [];

  // 残りの業種グループ（ユーザー業種を除外）
  const remainingPipelines = userIndustryKey
    ? PIPELINE_REGISTRY.filter((p) => p.industryKey !== userIndustryKey)
    : PIPELINE_REGISTRY;

  // クライアントサイド検索フィルター
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
  const filteredRemaining = remainingPipelines.filter(matchesSearch);
  const grouped = groupByIndustry(filteredRemaining);

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
          AIが書類作成・集計・確認などの繰り返し業務を自動でこなします。業種を選んで試してみましょう。
        </p>
      </div>

      {/* はじめての方へ案内カード（承認待ちセクションの上） */}
      {userIndustryKey && (
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
      {filteredRecommended.length > 0 && userIndustryLabel && (
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

      {/* 検索結果ゼロ */}
      {normalizedQuery && filteredRecommended.length === 0 && filteredRemaining.length === 0 && (
        <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
          「{searchQuery}」に一致する業務が見つかりませんでした。
        </div>
      )}

      {/* 業界別カード一覧 */}
      {[...grouped.entries()].map(([industry, pipelines]) => (
        <div key={industry} className="space-y-3">
          <h2 className="text-base font-semibold text-muted-foreground border-b pb-1">
            {industry}
          </h2>
          <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
            {pipelines.map((pipeline) => (
              <PipelineCard key={pipeline.key} pipeline={pipeline} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
