"use client";

import { useEffect, useState, useCallback } from "react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

// ---------------------------------------------------------------------------
// 型定義
// ---------------------------------------------------------------------------

interface ScoreReasonItem {
  factor?: string;
  tier?: string;
  revenue?: number;
  profit?: number;
  revenue_segment?: string;
  [key: string]: unknown;
}

interface Lead {
  id: string;
  company_name: string;
  contact_name: string | null;
  contact_email: string | null;
  industry: string | null;
  employee_count: number | null;
  source: string;
  score: number;
  score_reasons: ScoreReasonItem[] | string[];
  status: "new" | "contacted" | "qualified" | "nurturing" | "unqualified";
  first_contact_at: string | null;
  last_activity_at: string | null;
  created_at: string;
}

interface GenerateProposalResponse {
  proposal_id: string;
  message: string;
}

// ---------------------------------------------------------------------------
// 定数
// ---------------------------------------------------------------------------

const PAGE_SIZE = 50;

const STATUS_FILTERS: { key: Lead["status"] | "all"; label: string }[] = [
  { key: "all", label: "すべて" },
  { key: "new", label: "新規" },
  { key: "contacted", label: "接触済み" },
  { key: "qualified", label: "有望" },
  { key: "nurturing", label: "育成中" },
];

const STATUS_BADGE: Record<
  Lead["status"],
  { label: string; className: string }
> = {
  new: { label: "新規", className: "bg-blue-100 text-blue-800" },
  contacted: { label: "接触済み", className: "bg-yellow-100 text-yellow-800" },
  qualified: { label: "有望", className: "bg-green-100 text-green-800" },
  nurturing: { label: "育成中", className: "bg-purple-100 text-purple-800" },
  unqualified: {
    label: "対象外",
    className: "bg-muted text-muted-foreground",
  },
};

const INDUSTRY_LABELS: Record<string, string> = {
  construction: "建設",
  manufacturing: "製造",
  dental: "歯科",
  restaurant: "飲食",
  realestate: "不動産",
  clinic: "医療",
  pharmacy: "薬局",
  beauty: "美容",
  auto_repair: "自動車整備",
  hotel: "ホテル",
  ecommerce: "EC",
  staffing: "人材派遣",
  architecture: "設計",
  logistics: "物流",
  nursing: "介護",
  professional: "士業",
  wholesale: "卸売",
};

// ---------------------------------------------------------------------------
// ユーティリティ
// ---------------------------------------------------------------------------

/** 円単位の数値を億円表示に変換（1000000000 → 10億円） */
function formatOkuYen(value: number): string {
  const oku = value / 100_000_000;
  if (oku >= 1) {
    return `${Math.round(oku)}億円`;
  }
  const man = value / 10_000;
  if (man >= 1) {
    return `${Math.round(man)}万円`;
  }
  return `${value.toLocaleString()}円`;
}

// ---------------------------------------------------------------------------
// スコアバッジ
// ---------------------------------------------------------------------------

function ScoreBadge({ score }: { score: number }) {
  if (score >= 70) {
    return (
      <Badge className="bg-green-100 text-green-800 text-[11px]">
        {score} 高
      </Badge>
    );
  }
  if (score >= 40) {
    return (
      <Badge className="bg-yellow-100 text-yellow-800 text-[11px]">
        {score} 中
      </Badge>
    );
  }
  return (
    <Badge className="bg-muted text-muted-foreground text-[11px]">
      {score} 低
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// スケルトン（テーブル用）
// ---------------------------------------------------------------------------

function TableSkeleton() {
  return (
    <div className="space-y-2">
      {[...Array(8)].map((_, i) => (
        <div
          key={i}
          className="h-12 w-full animate-pulse rounded bg-muted"
        />
      ))}
      <p className="text-center text-sm text-muted-foreground pt-2">
        リストを読み込んでいます...
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 企業詳細パネル（ダイアログ）
// ---------------------------------------------------------------------------

function LeadDetailDialog({
  lead,
  open,
  onClose,
  onGenerateProposal,
  onStatusChange,
  generating,
}: {
  lead: Lead | null;
  open: boolean;
  onClose: () => void;
  onGenerateProposal: (lead: Lead) => void;
  onStatusChange: (lead: Lead, status: Lead["status"]) => void;
  generating: boolean;
}) {
  const [showStatusMenu, setShowStatusMenu] = useState(false);

  if (!lead) return null;

  const statusInfo = STATUS_BADGE[lead.status] ?? {
    label: lead.status,
    className: "bg-muted text-muted-foreground",
  };

  // score_reasons を構造化データとして解析
  const reasons = lead.score_reasons as ScoreReasonItem[];
  const tierReason = reasons.find((r) => r.tier);
  const revenueReason = reasons.find(
    (r) => typeof r.revenue === "number" && r.revenue > 0
  );
  const profitReason = reasons.find(
    (r) => typeof r.profit === "number" && r.profit > 0
  );
  const segmentReason = reasons.find((r) => r.revenue_segment);

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg w-full mx-2">
        <DialogHeader>
          <DialogTitle className="text-xl font-bold leading-snug pr-8">
            {lead.company_name}
          </DialogTitle>
          <DialogDescription className="sr-only">
            {lead.company_name}の詳細情報
          </DialogDescription>
          <div className="flex flex-wrap gap-2 pt-1">
            <Badge className={statusInfo.className}>{statusInfo.label}</Badge>
            <ScoreBadge score={lead.score} />
          </div>
        </DialogHeader>

        <div className="space-y-4 text-sm">
          {/* 基本情報 */}
          <div className="rounded-md border p-3 space-y-2">
            <p className="text-xs font-semibold text-muted-foreground">
              基本情報
            </p>
            {lead.industry && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">業種</span>
                <span className="font-medium">
                  {INDUSTRY_LABELS[lead.industry] ?? lead.industry}
                </span>
              </div>
            )}
            {lead.employee_count != null && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">従業員数</span>
                <span className="font-medium">
                  {lead.employee_count.toLocaleString()}名
                </span>
              </div>
            )}
            {lead.contact_name && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">担当者</span>
                <span className="font-medium">{lead.contact_name}</span>
              </div>
            )}
            {lead.contact_email && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">メール</span>
                <span className="font-medium break-all">
                  {lead.contact_email}
                </span>
              </div>
            )}
          </div>

          {/* スコア根拠 */}
          {(tierReason || revenueReason || profitReason || segmentReason) && (
            <div className="rounded-md border p-3 space-y-2">
              <p className="text-xs font-semibold text-muted-foreground">
                スコア根拠
              </p>
              {tierReason?.tier && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">企業規模</span>
                  <span className="font-medium">{String(tierReason.tier)}</span>
                </div>
              )}
              {revenueReason?.revenue != null && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">売上</span>
                  <span className="font-medium">
                    {formatOkuYen(revenueReason.revenue as number)}
                  </span>
                </div>
              )}
              {profitReason?.profit != null && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">利益</span>
                  <span className="font-medium">
                    {formatOkuYen(profitReason.profit as number)}
                  </span>
                </div>
              )}
              {segmentReason?.revenue_segment && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">売上区分</span>
                  <span className="font-medium">
                    {String(segmentReason.revenue_segment)}
                  </span>
                </div>
              )}
            </div>
          )}
        </div>

        {/* アクション */}
        <div className="flex flex-col gap-2 sm:flex-row sm:justify-end pt-2">
          {/* ステータス変更 */}
          <div className="relative">
            <Button
              variant="outline"
              className="w-full sm:w-auto"
              onClick={() => setShowStatusMenu((v) => !v)}
            >
              ステータスを変更する
            </Button>
            {showStatusMenu && (
              <div className="absolute bottom-full mb-1 right-0 z-20 w-44 rounded-md border bg-popover shadow-md">
                {STATUS_FILTERS.filter((s) => s.key !== "all").map((s) => (
                  <button
                    key={s.key}
                    className="w-full px-3 py-2 text-sm text-left hover:bg-muted transition-colors"
                    onClick={() => {
                      onStatusChange(lead, s.key as Lead["status"]);
                      setShowStatusMenu(false);
                    }}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* 提案書を作成 */}
          <Button
            className="w-full sm:w-auto"
            disabled={generating}
            onClick={() => onGenerateProposal(lead)}
          >
            {generating ? (
              <>
                <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                作成中...
              </>
            ) : (
              "提案書を作成する"
            )}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// ページ
// ---------------------------------------------------------------------------

export default function LeadsPage() {
  const { session } = useAuth();

  // データ
  const [leads, setLeads] = useState<Lead[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // フィルタ・ページネーション
  const [statusFilter, setStatusFilter] = useState<Lead["status"] | "all">(
    "all"
  );
  const [page, setPage] = useState(0); // 0-indexed

  // 詳細パネル
  const [selectedLead, setSelectedLead] = useState<Lead | null>(null);

  // アクション
  const [generatingId, setGeneratingId] = useState<string | null>(null);
  const [successLead, setSuccessLead] = useState<Lead | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const fetchLeads = useCallback(async () => {
    if (!session?.access_token) return;
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = {
        limit: String(PAGE_SIZE),
        offset: String(page * PAGE_SIZE),
      };
      if (statusFilter !== "all") {
        params.status = statusFilter;
      }
      const res = await apiFetch<{ items: Lead[]; total: number }>(
        "/sales/leads",
        {
          token: session.access_token,
          params,
        }
      );
      setLeads(res.items);
      setTotal(res.total);
    } catch {
      setError(
        "リストの取得に失敗しました。しばらく経ってから再度お試しください。"
      );
    } finally {
      setLoading(false);
    }
  }, [session?.access_token, statusFilter, page]);

  useEffect(() => {
    fetchLeads();
  }, [fetchLeads]);

  // フィルタ変更時はページをリセット
  function handleStatusFilter(key: Lead["status"] | "all") {
    setStatusFilter(key);
    setPage(0);
  }

  async function handleGenerateProposal(lead: Lead) {
    if (!session?.access_token) return;
    setGeneratingId(lead.id);
    try {
      const res = await apiFetch<GenerateProposalResponse>(
        "/sales/proposals/generate",
        {
          token: session.access_token,
          method: "POST",
          body: { lead_id: lead.id },
        }
      );
      setSelectedLead(null);
      setSuccessLead(lead);
      setSuccessMessage(res.message || "提案書の生成を開始しました。");
    } catch {
      setError(
        "提案書の作成に失敗しました。しばらく経ってから再度お試しください。"
      );
    } finally {
      setGeneratingId(null);
    }
  }

  async function handleStatusChange(
    lead: Lead,
    newStatus: Lead["status"]
  ) {
    if (!session?.access_token) return;
    try {
      await apiFetch(`/sales/leads/${lead.id}`, {
        token: session.access_token,
        method: "PATCH",
        body: { status: newStatus, version: 1 },
      });
      // 楽観的更新
      setLeads((prev) =>
        prev.map((l) =>
          l.id === lead.id ? { ...l, status: newStatus } : l
        )
      );
      if (selectedLead?.id === lead.id) {
        setSelectedLead({ ...lead, status: newStatus });
      }
    } catch {
      setError(
        "ステータスの変更に失敗しました。しばらく経ってから再度お試しください。"
      );
    }
  }

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const hasPrev = page > 0;
  const hasNext = page < totalPages - 1;

  // モバイル用カードリスト
  function MobileLeadCard({ lead }: { lead: Lead }) {
    const statusInfo = STATUS_BADGE[lead.status] ?? {
      label: lead.status,
      className: "bg-muted text-muted-foreground",
    };
    return (
      <button
        className="w-full text-left rounded-lg border p-3 space-y-1 hover:bg-muted/50 transition-colors"
        onClick={() => setSelectedLead(lead)}
      >
        <div className="flex items-start justify-between gap-2">
          <span className="text-sm font-medium leading-snug">
            {lead.company_name}
          </span>
          <ScoreBadge score={lead.score} />
        </div>
        <div className="flex flex-wrap gap-1 items-center">
          {lead.industry && (
            <span className="text-xs text-muted-foreground">
              {INDUSTRY_LABELS[lead.industry] ?? lead.industry}
            </span>
          )}
          {lead.employee_count != null && (
            <span className="text-xs text-muted-foreground">
              · {lead.employee_count.toLocaleString()}名
            </span>
          )}
        </div>
        <Badge className={`${statusInfo.className} text-[11px]`}>
          {statusInfo.label}
        </Badge>
      </button>
    );
  }

  return (
    <div className="mx-auto max-w-7xl space-y-4">
      {/* ヘッダー */}
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold">見込み客リスト</h1>
          <p className="text-sm text-muted-foreground">
            スコア順に表示しています。企業名をタップすると詳細を確認できます。
          </p>
        </div>
        {!loading && (
          <div className="text-sm text-muted-foreground">
            全 {total.toLocaleString()} 件
          </div>
        )}
      </div>

      {/* エラー */}
      {error && (
        <div className="rounded-md border border-destructive/50 bg-destructive/5 p-3">
          <p className="text-sm text-destructive">{error}</p>
        </div>
      )}

      {/* ステータスフィルタ */}
      <div className="flex flex-wrap gap-2">
        {STATUS_FILTERS.map((s) => (
          <Button
            key={s.key}
            size="sm"
            variant={statusFilter === s.key ? "default" : "outline"}
            onClick={() => handleStatusFilter(s.key)}
            className="h-8 text-xs"
          >
            {s.label}
          </Button>
        ))}
      </div>

      {/* テーブル（PC） / カードリスト（モバイル） */}
      {loading ? (
        <TableSkeleton />
      ) : leads.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
            <p className="text-sm text-muted-foreground">
              該当する見込み客が見つかりませんでした。
            </p>
            <p className="text-xs text-muted-foreground">
              フィルタを変更するか、アウトリーチ機能で新たな見込み客を取得してください。
            </p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => handleStatusFilter("all")}
            >
              すべて表示する
            </Button>
          </CardContent>
        </Card>
      ) : (
        <>
          {/* PC テーブル */}
          <div className="hidden sm:block rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[280px]">企業名</TableHead>
                  <TableHead>業種</TableHead>
                  <TableHead className="text-right">従業員数</TableHead>
                  <TableHead className="text-right">スコア</TableHead>
                  <TableHead>ステータス</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {leads.map((lead) => {
                  const statusInfo = STATUS_BADGE[lead.status] ?? {
                    label: lead.status,
                    className: "bg-muted text-muted-foreground",
                  };
                  return (
                    <TableRow
                      key={lead.id}
                      className="cursor-pointer"
                      onClick={() => setSelectedLead(lead)}
                    >
                      <TableCell className="font-medium whitespace-normal">
                        {lead.company_name}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {lead.industry
                          ? (INDUSTRY_LABELS[lead.industry] ?? lead.industry)
                          : "—"}
                      </TableCell>
                      <TableCell className="text-right text-muted-foreground">
                        {lead.employee_count != null
                          ? `${lead.employee_count.toLocaleString()}名`
                          : "—"}
                      </TableCell>
                      <TableCell className="text-right">
                        <ScoreBadge score={lead.score} />
                      </TableCell>
                      <TableCell>
                        <Badge
                          className={`${statusInfo.className} text-[11px]`}
                        >
                          {statusInfo.label}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>

          {/* モバイル カードリスト */}
          <div className="flex flex-col gap-2 sm:hidden">
            {leads.map((lead) => (
              <MobileLeadCard key={lead.id} lead={lead} />
            ))}
          </div>

          {/* ページネーション */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between gap-2 pt-2">
              <Button
                variant="outline"
                size="sm"
                disabled={!hasPrev}
                onClick={() => setPage((p) => p - 1)}
                className="h-9 min-w-[80px]"
              >
                前へ
              </Button>
              <span className="text-sm text-muted-foreground">
                {page + 1} / {totalPages} ページ
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={!hasNext}
                onClick={() => setPage((p) => p + 1)}
                className="h-9 min-w-[80px]"
              >
                次へ
              </Button>
            </div>
          )}
        </>
      )}

      {/* 企業詳細パネル */}
      <LeadDetailDialog
        lead={selectedLead}
        open={selectedLead !== null}
        onClose={() => setSelectedLead(null)}
        onGenerateProposal={handleGenerateProposal}
        onStatusChange={handleStatusChange}
        generating={
          selectedLead !== null && generatingId === selectedLead.id
        }
      />

      {/* 提案書生成完了ダイアログ */}
      <Dialog
        open={successLead !== null}
        onOpenChange={(open) => {
          if (!open) {
            setSuccessLead(null);
            setSuccessMessage(null);
          }
        }}
      >
        <DialogContent className="sm:max-w-md w-full mx-2">
          <DialogHeader>
            <DialogTitle>提案書の作成を開始しました</DialogTitle>
            <DialogDescription>
              {successLead?.company_name} 向けの提案書を作成しています。
              {successMessage && ` ${successMessage}`}{" "}
              完了後、提案書一覧からご確認ください。
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
            <Button
              variant="outline"
              className="w-full sm:w-auto"
              onClick={() => {
                setSuccessLead(null);
                setSuccessMessage(null);
              }}
            >
              閉じる
            </Button>
            <Button
              className="w-full sm:w-auto"
              onClick={() => {
                setSuccessLead(null);
                setSuccessMessage(null);
                window.location.href = "/sales/proposals";
              }}
            >
              提案書一覧を見る
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
