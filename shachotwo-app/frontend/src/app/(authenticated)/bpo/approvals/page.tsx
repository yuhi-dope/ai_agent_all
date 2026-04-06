"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useAuth } from "@/hooks/use-auth";
import type { PendingApprovalItem } from "@/hooks/use-pending-approvals";
import { usePendingApprovals } from "@/hooks/use-pending-approvals";
import { apiFetch } from "@/lib/api";

// ---------- 型定義 ----------

type ApprovalItem = PendingApprovalItem;

type StatusFilter = "pending" | "approved" | "rejected" | "all";

// ---------- ヘルパー ----------

function confidenceLabel(confidence: number): {
  label: string;
  className: string;
} {
  if (confidence >= 0.8)
    return { label: "確度：高", className: "bg-green-100 text-green-800" };
  if (confidence >= 0.5)
    return { label: "確度：中", className: "bg-yellow-100 text-yellow-800" };
  return { label: "参考情報", className: "bg-gray-100 text-gray-700" };
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    return `${y}/${m}/${day} ${hh}:${mm}`;
  } catch {
    return iso;
  }
}

// ---------- スケルトン ----------

function SkeletonRow() {
  return (
    <div className="flex flex-col gap-2 rounded-lg border p-4 sm:flex-row sm:items-center sm:justify-between animate-pulse">
      <div className="space-y-2 flex-1">
        <div className="h-4 w-32 rounded bg-muted" />
        <div className="h-3 w-64 rounded bg-muted" />
        <div className="h-3 w-24 rounded bg-muted" />
      </div>
      <div className="flex gap-2">
        <div className="h-8 w-20 rounded bg-muted" />
        <div className="h-8 w-16 rounded bg-muted" />
        <div className="h-8 w-16 rounded bg-muted" />
      </div>
    </div>
  );
}

// ---------- 承認行コンポーネント ----------

interface ApprovalRowProps {
  item: ApprovalItem;
  onApprove: (id: string) => Promise<void>;
  onReject: (id: string, reason: string) => Promise<void>;
}

function ApprovalRow({ item, onApprove, onReject }: ApprovalRowProps) {
  const router = useRouter();
  const [isRejectOpen, setIsRejectOpen] = useState(false);
  const [rejectionReason, setRejectionReason] = useState("");
  const [isApproving, setIsApproving] = useState(false);
  const [isRejecting, setIsRejecting] = useState(false);

  const { label, className } = confidenceLabel(item.confidence);

  async function handleApprove() {
    setIsApproving(true);
    try {
      await onApprove(item.id);
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
      <div className="flex flex-col gap-3 rounded-lg border bg-card p-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-col gap-1 min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary" className="shrink-0 text-xs">
              {item.pipeline_label}
            </Badge>
            <Badge className={`shrink-0 text-xs ${className}`}>
              {label}
            </Badge>
            <span className="text-xs text-muted-foreground">
              {formatDate(item.created_at)}
            </span>
          </div>
          <p className="text-sm font-medium leading-snug line-clamp-2">
            {item.summary}
          </p>
        </div>
        <div className="flex flex-wrap gap-2 shrink-0">
          <Button
            size="sm"
            variant="outline"
            onClick={() => router.push(`/bpo/approvals/${item.id}`)}
          >
            詳細を見る
          </Button>
          <Button
            size="sm"
            disabled={isApproving}
            onClick={handleApprove}
          >
            {isApproving ? (
              <>
                <span className="mr-1.5 h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
                承認中...
              </>
            ) : (
              "この提案を承認する"
            )}
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

      {/* 却下理由ダイアログ */}
      <Dialog open={isRejectOpen} onOpenChange={setIsRejectOpen}>
        <DialogContent className="sm:max-w-md w-full mx-2">
          <DialogHeader>
            <DialogTitle>この実行結果を却下しますか？</DialogTitle>
            <DialogDescription>
              「{item.summary}」を却下します。却下理由を入力してください。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor={`reject-reason-${item.id}`}>却下理由</Label>
            <Textarea
              id={`reject-reason-${item.id}`}
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
                  <span className="mr-1.5 h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
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

// ---------- ページ ----------

export default function BPOApprovalsPage() {
  const { session } = useAuth();
  const {
    items: pendingItems,
    count: pendingTotalCount,
    loading: pendingLoading,
    removeItemLocally,
  } = usePendingApprovals();
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("pending");
  const [searchQuery, setSearchQuery] = useState("");

  const loading = statusFilter === "pending" ? pendingLoading : false;
  const items = statusFilter === "pending" ? pendingItems : [];
  const totalCount = statusFilter === "pending" ? pendingTotalCount : 0;

  function showSuccess(msg: string) {
    setSuccessMessage(msg);
    setTimeout(() => setSuccessMessage(null), 3000);
  }

  async function handleApprove(id: string) {
    setError(null);
    try {
      await apiFetch<unknown>(`/approvals/${id}/approve`, {
        token: session?.access_token,
        method: "PATCH",
        body: {},
      });
      removeItemLocally(id);
      showSuccess("承認しました。");
    } catch {
      setError(
        "承認に失敗しました。しばらく経ってからもう一度お試しください。"
      );
    }
  }

  async function handleReject(id: string, reason: string) {
    setError(null);
    try {
      await apiFetch<unknown>(`/approvals/${id}/reject`, {
        token: session?.access_token,
        method: "PATCH",
        body: { reason },
      });
      removeItemLocally(id);
      showSuccess("却下しました。");
    } catch {
      setError(
        "却下に失敗しました。しばらく経ってからもう一度お試しください。"
      );
    }
  }

  // クライアントサイド検索
  const normalizedQuery = searchQuery.trim().toLowerCase();
  const filteredItems = items.filter((item) => {
    if (!normalizedQuery) return true;
    return (
      item.pipeline_label.toLowerCase().includes(normalizedQuery) ||
      item.summary.toLowerCase().includes(normalizedQuery)
    );
  });

  const statusTabs: { key: StatusFilter; label: string }[] = [
    { key: "pending", label: "承認待ち" },
    { key: "approved", label: "承認済み" },
    { key: "rejected", label: "却下済み" },
    { key: "all", label: "すべて" },
  ];

  return (
    <div className="space-y-6">
      {/* ヘッダー */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-bold">承認フロー</h1>
            {!loading && totalCount > 0 && statusFilter === "pending" && (
              <Badge variant="destructive" className="text-sm px-2 py-0.5">
                {totalCount}件 確認待ち
              </Badge>
            )}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            AIが自動実行した業務の結果を確認し、承認または却下してください。
          </p>
        </div>
        <Link href="/bpo">
          <Button variant="outline" size="sm">
            業務自動化に戻る
          </Button>
        </Link>
      </div>

      {/* 成功バナー */}
      {successMessage && (
        <div className="rounded-md border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700">
          {successMessage}
        </div>
      )}

      {/* エラーバナー */}
      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* ステータスタブ + 検索 */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            {/* ステータスタブ */}
            <div className="flex flex-wrap gap-1">
              {statusTabs.map((tab) => (
                <Button
                  key={tab.key}
                  size="sm"
                  variant={statusFilter === tab.key ? "secondary" : "ghost"}
                  onClick={() => setStatusFilter(tab.key)}
                  className="text-xs"
                >
                  {tab.label}
                </Button>
              ))}
            </div>
            {/* 検索 */}
            <div className="relative w-full sm:w-56">
              <Input
                type="search"
                placeholder="業務名・内容で絞り込む"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-8 text-sm"
              />
              <span
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground text-xs"
                aria-hidden="true"
              >
                🔍
              </span>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {/* ローディング */}
          {loading && (
            <>
              <SkeletonRow />
              <SkeletonRow />
              <SkeletonRow />
              <p className="text-center text-xs text-muted-foreground pt-1">
                データを読み込んでいます...
              </p>
            </>
          )}

          {/* 承認済み・却下済み・全件は現在準備中 */}
          {!loading && statusFilter !== "pending" && (
            <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed py-12 text-center">
              <p className="text-sm text-muted-foreground">
                {statusFilter === "approved" && "承認済みの履歴はここに表示されます。（準備中）"}
                {statusFilter === "rejected" && "却下済みの履歴はここに表示されます。（準備中）"}
                {statusFilter === "all" && "すべての実行履歴はここに表示されます。（準備中）"}
              </p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setStatusFilter("pending")}
              >
                承認待ちを確認する
              </Button>
            </div>
          )}

          {/* 承認待ちゼロ */}
          {!loading && statusFilter === "pending" && filteredItems.length === 0 && !error && (
            <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed py-12 text-center">
              <span className="text-4xl" aria-hidden="true">✅</span>
              <p className="text-sm font-medium">
                {normalizedQuery
                  ? `「${searchQuery}」に一致する承認待ちはありません`
                  : "承認待ちの業務はありません"}
              </p>
              <p className="text-xs text-muted-foreground">
                AIが業務を自動実行すると、ここに確認依頼が届きます。
              </p>
              <Link href="/bpo">
                <Button size="sm" variant="outline">
                  業務自動化を実行する
                </Button>
              </Link>
            </div>
          )}

          {/* 一覧 */}
          {!loading && statusFilter === "pending" && filteredItems.length > 0 && (
            <div className="space-y-3">
              {filteredItems.map((item) => (
                <ApprovalRow
                  key={item.id}
                  item={item}
                  onApprove={handleApprove}
                  onReject={handleReject}
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
