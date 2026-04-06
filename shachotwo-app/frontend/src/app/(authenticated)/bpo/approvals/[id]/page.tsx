"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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
import { apiFetch } from "@/lib/api";

// ---------- 型定義 ----------

interface ExecutionDetail {
  id: string;
  pipeline_key: string;
  pipeline_label: string;
  created_at: string;
  summary: string;
  confidence: number;
  approval_status: string;
  output_detail: string | null;
  final_output: Record<string, unknown>;
  steps: Array<Record<string, unknown>>;
  approved_by: string | null;
  approved_at: string | null;
  rejection_reason: string | null;
}

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

function approvalStatusBadge(status: string): {
  label: string;
  className: string;
} {
  switch (status) {
    case "pending":
      return {
        label: "承認待ち",
        className: "bg-yellow-100 text-yellow-800",
      };
    case "approved":
    case "modified":
      return { label: "承認済み", className: "bg-green-100 text-green-800" };
    case "rejected":
      return {
        label: "却下済み",
        className: "bg-red-100 text-red-800",
      };
    default:
      return { label: status, className: "bg-gray-100 text-gray-700" };
  }
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
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

function DetailSkeleton() {
  return (
    <div className="space-y-4 animate-pulse">
      <div className="h-6 w-48 rounded bg-muted" />
      <div className="h-4 w-64 rounded bg-muted" />
      <div className="rounded-lg border p-4 space-y-3">
        <div className="h-4 w-32 rounded bg-muted" />
        <div className="h-40 w-full rounded bg-muted" />
      </div>
      <div className="rounded-lg border p-4 space-y-3">
        <div className="h-4 w-24 rounded bg-muted" />
        <div className="h-20 w-full rounded bg-muted" />
      </div>
      <p className="text-center text-xs text-muted-foreground">
        データを読み込んでいます...
      </p>
    </div>
  );
}

// ---------- ページ ----------

export default function BPOApprovalDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { session } = useAuth();

  const [detail, setDetail] = useState<ExecutionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const [isRejectOpen, setIsRejectOpen] = useState(false);
  const [rejectionReason, setRejectionReason] = useState("");
  const [isApproving, setIsApproving] = useState(false);
  const [isRejecting, setIsRejecting] = useState(false);

  function showSuccess(msg: string) {
    setSuccessMessage(msg);
    setTimeout(() => setSuccessMessage(null), 3000);
  }

  useEffect(() => {
    if (!session?.access_token || !params.id) return;
    setLoading(true);
    setError(null);
    apiFetch<ExecutionDetail>(`/approvals/${params.id}`, {
      token: session.access_token,
    })
      .then((data) => setDetail(data))
      .catch(() =>
        setError(
          "詳細データの取得に失敗しました。しばらく経ってからもう一度お試しください。"
        )
      )
      .finally(() => setLoading(false));
  }, [session?.access_token, params.id]);

  async function handleApprove() {
    if (!detail) return;
    setError(null);
    setIsApproving(true);
    try {
      await apiFetch<unknown>(`/approvals/${detail.id}/approve`, {
        token: session?.access_token,
        method: "PATCH",
        body: {},
      });
      setDetail((prev) =>
        prev ? { ...prev, approval_status: "approved" } : prev
      );
      showSuccess("承認しました。");
    } catch {
      setError(
        "承認に失敗しました。しばらく経ってからもう一度お試しください。"
      );
    } finally {
      setIsApproving(false);
    }
  }

  async function handleRejectSubmit() {
    if (!detail || !rejectionReason.trim()) return;
    setError(null);
    setIsRejecting(true);
    try {
      await apiFetch<unknown>(`/approvals/${detail.id}/reject`, {
        token: session?.access_token,
        method: "PATCH",
        body: { reason: rejectionReason.trim() },
      });
      setDetail((prev) =>
        prev
          ? {
              ...prev,
              approval_status: "rejected",
              rejection_reason: rejectionReason.trim(),
            }
          : prev
      );
      setIsRejectOpen(false);
      setRejectionReason("");
      showSuccess("却下しました。");
    } catch {
      setError(
        "却下に失敗しました。しばらく経ってからもう一度お試しください。"
      );
    } finally {
      setIsRejecting(false);
    }
  }

  const isPending = detail?.approval_status === "pending";

  // final_output から表示用テキストを組み立てる
  function buildReadableOutput(output: Record<string, unknown>): string {
    if (!output || Object.keys(output).length === 0) {
      return "出力データがありません。";
    }
    // message / summary フィールドを優先して表示
    const lines: string[] = [];
    if (output.message) lines.push(`実行結果: ${output.message}`);
    if (output.summary) lines.push(`概要: ${output.summary}`);
    // その他のフィールドをリスト表示
    for (const [key, value] of Object.entries(output)) {
      if (key === "message" || key === "summary") continue;
      if (typeof value === "object") {
        lines.push(`${key}: ${JSON.stringify(value, null, 2)}`);
      } else {
        lines.push(`${key}: ${value}`);
      }
    }
    return lines.join("\n");
  }

  return (
    <div className="space-y-6">
      {/* ナビゲーション */}
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Link href="/bpo" className="hover:text-foreground transition-colors">
          業務自動化
        </Link>
        <span>/</span>
        <Link
          href="/bpo/approvals"
          className="hover:text-foreground transition-colors"
        >
          承認フロー
        </Link>
        <span>/</span>
        <span className="text-foreground">詳細</span>
      </div>

      {/* ローディング */}
      {loading && <DetailSkeleton />}

      {/* エラー */}
      {!loading && error && !detail && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* 本体 */}
      {!loading && detail && (
        <>
          {/* ヘッダー */}
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-2xl font-bold">{detail.pipeline_label}</h1>
                <Badge
                  className={`text-xs ${approvalStatusBadge(detail.approval_status).className}`}
                >
                  {approvalStatusBadge(detail.approval_status).label}
                </Badge>
                <Badge
                  className={`text-xs ${confidenceLabel(detail.confidence).className}`}
                >
                  {confidenceLabel(detail.confidence).label}
                </Badge>
              </div>
              <p className="mt-1 text-sm text-muted-foreground">
                実行日時: {formatDate(detail.created_at)}
              </p>
            </div>
            {isPending && (
              <div className="flex flex-wrap gap-2">
                <Button
                  disabled={isApproving}
                  onClick={handleApprove}
                  size="sm"
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
            )}
          </div>

          {/* 成功バナー */}
          {successMessage && (
            <div className="rounded-md border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700">
              {successMessage}
            </div>
          )}

          {/* エラーバナー（操作失敗時） */}
          {error && (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
              {error}
            </div>
          )}

          {/* 概要 */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">AIの判断概要</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm leading-relaxed">{detail.summary}</p>
            </CardContent>
          </Card>

          {/* 実行結果プレビュー */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">実行結果の詳細</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="rounded-md border bg-muted/40 p-4 text-sm font-mono whitespace-pre-wrap leading-relaxed max-h-96 overflow-y-auto">
                {detail.final_output && Object.keys(detail.final_output).length > 0
                  ? buildReadableOutput(detail.final_output)
                  : "出力データがありません。"}
              </div>
            </CardContent>
          </Card>

          {/* 処理ステップ */}
          {detail.steps && detail.steps.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">処理ステップ</CardTitle>
              </CardHeader>
              <CardContent>
                <ol className="space-y-3">
                  {detail.steps.map((step, index) => {
                    const stepName =
                      typeof step.step === "string"
                        ? step.step
                        : `ステップ ${index + 1}`;
                    const stepSuccess = step.success !== false;
                    return (
                      <li
                        key={index}
                        className="flex items-start gap-3 text-sm"
                      >
                        <span
                          className={`shrink-0 mt-0.5 text-xs font-bold rounded-full w-5 h-5 flex items-center justify-center ${
                            stepSuccess
                              ? "bg-green-100 text-green-700"
                              : "bg-red-100 text-red-700"
                          }`}
                        >
                          {index + 1}
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className="font-medium leading-snug">{stepName}</p>
                          {typeof step.output === "string" && step.output && (
                            <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
                              {step.output}
                            </p>
                          )}
                        </div>
                        <Badge
                          className={`shrink-0 text-xs ${
                            stepSuccess
                              ? "bg-green-100 text-green-800"
                              : "bg-red-100 text-red-800"
                          }`}
                        >
                          {stepSuccess ? "完了" : "失敗"}
                        </Badge>
                      </li>
                    );
                  })}
                </ol>
              </CardContent>
            </Card>
          )}

          {/* 承認履歴 */}
          {!isPending && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">承認履歴</CardTitle>
              </CardHeader>
              <CardContent className="text-sm space-y-2">
                <div className="flex items-center gap-2">
                  <span className="text-muted-foreground w-24 shrink-0">
                    ステータス
                  </span>
                  <Badge
                    className={`text-xs ${approvalStatusBadge(detail.approval_status).className}`}
                  >
                    {approvalStatusBadge(detail.approval_status).label}
                  </Badge>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-muted-foreground w-24 shrink-0">
                    処理日時
                  </span>
                  <span>{formatDate(detail.approved_at)}</span>
                </div>
                {detail.rejection_reason && (
                  <div className="flex items-start gap-2">
                    <span className="text-muted-foreground w-24 shrink-0">
                      却下理由
                    </span>
                    <span className="leading-relaxed">
                      {detail.rejection_reason}
                    </span>
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {/* 下部ナビゲーション */}
          <div className="flex flex-wrap gap-2 pt-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => router.push("/bpo/approvals")}
            >
              一覧に戻る
            </Button>
            <Link href="/bpo">
              <Button variant="outline" size="sm">
                業務自動化に戻る
              </Button>
            </Link>
          </div>
        </>
      )}

      {/* 却下ダイアログ */}
      <Dialog open={isRejectOpen} onOpenChange={setIsRejectOpen}>
        <DialogContent className="sm:max-w-md w-full mx-2">
          <DialogHeader>
            <DialogTitle>この実行結果を却下しますか？</DialogTitle>
            <DialogDescription>
              「{detail?.summary}」を却下します。却下理由を入力してください。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="reject-reason-detail">却下理由</Label>
            <Textarea
              id="reject-reason-detail"
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
    </div>
  );
}
