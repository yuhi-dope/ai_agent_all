"use client";

import { useEffect, useState } from "react";
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
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";

// ---------- 型定義 ----------

interface FeatureRequest {
  id: string;
  title: string;
  description: string;
  category: string;
  priority: string;
  vote_count: number;
  mrr_impact: number;
  status: string;
  response: string | null;
  responded_at: string | null;
  customer_company_name: string;
  created_at: string;
}

interface RequestsResponse {
  items: FeatureRequest[];
  total: number;
  has_more: boolean;
}

// ---------- ヘルパー ----------

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("ja-JP", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function formatMrr(val: number): string {
  if (val === 0) return "—";
  if (val >= 1_000_000) return `¥${(val / 1_000_000).toFixed(1)}M`;
  if (val >= 1_000) return `¥${(val / 1_000).toFixed(0)}K`;
  return `¥${val.toLocaleString()}`;
}

function statusBadge(status: string): { label: string; className: string } {
  const map: Record<string, { label: string; className: string }> = {
    new: { label: "新規", className: "bg-blue-100 text-blue-800" },
    reviewing: { label: "検討中", className: "bg-yellow-100 text-yellow-800" },
    planned: { label: "対応予定", className: "bg-purple-100 text-purple-800" },
    in_progress: { label: "対応中", className: "bg-orange-100 text-orange-800" },
    done: { label: "完了", className: "bg-green-100 text-green-800" },
    declined: { label: "見送り", className: "bg-muted text-muted-foreground" },
  };
  return map[status] ?? { label: status, className: "bg-muted text-muted-foreground" };
}

function categoryLabel(cat: string): string {
  const map: Record<string, string> = {
    feature: "新機能",
    improvement: "改善",
    integration: "連携",
    bug: "不具合",
  };
  return map[cat] ?? cat;
}

function priorityBadge(priority: string): { label: string; variant: "destructive" | "secondary" | "outline" | "default" } {
  const map: Record<string, { label: string; variant: "destructive" | "secondary" | "outline" | "default" }> = {
    critical: { label: "最重要", variant: "destructive" },
    high: { label: "高", variant: "destructive" },
    medium: { label: "中", variant: "secondary" },
    low: { label: "低", variant: "outline" },
  };
  return map[priority] ?? { label: priority, variant: "outline" };
}

// ---------- スケルトン ----------

function RequestCardSkeleton() {
  return (
    <Card>
      <CardContent className="pt-4">
        <div className="flex flex-col gap-2">
          <div className="flex gap-2">
            <div className="h-5 w-14 animate-pulse rounded-full bg-muted" />
            <div className="h-5 w-14 animate-pulse rounded-full bg-muted" />
          </div>
          <div className="h-5 w-56 animate-pulse rounded bg-muted" />
          <div className="h-4 w-full animate-pulse rounded bg-muted" />
          <div className="flex gap-4">
            <div className="h-4 w-16 animate-pulse rounded bg-muted" />
            <div className="h-4 w-24 animate-pulse rounded bg-muted" />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------- ステータス変更ダイアログ ----------

interface StatusUpdateDialogProps {
  request: FeatureRequest | null;
  onClose: () => void;
  onUpdate: (id: string, status: string, response: string) => Promise<void>;
}

function StatusUpdateDialog({ request, onClose, onUpdate }: StatusUpdateDialogProps) {
  const [status, setStatus] = useState(request?.status ?? "new");
  const [response, setResponse] = useState(request?.response ?? "");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    if (request) {
      setStatus(request.status);
      setResponse(request.response ?? "");
      setSaveError(null);
    }
  }, [request]);

  if (!request) return null;

  const statusOptions = [
    { value: "new", label: "新規" },
    { value: "reviewing", label: "検討中" },
    { value: "planned", label: "対応予定" },
    { value: "in_progress", label: "対応中" },
    { value: "done", label: "完了" },
    { value: "declined", label: "見送り" },
  ];

  async function handleSave() {
    if (!request) return;
    setSaving(true);
    setSaveError(null);
    try {
      await onUpdate(request.id, status, response);
      onClose();
    } catch {
      setSaveError("更新に失敗しました。もう一度お試しください。");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={!!request} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md w-full mx-2">
        <DialogHeader>
          <DialogTitle>要望のステータスを更新する</DialogTitle>
          <DialogDescription>{request.title}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4 pt-2">
          {/* ステータス選択 */}
          <div className="space-y-2">
            <Label>ステータス</Label>
            <div className="flex flex-wrap gap-2">
              {statusOptions.map((opt) => {
                const sb = statusBadge(opt.value);
                return (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => setStatus(opt.value)}
                    className={`rounded-full px-3 py-1 text-xs font-medium transition-all border-2 ${
                      status === opt.value
                        ? "border-primary ring-2 ring-primary/20"
                        : "border-transparent"
                    } ${sb.className}`}
                  >
                    {sb.label}
                  </button>
                );
              })}
            </div>
          </div>

          {/* 回答 */}
          <div className="space-y-2">
            <Label htmlFor="response-textarea">顧客への回答（任意）</Label>
            <Textarea
              id="response-textarea"
              placeholder="例: ご要望をいただきありがとうございます。次のリリースで対応予定です。"
              value={response}
              onChange={(e) => setResponse(e.target.value)}
              rows={4}
              className="resize-none"
            />
          </div>

          {saveError && (
            <p className="text-sm text-destructive">{saveError}</p>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={onClose} disabled={saving}>
              キャンセル
            </Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving ? (
                <>
                  <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  保存中...
                </>
              ) : (
                "ステータスを更新する"
              )}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ---------- 要望カード ----------

interface RequestCardProps {
  request: FeatureRequest;
  rank: number;
  onSelect: (req: FeatureRequest) => void;
}

function RequestCard({ request, rank, onSelect }: RequestCardProps) {
  const st = statusBadge(request.status);
  const pb = priorityBadge(request.priority);

  return (
    <Card className="transition-shadow hover:shadow-sm">
      <CardContent className="pt-4 pb-4">
        <div className="flex gap-3">
          {/* ランク */}
          <div className="flex shrink-0 flex-col items-center gap-1 pt-0.5">
            <span className="text-xl font-bold text-muted-foreground">{rank}</span>
            <div className="flex flex-col items-center">
              <span className="text-base font-bold">{request.vote_count}</span>
              <span className="text-[11px] text-muted-foreground">票</span>
            </div>
          </div>

          {/* 内容 */}
          <div className="flex-1 min-w-0 space-y-2">
            {/* バッジ行 */}
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`rounded-full px-2 py-0.5 text-xs font-medium ${st.className}`}
              >
                {st.label}
              </span>
              <Badge variant={pb.variant} className="text-xs">
                {pb.label}
              </Badge>
              <Badge variant="outline" className="text-xs">
                {categoryLabel(request.category)}
              </Badge>
            </div>

            {/* タイトル */}
            <p className="font-semibold text-sm leading-tight">{request.title}</p>

            {/* 説明 */}
            <p className="text-xs text-muted-foreground line-clamp-2">{request.description}</p>

            {/* メタ情報 */}
            <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
              <span>
                MRRインパクト:{" "}
                <span className="font-medium text-foreground">
                  {formatMrr(request.mrr_impact)}
                </span>
              </span>
              <span>{request.customer_company_name}</span>
              <span>{formatDate(request.created_at)}</span>
            </div>

            {/* 回答済み表示 */}
            {request.response && (
              <div className="rounded-md border border-green-200 bg-green-50 p-2">
                <p className="text-xs font-medium text-green-700">回答済み</p>
                <p className="mt-0.5 text-xs text-green-700 line-clamp-2">
                  {request.response}
                </p>
              </div>
            )}

            {/* アクション */}
            <div className="pt-1">
              <Button
                variant="outline"
                size="sm"
                onClick={() => onSelect(request)}
              >
                ステータスを更新する
              </Button>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------- ページ ----------

export default function CrmRequestsPage() {
  const { session } = useAuth();
  const [requests, setRequests] = useState<FeatureRequest[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [categoryFilter, setCategoryFilter] = useState<string>("all");
  const [selectedRequest, setSelectedRequest] = useState<FeatureRequest | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!session?.access_token) return;
    fetchRequests();
  }, [session?.access_token, statusFilter, categoryFilter]);

  async function fetchRequests() {
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = {
        sort_by: "priority_score",
        order: "desc",
        limit: "50",
      };
      if (statusFilter !== "all") {
        params.status = statusFilter;
      }
      if (categoryFilter !== "all") {
        params.category = categoryFilter;
      }

      const res = await apiFetch<RequestsResponse>("/crm/requests", {
        token: session?.access_token,
        params,
      });
      setRequests(res.items);
      setTotal(res.total);
    } catch {
      setError("要望一覧の取得に失敗しました。しばらく経ってから再度お試しください。");
    } finally {
      setLoading(false);
    }
  }

  async function handleStatusUpdate(id: string, status: string, response: string) {
    await apiFetch(`/crm/requests/${id}`, {
      token: session?.access_token,
      method: "PATCH",
      body: { status, response: response || null },
    });

    // 楽観的更新
    setRequests((prev) =>
      prev.map((r) =>
        r.id === id ? { ...r, status, response: response || null } : r
      )
    );
    setSuccessMessage("ステータスを更新しました。");
    setTimeout(() => setSuccessMessage(null), 3000);
  }

  const statusOptions = [
    { value: "all", label: "すべて" },
    { value: "new", label: "新規" },
    { value: "reviewing", label: "検討中" },
    { value: "planned", label: "対応予定" },
    { value: "in_progress", label: "対応中" },
    { value: "done", label: "完了" },
    { value: "declined", label: "見送り" },
  ];

  const categoryOptions = [
    { value: "all", label: "すべて" },
    { value: "feature", label: "新機能" },
    { value: "improvement", label: "改善" },
    { value: "integration", label: "連携" },
    { value: "bug", label: "不具合" },
  ];

  // 統計
  const totalMrrImpact = requests.reduce((s, r) => s + r.mrr_impact, 0);
  const pendingCount = requests.filter(
    (r) => r.status === "new" || r.status === "reviewing"
  ).length;

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      {/* ページヘッダー */}
      <div>
        <h1 className="text-2xl font-bold">要望ボード</h1>
        <p className="text-sm text-muted-foreground">
          {total !== null ? `${total}件` : ""}　投票数・MRRインパクト順にランキング表示しています。
        </p>
      </div>

      {/* 成功トースト */}
      {successMessage && (
        <div className="rounded-lg border border-green-200 bg-green-50 px-4 py-3">
          <p className="text-sm font-medium text-green-700">{successMessage}</p>
        </div>
      )}

      {/* エラー */}
      {error && (
        <Card className="border-destructive/50 bg-destructive/5">
          <CardContent className="pt-4">
            <p className="text-sm text-destructive">{error}</p>
          </CardContent>
        </Card>
      )}

      {/* サマリー */}
      {!loading && requests.length > 0 && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <Card>
            <CardHeader className="pb-1 pt-4">
              <CardDescription className="text-xs">要望総数</CardDescription>
            </CardHeader>
            <CardContent className="pb-4">
              <p className="text-2xl font-bold">{total ?? requests.length}件</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-1 pt-4">
              <CardDescription className="text-xs">未対応の要望</CardDescription>
            </CardHeader>
            <CardContent className="pb-4">
              <p className="text-2xl font-bold text-yellow-600">{pendingCount}件</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-1 pt-4">
              <CardDescription className="text-xs">潜在MRRインパクト</CardDescription>
            </CardHeader>
            <CardContent className="pb-4">
              <p className="text-2xl font-bold">{formatMrr(totalMrrImpact)}</p>
              <p className="text-xs text-muted-foreground">対応した場合の追加MRR推計</p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* フィルター */}
      <div className="space-y-3">
        <div>
          <p className="mb-2 text-xs font-medium text-muted-foreground">ステータス</p>
          <div className="flex flex-wrap gap-2">
            {statusOptions.map((opt) => (
              <Button
                key={opt.value}
                variant={statusFilter === opt.value ? "default" : "outline"}
                size="sm"
                onClick={() => setStatusFilter(opt.value)}
              >
                {opt.label}
              </Button>
            ))}
          </div>
        </div>
        <div>
          <p className="mb-2 text-xs font-medium text-muted-foreground">カテゴリ</p>
          <div className="flex flex-wrap gap-2">
            {categoryOptions.map((opt) => (
              <Button
                key={opt.value}
                variant={categoryFilter === opt.value ? "secondary" : "outline"}
                size="sm"
                onClick={() => setCategoryFilter(opt.value)}
              >
                {opt.label}
              </Button>
            ))}
          </div>
        </div>
      </div>

      {/* 要望リスト */}
      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <RequestCardSkeleton key={i} />
          ))}
        </div>
      ) : requests.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-4 py-12 text-center">
            <p className="text-sm text-muted-foreground">
              {statusFilter !== "all" || categoryFilter !== "all"
                ? "条件に一致する要望が見つかりませんでした。"
                : "まだ要望が登録されていません。顧客からのフィードバックが届くと自動で表示されます。"}
            </p>
            {(statusFilter !== "all" || categoryFilter !== "all") && (
              <Button
                variant="outline"
                onClick={() => {
                  setStatusFilter("all");
                  setCategoryFilter("all");
                }}
              >
                フィルターをリセットする
              </Button>
            )}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {requests.map((req, i) => (
            <RequestCard
              key={req.id}
              request={req}
              rank={i + 1}
              onSelect={setSelectedRequest}
            />
          ))}
        </div>
      )}

      {/* ステータス更新ダイアログ */}
      <StatusUpdateDialog
        request={selectedRequest}
        onClose={() => setSelectedRequest(null)}
        onUpdate={handleStatusUpdate}
      />
    </div>
  );
}
