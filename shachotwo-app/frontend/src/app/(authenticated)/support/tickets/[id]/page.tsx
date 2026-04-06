"use client";

import { useEffect, useState, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";

// ---------- Types ----------

type TicketStatus =
  | "open"
  | "waiting"
  | "ai_responded"
  | "escalated"
  | "resolved"
  | "closed";
type TicketPriority = "low" | "medium" | "high" | "urgent";
type TicketCategory = "usage" | "billing" | "bug" | "feature" | "account";
type SenderType = "customer" | "agent" | "ai";

interface TicketMessage {
  id: string;
  sender_type: SenderType;
  content: string;
  created_at: string;
}

interface SupportTicket {
  id: string;
  ticket_number: string;
  subject: string;
  category: TicketCategory;
  priority: TicketPriority;
  status: TicketStatus;
  ai_handled: boolean;
  ai_confidence: number | null;
  ai_response: string | null;
  escalated: boolean;
  escalation_reason: string | null;
  sla_due_at: string | null;
  first_response_at: string | null;
  resolved_at: string | null;
  satisfaction_score: number | null;
  created_at: string;
  messages: TicketMessage[];
}

interface EscalateResponse {
  ticket_id: string;
  status: string;
}

interface ReplyResponse {
  message_id: string;
}

// ---------- Config ----------

const PRIORITY_CONFIG: Record<
  TicketPriority,
  { label: string; className: string }
> = {
  urgent: { label: "緊急", className: "bg-red-100 text-red-800" },
  high: { label: "高", className: "bg-orange-100 text-orange-800" },
  medium: { label: "中", className: "bg-yellow-100 text-yellow-800" },
  low: { label: "低", className: "bg-gray-100 text-gray-700" },
};

const STATUS_CONFIG: Record<
  TicketStatus,
  { label: string; className: string }
> = {
  open: { label: "未対応", className: "bg-blue-100 text-blue-800" },
  waiting: { label: "返信待ち", className: "bg-yellow-100 text-yellow-800" },
  ai_responded: {
    label: "AI対応済み",
    className: "bg-green-100 text-green-800",
  },
  escalated: {
    label: "担当者対応中",
    className: "bg-orange-100 text-orange-800",
  },
  resolved: { label: "解決済み", className: "bg-gray-100 text-gray-700" },
  closed: { label: "クローズ", className: "bg-gray-100 text-gray-500" },
};

const CATEGORY_LABELS: Record<TicketCategory, string> = {
  usage: "使い方",
  billing: "請求・料金",
  bug: "不具合",
  feature: "機能要望",
  account: "アカウント",
};

// ---------- Helpers ----------

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("ja-JP", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function getSenderLabel(senderType: SenderType): string {
  if (senderType === "ai") return "AI（自動回答）";
  if (senderType === "agent") return "サポート担当";
  return "お客様";
}

function getSenderClass(senderType: SenderType): string {
  if (senderType === "customer") return "ml-auto bg-primary text-primary-foreground";
  if (senderType === "ai") return "mr-auto bg-green-100 text-green-900";
  return "mr-auto bg-muted";
}

function getAiConfidenceLabel(confidence: number | null): string {
  if (confidence === null) return "";
  if (confidence >= 0.85) return "回答の確度：高";
  if (confidence >= 0.5) return "回答の確度：中";
  return "参考情報としてご確認ください";
}

// ---------- Page ----------

export default function TicketDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { session } = useAuth();
  const [ticket, setTicket] = useState<SupportTicket | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [replyText, setReplyText] = useState("");
  const [replying, setReplying] = useState(false);
  const [replyError, setReplyError] = useState<string | null>(null);
  const [replySuccess, setReplySuccess] = useState(false);
  const [escalateOpen, setEscalateOpen] = useState(false);
  const [escalateReason, setEscalateReason] = useState("");
  const [escalating, setEscalating] = useState(false);
  const [escalateError, setEscalateError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!session?.access_token || !params.id) return;
    setLoading(true);
    setError(null);
    apiFetch<SupportTicket>(`/support/tickets/${params.id}`, {
      token: session.access_token,
    })
      .then((data) => {
        setTicket(data);
        setTimeout(() => {
          bottomRef.current?.scrollIntoView({ behavior: "smooth" });
        }, 100);
      })
      .catch(() => {
        setError(
          "チケット情報の取得に失敗しました。しばらく経ってから再度お試しください"
        );
      })
      .finally(() => setLoading(false));
  }, [session?.access_token, params.id]);

  async function handleReply() {
    if (!session?.access_token || !ticket || !replyText.trim()) return;
    setReplying(true);
    setReplyError(null);
    setReplySuccess(false);
    try {
      await apiFetch<ReplyResponse>(
        `/support/tickets/${ticket.id}/messages`,
        {
          token: session.access_token,
          method: "POST",
          body: { content: replyText.trim(), sender_type: "agent" },
        }
      );
      setReplyText("");
      setReplySuccess(true);
      // メッセージを再取得
      const updated = await apiFetch<SupportTicket>(
        `/support/tickets/${ticket.id}`,
        { token: session.access_token }
      );
      setTicket(updated);
      setTimeout(() => {
        bottomRef.current?.scrollIntoView({ behavior: "smooth" });
      }, 100);
    } catch {
      setReplyError(
        "返信の送信に失敗しました。入力内容を確認してもう一度お試しください"
      );
    } finally {
      setReplying(false);
    }
  }

  async function handleEscalate() {
    if (!session?.access_token || !ticket) return;
    setEscalating(true);
    setEscalateError(null);
    try {
      await apiFetch<EscalateResponse>(
        `/support/tickets/${ticket.id}/escalate`,
        {
          token: session.access_token,
          method: "POST",
          body: { reason: escalateReason.trim() || undefined },
        }
      );
      setEscalateOpen(false);
      const updated = await apiFetch<SupportTicket>(
        `/support/tickets/${ticket.id}`,
        { token: session.access_token }
      );
      setTicket(updated);
    } catch {
      setEscalateError(
        "エスカレーションに失敗しました。しばらく経ってから再度お試しください"
      );
    } finally {
      setEscalating(false);
    }
  }

  if (loading) {
    return (
      <div className="mx-auto max-w-3xl space-y-4">
        <div className="h-8 w-64 animate-pulse rounded bg-muted" />
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-16 w-full animate-pulse rounded-lg bg-muted" />
          ))}
        </div>
        <p className="text-center text-sm text-muted-foreground">
          チケット情報を読み込んでいます...
        </p>
      </div>
    );
  }

  if (error || !ticket) {
    return (
      <div className="mx-auto max-w-3xl">
        <Card className="border-destructive/50 bg-destructive/5">
          <CardContent className="py-8 text-center">
            <p className="text-sm text-destructive">
              {error ?? "チケットが見つかりませんでした。"}
            </p>
            <Button
              variant="outline"
              className="mt-4"
              onClick={() => router.push("/support/tickets")}
            >
              チケット一覧に戻る
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  const priorityCfg =
    PRIORITY_CONFIG[ticket.priority] ?? PRIORITY_CONFIG.medium;
  const statusCfg = STATUS_CONFIG[ticket.status] ?? STATUS_CONFIG.open;
  const canEscalate =
    !ticket.escalated &&
    ticket.status !== "resolved" &&
    ticket.status !== "closed";

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      {/* ヘッダー */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => router.push("/support/tickets")}
              className="px-0 text-muted-foreground"
            >
              チケット一覧
            </Button>
            <span className="text-muted-foreground">/</span>
            <span className="text-sm text-muted-foreground">
              #{ticket.ticket_number}
            </span>
          </div>
          <h1 className="text-2xl font-bold">{ticket.subject}</h1>
          <div className="flex flex-wrap items-center gap-2">
            <Badge className={priorityCfg.className}>
              {priorityCfg.label}
            </Badge>
            <Badge className={statusCfg.className}>{statusCfg.label}</Badge>
            <Badge variant="outline">
              {CATEGORY_LABELS[ticket.category] ?? ticket.category}
            </Badge>
            {ticket.ai_handled && (
              <Badge className="bg-green-100 text-green-800">AI対応済み</Badge>
            )}
          </div>
        </div>

        {canEscalate && (
          <Button
            variant="outline"
            onClick={() => setEscalateOpen(true)}
            className="shrink-0 border-orange-300 text-orange-700 hover:bg-orange-50"
          >
            担当者にエスカレーション
          </Button>
        )}
      </div>

      {/* チケット情報 */}
      <Card>
        <CardContent className="grid grid-cols-2 gap-3 pt-4 text-sm sm:grid-cols-3">
          <div>
            <p className="text-xs text-muted-foreground">受付日時</p>
            <p className="font-medium">{formatDate(ticket.created_at)}</p>
          </div>
          {ticket.sla_due_at && (
            <div>
              <p className="text-xs text-muted-foreground">SLA期限</p>
              <p className="font-medium">{formatDate(ticket.sla_due_at)}</p>
            </div>
          )}
          {ticket.first_response_at && (
            <div>
              <p className="text-xs text-muted-foreground">初回応答日時</p>
              <p className="font-medium">
                {formatDate(ticket.first_response_at)}
              </p>
            </div>
          )}
          {ticket.resolved_at && (
            <div>
              <p className="text-xs text-muted-foreground">解決日時</p>
              <p className="font-medium">{formatDate(ticket.resolved_at)}</p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* AI 回答プレビュー */}
      {ticket.ai_response && (
        <Card className="border-green-200 bg-green-50">
          <CardHeader className="pb-2">
            <CardTitle className="text-base font-semibold text-green-800">
              AI 自動回答プレビュー
            </CardTitle>
            {ticket.ai_confidence !== null && (
              <CardDescription className="text-green-700">
                {getAiConfidenceLabel(ticket.ai_confidence)}
              </CardDescription>
            )}
          </CardHeader>
          <CardContent>
            <p className="whitespace-pre-wrap text-sm text-green-900">
              {ticket.ai_response}
            </p>
          </CardContent>
        </Card>
      )}

      {/* エスカレーション情報 */}
      {ticket.escalated && ticket.escalation_reason && (
        <Card className="border-orange-200 bg-orange-50">
          <CardContent className="pt-4">
            <p className="text-sm font-medium text-orange-800">
              エスカレーション理由
            </p>
            <p className="mt-1 text-sm text-orange-700">
              {ticket.escalation_reason}
            </p>
          </CardContent>
        </Card>
      )}

      {/* メッセージスレッド */}
      <div className="space-y-3">
        <h2 className="text-lg font-semibold">会話の履歴</h2>

        {ticket.messages.length === 0 ? (
          <Card>
            <CardContent className="py-8 text-center">
              <p className="text-sm text-muted-foreground">
                まだメッセージはありません。
              </p>
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-3">
            {ticket.messages.map((msg) => {
              const isCustomer = msg.sender_type === "customer";
              return (
                <div
                  key={msg.id}
                  className={`flex flex-col max-w-[85%] ${
                    isCustomer ? "ml-auto items-end" : "mr-auto items-start"
                  }`}
                >
                  <span className="mb-1 text-xs text-muted-foreground">
                    {getSenderLabel(msg.sender_type)}
                  </span>
                  <div
                    className={`rounded-xl px-4 py-3 text-sm ${getSenderClass(
                      msg.sender_type
                    )}`}
                  >
                    <p className="whitespace-pre-wrap">{msg.content}</p>
                  </div>
                  <span className="mt-1 text-[11px] text-muted-foreground">
                    {formatDate(msg.created_at)}
                  </span>
                </div>
              );
            })}
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* 返信フォーム */}
      {ticket.status !== "closed" && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base font-medium">返信する</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {replySuccess && (
              <p className="text-sm text-green-700">返信を送信しました。</p>
            )}
            {replyError && (
              <p className="text-sm text-destructive">{replyError}</p>
            )}
            <Textarea
              value={replyText}
              onChange={(e) => setReplyText(e.target.value)}
              placeholder="例: ご不便をおかけし申し訳ございません。設定画面の「ナレッジ管理」より..."
              rows={4}
              className="w-full"
            />
            <Button
              onClick={handleReply}
              disabled={replying || !replyText.trim()}
              className="w-full sm:w-auto"
            >
              {replying ? (
                <>
                  <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  送信中...
                </>
              ) : (
                "返信を送信する"
              )}
            </Button>
          </CardContent>
        </Card>
      )}

      {/* エスカレーションダイアログ */}
      <Dialog open={escalateOpen} onOpenChange={setEscalateOpen}>
        <DialogContent className="sm:max-w-md w-full mx-2">
          <DialogTitle>担当者にエスカレーションしますか？</DialogTitle>
          <DialogDescription>
            このチケットを担当者に引き継ぎます。AI による自動対応を停止し、担当者が対応します。
          </DialogDescription>
          <div className="space-y-3 pt-2">
            <Textarea
              value={escalateReason}
              onChange={(e) => setEscalateReason(e.target.value)}
              placeholder="例: AI の回答では解決できず、個別対応が必要なため"
              rows={3}
              className="w-full"
            />
            {escalateError && (
              <p className="text-sm text-destructive">{escalateError}</p>
            )}
            <div className="flex flex-col gap-2 sm:flex-row-reverse">
              <Button
                onClick={handleEscalate}
                disabled={escalating}
                className="w-full sm:w-auto"
              >
                {escalating ? (
                  <>
                    <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                    処理中...
                  </>
                ) : (
                  "担当者に引き継ぐ"
                )}
              </Button>
              <Button
                variant="outline"
                onClick={() => setEscalateOpen(false)}
                disabled={escalating}
                className="w-full sm:w-auto"
              >
                キャンセル
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
