"use client";

import { useEffect, useState, useCallback } from "react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

// ---------------------------------------------------------------------------
// 型定義
// ---------------------------------------------------------------------------

interface Proposal {
  id: string;
  opportunity_id: string;
  version: number;
  title: string;
  content: Record<string, unknown>;
  pdf_storage_path: string | null;
  sent_at: string | null;
  sent_to: string | null;
  opened_at: string | null;
  status: "draft" | "sent" | "viewed" | "accepted" | "rejected";
  created_at: string;
}

// ---------------------------------------------------------------------------
// 定数
// ---------------------------------------------------------------------------

const STATUS_CONFIG: Record<
  Proposal["status"],
  { label: string; className: string }
> = {
  draft: { label: "下書き", className: "bg-muted text-muted-foreground" },
  sent: { label: "送付済み", className: "bg-blue-100 text-blue-800" },
  viewed: { label: "開封済み", className: "bg-yellow-100 text-yellow-800" },
  accepted: { label: "承諾", className: "bg-green-100 text-green-800" },
  rejected: { label: "辞退", className: "bg-destructive/15 text-destructive" },
};

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("ja-JP", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function formatDateTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("ja-JP", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ---------------------------------------------------------------------------
// スケルトン
// ---------------------------------------------------------------------------

function ProposalRowSkeleton() {
  return (
    <div className="flex items-center gap-3 rounded-md border p-3">
      <div className="flex-1 space-y-1.5">
        <div className="h-4 w-48 animate-pulse rounded bg-muted" />
        <div className="h-3 w-32 animate-pulse rounded bg-muted" />
      </div>
      <div className="h-5 w-16 animate-pulse rounded-full bg-muted" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 送付ダイアログ
// ---------------------------------------------------------------------------

function SendDialog({
  proposal,
  onClose,
  onSent,
  token,
}: {
  proposal: Proposal;
  onClose: () => void;
  onSent: (proposalId: string, sentTo: string) => void;
  token: string;
}) {
  const [email, setEmail] = useState(proposal.sent_to ?? "");
  const [sending, setSending] = useState(false);
  const [emailError, setEmailError] = useState<string | null>(null);

  async function handleSend() {
    if (!email.trim()) {
      setEmailError("メールアドレスを入力してください");
      return;
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      setEmailError("メールアドレスの形式で入力してください");
      return;
    }
    setEmailError(null);
    setSending(true);
    try {
      await apiFetch(`/sales/proposals/${proposal.id}/send`, {
        token,
        method: "POST",
        body: { email },
      });
      onSent(proposal.id, email);
    } catch {
      setEmailError(
        "送付に失敗しました。しばらく経ってから再度お試しください。"
      );
    } finally {
      setSending(false);
    }
  }

  return (
    <DialogContent className="sm:max-w-md w-full mx-2">
      <DialogHeader>
        <DialogTitle>提案書を送付する</DialogTitle>
        <DialogDescription>
          「{proposal.title}」を指定のメールアドレスに送付します。
        </DialogDescription>
      </DialogHeader>
      <div className="space-y-3">
        <div className="space-y-1">
          <Label>送付先メールアドレス</Label>
          <Input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="例: tanaka@example.com"
            className="w-full"
          />
          {emailError && (
            <p className="text-xs text-destructive">{emailError}</p>
          )}
        </div>
      </div>
      <DialogFooter className="flex gap-2 sm:gap-0">
        <Button variant="outline" onClick={onClose} disabled={sending}>
          キャンセル
        </Button>
        <Button onClick={handleSend} disabled={sending}>
          {sending ? (
            <>
              <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
              送付中...
            </>
          ) : (
            "提案書を送付する"
          )}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

// ---------------------------------------------------------------------------
// PDFプレビューダイアログ
// ---------------------------------------------------------------------------

function PreviewDialog({
  proposal,
  onClose,
}: {
  proposal: Proposal;
  onClose: () => void;
}) {
  return (
    <DialogContent className="sm:max-w-2xl w-full mx-2 max-h-[80vh] overflow-y-auto">
      <DialogHeader>
        <DialogTitle>{proposal.title}</DialogTitle>
        <DialogDescription>
          バージョン {proposal.version} · 作成日: {formatDate(proposal.created_at)}
        </DialogDescription>
      </DialogHeader>
      {proposal.pdf_storage_path ? (
        <div className="flex flex-col items-center gap-4 py-4">
          <p className="text-sm text-muted-foreground">
            PDFファイルが生成されています。
          </p>
          <Button
            onClick={() => {
              window.open(
                `/api/v1/sales/proposals/${proposal.id}/pdf`,
                "_blank"
              );
            }}
          >
            PDFを開く
          </Button>
        </div>
      ) : (
        <div className="rounded-md border bg-muted/30 p-4">
          <p className="text-sm text-muted-foreground mb-3">提案書の概要</p>
          <div className="space-y-2">
            {Object.entries(proposal.content).map(([key, value]) => (
              <div key={key} className="text-sm">
                <span className="font-medium">{key}:</span>{" "}
                <span className="text-muted-foreground">
                  {typeof value === "string" ? value : JSON.stringify(value)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>
          閉じる
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

// ---------------------------------------------------------------------------
// ページ
// ---------------------------------------------------------------------------

export default function ProposalsPage() {
  const { session } = useAuth();
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sendTarget, setSendTarget] = useState<Proposal | null>(null);
  const [previewTarget, setPreviewTarget] = useState<Proposal | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const fetchProposals = useCallback(async () => {
    if (!session?.access_token) return;
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch<{ items: Proposal[]; total: number }>(
        "/sales/proposals",
        {
          token: session.access_token,
          params: { limit: "100" },
        }
      );
      setProposals(res.items);
    } catch {
      setError(
        "提案書一覧の取得に失敗しました。しばらく経ってから再度お試しください。"
      );
    } finally {
      setLoading(false);
    }
  }, [session?.access_token]);

  useEffect(() => {
    fetchProposals();
  }, [fetchProposals]);

  function handleSent(proposalId: string, sentTo: string) {
    setProposals((prev) =>
      prev.map((p) =>
        p.id === proposalId
          ? { ...p, status: "sent", sent_to: sentTo, sent_at: new Date().toISOString() }
          : p
      )
    );
    setSendTarget(null);
    setSuccessMsg("提案書を送付しました。相手が開封するとステータスが更新されます。");
    setTimeout(() => setSuccessMsg(null), 5000);
  }

  const draftCount = proposals.filter((p) => p.status === "draft").length;
  const sentCount = proposals.filter(
    (p) => p.status === "sent" || p.status === "viewed"
  ).length;
  const acceptedCount = proposals.filter((p) => p.status === "accepted").length;

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      {/* ヘッダー */}
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold">提案書管理</h1>
          <p className="text-sm text-muted-foreground">
            AIが生成した提案書の送付・開封状況を管理します。
          </p>
        </div>
      </div>

      {/* サマリー */}
      <div className="grid grid-cols-3 gap-3">
        <Card>
          <CardHeader className="pb-1 pt-3 px-3">
            <p className="text-xs text-muted-foreground">下書き</p>
          </CardHeader>
          <CardContent className="px-3 pb-3">
            <p className="text-2xl font-bold">{draftCount}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-1 pt-3 px-3">
            <p className="text-xs text-muted-foreground">送付済み</p>
          </CardHeader>
          <CardContent className="px-3 pb-3">
            <p className="text-2xl font-bold">{sentCount}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-1 pt-3 px-3">
            <p className="text-xs text-muted-foreground">承諾済み</p>
          </CardHeader>
          <CardContent className="px-3 pb-3">
            <p className="text-2xl font-bold text-green-600">{acceptedCount}</p>
          </CardContent>
        </Card>
      </div>

      {/* 成功メッセージ */}
      {successMsg && (
        <div className="rounded-md border border-green-200 bg-green-50 p-3">
          <p className="text-sm text-green-800">{successMsg}</p>
        </div>
      )}

      {/* エラー */}
      {error && (
        <div className="rounded-md border border-destructive/50 bg-destructive/5 p-3">
          <p className="text-sm text-destructive">{error}</p>
        </div>
      )}

      {/* 提案書一覧 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base font-semibold">
            提案書一覧
            <span className="ml-2 text-sm font-normal text-muted-foreground">
              全 {proposals.length} 件
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {loading ? (
            <>
              {[1, 2, 3, 4].map((i) => (
                <ProposalRowSkeleton key={i} />
              ))}
            </>
          ) : proposals.length === 0 ? (
            <div className="flex flex-col items-center gap-4 py-10 text-center">
              <p className="text-sm text-muted-foreground">
                まだ提案書がありません。
              </p>
              <p className="text-xs text-muted-foreground">
                リード管理でスコアの高いリードから提案書を生成できます。
              </p>
            </div>
          ) : (
            proposals.map((proposal) => {
              const statusCfg = STATUS_CONFIG[proposal.status];
              return (
                <div
                  key={proposal.id}
                  className="flex flex-col gap-2 rounded-md border p-3 sm:flex-row sm:items-center"
                >
                  {/* タイトル・メタ情報 */}
                  <div className="flex-1 space-y-1 min-w-0">
                    <p className="text-sm font-medium truncate">
                      {proposal.title}
                    </p>
                    <div className="flex flex-wrap gap-1.5 text-xs text-muted-foreground">
                      <span>v{proposal.version}</span>
                      <span>·</span>
                      <span>作成: {formatDate(proposal.created_at)}</span>
                      {proposal.sent_at && (
                        <>
                          <span>·</span>
                          <span>送付: {formatDateTime(proposal.sent_at)}</span>
                        </>
                      )}
                      {proposal.opened_at && (
                        <>
                          <span>·</span>
                          <span className="text-yellow-700">
                            開封: {formatDateTime(proposal.opened_at)}
                          </span>
                        </>
                      )}
                    </div>
                    {proposal.sent_to && (
                      <p className="text-xs text-muted-foreground">
                        送付先: {proposal.sent_to}
                      </p>
                    )}
                  </div>

                  {/* ステータス・操作 */}
                  <div className="flex items-center gap-2 flex-wrap">
                    <Badge
                      className={`text-[11px] ${statusCfg.className}`}
                    >
                      {statusCfg.label}
                    </Badge>
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 text-xs"
                      onClick={() => setPreviewTarget(proposal)}
                    >
                      内容を確認
                    </Button>
                    {(proposal.status === "draft" ||
                      proposal.status === "viewed") && (
                      <Button
                        size="sm"
                        className="h-7 text-xs"
                        onClick={() => setSendTarget(proposal)}
                      >
                        送付する
                      </Button>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </CardContent>
      </Card>

      {/* 送付ダイアログ */}
      <Dialog
        open={sendTarget !== null}
        onOpenChange={(open) => {
          if (!open) setSendTarget(null);
        }}
      >
        {sendTarget && session?.access_token && (
          <SendDialog
            proposal={sendTarget}
            onClose={() => setSendTarget(null)}
            onSent={handleSent}
            token={session.access_token}
          />
        )}
      </Dialog>

      {/* プレビューダイアログ */}
      <Dialog
        open={previewTarget !== null}
        onOpenChange={(open) => {
          if (!open) setPreviewTarget(null);
        }}
      >
        {previewTarget && (
          <PreviewDialog
            proposal={previewTarget}
            onClose={() => setPreviewTarget(null)}
          />
        )}
      </Dialog>
    </div>
  );
}
