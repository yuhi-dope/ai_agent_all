"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";

// ---------- 型定義 ----------

interface ProcessItem {
  id?: string;
  process_name: string;
  equipment: string;
  setup_time_min: number;
  cycle_time_min: number;
  charge_rate: number;
  cost?: number;
}

interface ManufacturingQuoteDetail {
  id: string;
  quote_number: string;
  customer_name: string | null;
  product_name: string;
  material: string;
  quantity: number;
  surface_treatment: string;
  sub_industry: string;
  delivery_days: number | null;
  overhead_rate: number;
  profit_rate: number;
  material_cost: number;
  process_cost: number;
  additional_cost: number;
  overhead_cost: number;
  profit: number;
  total_amount: number;
  status: string;
  overall_confidence: number | null;
  processes: ProcessItem[];
  created_at: string;
  updated_at: string | null;
}

// ---------- ステータス設定 ----------

const STATUS_MAP: Record<string, { label: string; className: string }> = {
  draft: { label: "下書き", className: "bg-secondary text-secondary-foreground" },
  sent: { label: "送付済み", className: "bg-secondary text-secondary-foreground" },
  won: { label: "受注", className: "bg-green-100 text-green-800" },
  lost: { label: "失注", className: "bg-destructive/10 text-destructive" },
};

const SURFACE_TREATMENT_LABELS: Record<string, string> = {
  none: "なし",
  mekki: "メッキ",
  alumite: "アルマイト",
  kuroshime: "黒染め",
  coating: "塗装",
  nitriding: "窒化処理",
  plating_hard_chrome: "硬質クロムメッキ",
  other: "その他",
};

const SUB_INDUSTRY_LABELS: Record<string, string> = {
  metalwork: "金属加工",
  plastics: "樹脂・プラスチック加工",
  food_chemical: "食品・化学",
  electronics: "電子部品・精密機器",
  general: "汎用製造",
};

// ---------- 日付フォーマット ----------

function formatDate(isoString: string): string {
  try {
    const d = new Date(isoString);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  } catch {
    return isoString;
  }
}

// ---------- 確度バッジ ----------

function ConfidenceBadge({ confidence }: { confidence: number }) {
  if (confidence >= 0.8) {
    return (
      <span className="inline-flex items-center rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800">
        確度：高
      </span>
    );
  }
  if (confidence >= 0.5) {
    return (
      <span className="inline-flex items-center rounded-full bg-yellow-100 px-2 py-0.5 text-xs font-medium text-yellow-800">
        確度：中
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded-full bg-secondary px-2 py-0.5 text-xs font-medium text-secondary-foreground">
      参考情報
    </span>
  );
}

// ---------- スケルトン ----------

function Skeleton({ className }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-muted ${className ?? ""}`} />;
}

function DetailSkeleton() {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-9 w-28" />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        {[...Array(4)].map((_, i) => (
          <Card key={i}>
            <CardContent className="pt-6">
              <Skeleton className="h-4 w-24 mb-2" />
              <Skeleton className="h-6 w-32" />
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}

// ---------- 確認ダイアログ ----------

interface ConfirmDialogProps {
  open: boolean;
  message: string;
  onConfirm: () => void;
  onCancel: () => void;
}

function ConfirmDialog({ open, message, onConfirm, onCancel }: ConfirmDialogProps) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4">
      <div className="w-full max-w-sm rounded-lg border bg-background p-6 shadow-lg space-y-4">
        <p className="text-sm leading-relaxed">{message}</p>
        <div className="flex justify-end gap-3">
          <Button variant="outline" size="sm" onClick={onCancel}>
            キャンセル
          </Button>
          <Button size="sm" onClick={onConfirm}>
            確認する
          </Button>
        </div>
      </div>
    </div>
  );
}

// ---------- 新規作成リンク用クエリ文字列ビルダー ----------

function buildPrefillQuery(quote: ManufacturingQuoteDetail): string {
  const params = new URLSearchParams({
    product_name: quote.product_name,
    material: quote.material,
    quantity: String(quote.quantity),
    surface_treatment: quote.surface_treatment,
    sub_industry: quote.sub_industry,
    ...(quote.customer_name ? { customer_name: quote.customer_name } : {}),
    ...(quote.delivery_days != null ? { delivery_days: String(quote.delivery_days) } : {}),
  });
  return params.toString();
}

// ---------- Page ----------

export default function ManufacturingQuoteDetailPage() {
  const params = useParams();
  const router = useRouter();
  const { session } = useAuth();
  const id = params?.id as string;

  const [quote, setQuote] = useState<ManufacturingQuoteDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // インライン編集用
  const [editingProcessIndex, setEditingProcessIndex] = useState<number | null>(null);
  const [editProcessValues, setEditProcessValues] = useState<Partial<ProcessItem>>({});
  const [savingProcess, setSavingProcess] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null);

  // ステータス更新
  const [updatingStatus, setUpdatingStatus] = useState(false);

  // 確認ダイアログ
  const [confirmDialog, setConfirmDialog] = useState<{
    open: boolean;
    message: string;
    onConfirm: () => void;
  }>({ open: false, message: "", onConfirm: () => {} });

  const token = session?.access_token;

  const load = useCallback(async () => {
    if (!token || !id) return;
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch<ManufacturingQuoteDetail>(
        `/bpo/manufacturing/quotes/${id}`,
        { token }
      );
      setQuote(data);
    } catch {
      setError("見積データの取得に失敗しました。しばらく経ってから再度お試しください。");
    } finally {
      setLoading(false);
    }
  }, [token, id]);

  useEffect(() => {
    load();
  }, [load]);

  // ---------- 工程インライン編集 ----------

  function startEditProcess(index: number, process: ProcessItem) {
    setEditingProcessIndex(index);
    setEditProcessValues({ ...process });
  }

  function cancelEditProcess() {
    setEditingProcessIndex(null);
    setEditProcessValues({});
  }

  async function saveProcess(index: number) {
    if (!token || !quote) return;
    setSavingProcess(true);
    try {
      const updatedProcesses = quote.processes.map((p, i) =>
        i === index ? { ...p, ...editProcessValues } : p
      );
      await apiFetch<ManufacturingQuoteDetail>(
        `/bpo/manufacturing/quotes/${id}`,
        {
          method: "PATCH",
          token,
          body: { processes: updatedProcesses },
        }
      );
      setEditingProcessIndex(null);
      setEditProcessValues({});
      showSuccess("工程情報を更新しました");
      // 合計金額等を最新化するためデータ全体を再取得する
      await load();
    } catch {
      setError("工程の保存に失敗しました。しばらく経ってから再度お試しください。");
    } finally {
      setSavingProcess(false);
    }
  }

  // ---------- ステータス更新 ----------

  async function updateStatus(newStatus: string) {
    if (!token || !quote) return;
    setUpdatingStatus(true);
    try {
      const updated = await apiFetch<ManufacturingQuoteDetail>(
        `/bpo/manufacturing/quotes/${id}`,
        {
          method: "PATCH",
          token,
          body: { status: newStatus },
        }
      );
      setQuote(updated);
      showSuccess(
        newStatus === "sent"
          ? "見積を送付済みに更新しました"
          : newStatus === "won"
          ? "受注として記録しました"
          : newStatus === "lost"
          ? "失注として記録しました"
          : "ステータスを更新しました"
      );
    } catch {
      setError("ステータスの更新に失敗しました。しばらく経ってから再度お試しください。");
    } finally {
      setUpdatingStatus(false);
    }
  }

  function requestStatusChange(newStatus: string) {
    const message =
      newStatus === "won"
        ? "受注として記録します。この操作は取り消せません。よろしいですか？"
        : "失注として記録します。この操作は取り消せません。よろしいですか？";
    setConfirmDialog({
      open: true,
      message,
      onConfirm: () => {
        setConfirmDialog((prev) => ({ ...prev, open: false }));
        updateStatus(newStatus);
      },
    });
  }

  function showSuccess(msg: string) {
    setSaveSuccess(msg);
    setTimeout(() => setSaveSuccess(null), 3000);
  }

  // ---------- レンダリング ----------

  if (loading) return <DetailSkeleton />;

  if (error && !quote) {
    return (
      <div className="space-y-4">
        <Button variant="outline" onClick={() => router.push("/bpo/manufacturing")}>
          一覧に戻る
        </Button>
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      </div>
    );
  }

  if (!quote) return null;

  const st = STATUS_MAP[quote.status] ?? {
    label: quote.status,
    className: "bg-secondary text-secondary-foreground",
  };

  const unitPrice =
    quote.quantity > 0 ? Math.round(quote.total_amount / quote.quantity) : null;

  const prefillQuery = buildPrefillQuery(quote);

  return (
    <>
      <ConfirmDialog
        open={confirmDialog.open}
        message={confirmDialog.message}
        onConfirm={confirmDialog.onConfirm}
        onCancel={() => setConfirmDialog((prev) => ({ ...prev, open: false }))}
      />

      <div className="space-y-6">
        {/* ヘッダー */}
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1 min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                className="text-muted-foreground h-7 px-2"
                onClick={() => router.push("/bpo/manufacturing")}
              >
                ← 一覧
              </Button>
              <span className="text-xs font-mono text-muted-foreground">
                {quote.quote_number}
              </span>
            </div>
            <h1 className="text-2xl font-bold leading-tight">{quote.product_name}</h1>
            <div className="flex flex-wrap items-center gap-2">
              <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${st.className}`}>
                {st.label}
              </span>
              {quote.overall_confidence != null && (
                <ConfidenceBadge confidence={quote.overall_confidence} />
              )}
              <span className="text-xs text-muted-foreground">
                作成日: {formatDate(quote.created_at)}
              </span>
            </div>
          </div>
          {/* PDF出力（準備中） */}
          <Button variant="outline" disabled>
            見積書PDFを出力する（準備中）
          </Button>
        </div>

        {/* 成功バナー */}
        {saveSuccess && (
          <div className="rounded-md border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700">
            {saveSuccess}
          </div>
        )}

        {/* エラーバナー */}
        {error && (
          <div className="rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {/* 基本情報 */}
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
              基本情報
            </h2>
            <button
              type="button"
              className="text-xs text-primary underline underline-offset-2 hover:opacity-70"
              onClick={() => router.push(`/bpo/manufacturing/new?${prefillQuery}`)}
            >
              内容を修正して再見積する →
            </button>
          </div>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Card>
              <CardContent className="pt-4 pb-4">
                <p className="text-xs text-muted-foreground">顧客名</p>
                <p className="font-medium mt-0.5">{quote.customer_name ?? "—"}</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4 pb-4">
                <p className="text-xs text-muted-foreground">材質</p>
                <p className="font-medium mt-0.5">{quote.material}</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4 pb-4">
                <p className="text-xs text-muted-foreground">数量</p>
                <p className="font-medium mt-0.5">{quote.quantity.toLocaleString()} 個</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4 pb-4">
                <p className="text-xs text-muted-foreground">表面処理</p>
                <p className="font-medium mt-0.5">
                  {SURFACE_TREATMENT_LABELS[quote.surface_treatment] ?? quote.surface_treatment}
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4 pb-4">
                <p className="text-xs text-muted-foreground">業種・加工種別</p>
                <p className="font-medium mt-0.5">
                  {SUB_INDUSTRY_LABELS[quote.sub_industry] ?? quote.sub_industry}
                </p>
              </CardContent>
            </Card>
            {quote.delivery_days != null && (
              <Card>
                <CardContent className="pt-4 pb-4">
                  <p className="text-xs text-muted-foreground">納期</p>
                  <p className="font-medium mt-0.5">{quote.delivery_days} 日</p>
                </CardContent>
              </Card>
            )}
          </div>
        </div>

        {/* 原価内訳 */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">原価・見積金額</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="rounded-md border overflow-hidden">
              <table className="w-full text-sm">
                <tbody>
                  <tr className="border-b">
                    <td className="px-4 py-3 text-muted-foreground">材料費</td>
                    <td className="px-4 py-3 text-right font-medium">
                      ¥{Math.round(quote.material_cost).toLocaleString()}
                    </td>
                  </tr>
                  <tr className="border-b">
                    <td className="px-4 py-3 text-muted-foreground">
                      加工費（{quote.processes.length}工程）
                    </td>
                    <td className="px-4 py-3 text-right font-medium">
                      ¥{Math.round(quote.process_cost).toLocaleString()}
                    </td>
                  </tr>
                  {quote.additional_cost > 0 && (
                    <tr className="border-b">
                      <td className="px-4 py-3 text-muted-foreground">
                        その他費用（金型償却等）
                      </td>
                      <td className="px-4 py-3 text-right font-medium">
                        ¥{Math.round(quote.additional_cost).toLocaleString()}
                      </td>
                    </tr>
                  )}
                  <tr className="border-b bg-muted/30">
                    <td className="px-4 py-3 text-muted-foreground">
                      諸経費（{Math.round(quote.overhead_rate * 100)}%）
                    </td>
                    <td className="px-4 py-3 text-right font-medium">
                      ¥{Math.round(quote.overhead_cost).toLocaleString()}
                    </td>
                  </tr>
                  <tr className="border-b bg-muted/30">
                    <td className="px-4 py-3 text-muted-foreground">
                      利益（{Math.round(quote.profit_rate * 100)}%）
                    </td>
                    <td className="px-4 py-3 text-right font-medium">
                      ¥{Math.round(quote.profit).toLocaleString()}
                    </td>
                  </tr>
                  <tr className="bg-primary/5">
                    <td className="px-4 py-3 font-semibold">合計金額</td>
                    <td className="px-4 py-3 text-right text-xl font-bold">
                      ¥{Math.round(quote.total_amount).toLocaleString()}
                    </td>
                  </tr>
                  {unitPrice != null && (
                    <tr className="border-t bg-muted/20">
                      <td className="px-4 py-3 text-xs text-muted-foreground">
                        1個あたり単価
                      </td>
                      <td className="px-4 py-3 text-right text-sm font-medium text-muted-foreground">
                        ¥{unitPrice.toLocaleString()}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>

        {/* 加工工程 */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">加工工程</CardTitle>
          </CardHeader>
          <CardContent>
            {quote.processes.length === 0 ? (
              <p className="text-sm text-muted-foreground py-4 text-center">
                加工工程の情報がありません
              </p>
            ) : (
              <>
                {/* PC: テーブル表示 */}
                <div className="hidden md:block">
                  <div className="rounded-md border overflow-hidden">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b bg-muted/50">
                          <th className="px-4 py-2 text-left font-medium text-muted-foreground">工程名</th>
                          <th className="px-4 py-2 text-left font-medium text-muted-foreground">設備</th>
                          <th className="px-4 py-2 text-right font-medium text-muted-foreground">段取（分）</th>
                          <th className="px-4 py-2 text-right font-medium text-muted-foreground">サイクル（分）</th>
                          <th className="px-4 py-2 text-right font-medium text-muted-foreground">チャージ（円/時）</th>
                          <th className="px-4 py-2 text-right font-medium text-muted-foreground">工程費</th>
                          <th className="px-4 py-2" />
                        </tr>
                      </thead>
                      <tbody>
                        {quote.processes.map((p, i) => {
                          const isEditing = editingProcessIndex === i;
                          return (
                            <tr key={i} className="border-b last:border-0">
                              {isEditing ? (
                                <td colSpan={7} className="px-4 py-3">
                                  <div className="space-y-3">
                                    <div className="grid gap-3 sm:grid-cols-2">
                                      <div className="space-y-1">
                                        <label className="text-xs text-muted-foreground">工程名</label>
                                        <Input
                                          value={editProcessValues.process_name ?? ""}
                                          onChange={(e) =>
                                            setEditProcessValues((prev) => ({
                                              ...prev,
                                              process_name: e.target.value,
                                            }))
                                          }
                                          className="h-8"
                                        />
                                      </div>
                                      <div className="space-y-1">
                                        <label className="text-xs text-muted-foreground">設備</label>
                                        <Input
                                          value={editProcessValues.equipment ?? ""}
                                          onChange={(e) =>
                                            setEditProcessValues((prev) => ({
                                              ...prev,
                                              equipment: e.target.value,
                                            }))
                                          }
                                          className="h-8"
                                        />
                                      </div>
                                    </div>
                                    <div className="grid grid-cols-3 gap-3">
                                      <div className="space-y-1">
                                        <label className="text-xs text-muted-foreground">段取時間（分）</label>
                                        <Input
                                          type="number"
                                          min={0}
                                          value={editProcessValues.setup_time_min ?? 0}
                                          onChange={(e) =>
                                            setEditProcessValues((prev) => ({
                                              ...prev,
                                              setup_time_min: Number(e.target.value),
                                            }))
                                          }
                                          className="h-8"
                                        />
                                      </div>
                                      <div className="space-y-1">
                                        <label className="text-xs text-muted-foreground">サイクル（分）</label>
                                        <Input
                                          type="number"
                                          min={0}
                                          value={editProcessValues.cycle_time_min ?? 0}
                                          onChange={(e) =>
                                            setEditProcessValues((prev) => ({
                                              ...prev,
                                              cycle_time_min: Number(e.target.value),
                                            }))
                                          }
                                          className="h-8"
                                        />
                                      </div>
                                      <div className="space-y-1">
                                        <label className="text-xs text-muted-foreground">チャージ（円/時）</label>
                                        <Input
                                          type="number"
                                          min={0}
                                          value={editProcessValues.charge_rate ?? 0}
                                          onChange={(e) =>
                                            setEditProcessValues((prev) => ({
                                              ...prev,
                                              charge_rate: Number(e.target.value),
                                            }))
                                          }
                                          className="h-8"
                                        />
                                      </div>
                                    </div>
                                    <div className="flex gap-2">
                                      <Button
                                        size="sm"
                                        onClick={() => saveProcess(i)}
                                        disabled={savingProcess}
                                      >
                                        {savingProcess ? (
                                          <>
                                            <span className="mr-1.5 h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
                                            保存中...
                                          </>
                                        ) : (
                                          "変更を保存する"
                                        )}
                                      </Button>
                                      <Button
                                        size="sm"
                                        variant="outline"
                                        onClick={cancelEditProcess}
                                        disabled={savingProcess}
                                      >
                                        キャンセル
                                      </Button>
                                    </div>
                                  </div>
                                </td>
                              ) : (
                                <>
                                  <td className="px-4 py-3 font-medium">{p.process_name}</td>
                                  <td className="px-4 py-3 text-muted-foreground">{p.equipment}</td>
                                  <td className="px-4 py-3 text-right">{p.setup_time_min}</td>
                                  <td className="px-4 py-3 text-right">{p.cycle_time_min}</td>
                                  <td className="px-4 py-3 text-right">¥{p.charge_rate.toLocaleString()}</td>
                                  <td className="px-4 py-3 text-right font-medium">
                                    {p.cost != null ? `¥${Math.round(p.cost).toLocaleString()}` : "—"}
                                  </td>
                                  <td className="px-4 py-3 text-right">
                                    <Button
                                      size="sm"
                                      variant="outline"
                                      onClick={() => startEditProcess(i, p)}
                                    >
                                      編集する
                                    </Button>
                                  </td>
                                </>
                              )}
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>

                {/* スマホ: カードリスト */}
                <div className="md:hidden space-y-3">
                  {quote.processes.map((p, i) => {
                    const isEditing = editingProcessIndex === i;
                    return (
                      <div key={i} className="rounded-md border p-3">
                        {isEditing ? (
                          <div className="space-y-3">
                            <div className="grid gap-3 grid-cols-1">
                              <div className="space-y-1">
                                <label className="text-xs text-muted-foreground">工程名</label>
                                <Input
                                  value={editProcessValues.process_name ?? ""}
                                  onChange={(e) =>
                                    setEditProcessValues((prev) => ({
                                      ...prev,
                                      process_name: e.target.value,
                                    }))
                                  }
                                  className="h-8"
                                />
                              </div>
                              <div className="space-y-1">
                                <label className="text-xs text-muted-foreground">設備</label>
                                <Input
                                  value={editProcessValues.equipment ?? ""}
                                  onChange={(e) =>
                                    setEditProcessValues((prev) => ({
                                      ...prev,
                                      equipment: e.target.value,
                                    }))
                                  }
                                  className="h-8"
                                />
                              </div>
                            </div>
                            <div className="grid grid-cols-3 gap-2">
                              <div className="space-y-1">
                                <label className="text-xs text-muted-foreground">段取（分）</label>
                                <Input
                                  type="number"
                                  min={0}
                                  value={editProcessValues.setup_time_min ?? 0}
                                  onChange={(e) =>
                                    setEditProcessValues((prev) => ({
                                      ...prev,
                                      setup_time_min: Number(e.target.value),
                                    }))
                                  }
                                  className="h-8"
                                />
                              </div>
                              <div className="space-y-1">
                                <label className="text-xs text-muted-foreground">サイクル（分）</label>
                                <Input
                                  type="number"
                                  min={0}
                                  value={editProcessValues.cycle_time_min ?? 0}
                                  onChange={(e) =>
                                    setEditProcessValues((prev) => ({
                                      ...prev,
                                      cycle_time_min: Number(e.target.value),
                                    }))
                                  }
                                  className="h-8"
                                />
                              </div>
                              <div className="space-y-1">
                                <label className="text-xs text-muted-foreground">チャージ（円/時）</label>
                                <Input
                                  type="number"
                                  min={0}
                                  value={editProcessValues.charge_rate ?? 0}
                                  onChange={(e) =>
                                    setEditProcessValues((prev) => ({
                                      ...prev,
                                      charge_rate: Number(e.target.value),
                                    }))
                                  }
                                  className="h-8"
                                />
                              </div>
                            </div>
                            <div className="flex gap-2">
                              <Button
                                size="sm"
                                onClick={() => saveProcess(i)}
                                disabled={savingProcess}
                              >
                                {savingProcess ? (
                                  <>
                                    <span className="mr-1.5 h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
                                    保存中...
                                  </>
                                ) : (
                                  "変更を保存する"
                                )}
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={cancelEditProcess}
                                disabled={savingProcess}
                              >
                                キャンセル
                              </Button>
                            </div>
                          </div>
                        ) : (
                          <div className="space-y-2">
                            <div className="flex items-center justify-between gap-2">
                              <p className="font-medium text-sm">{p.process_name}</p>
                              {p.cost != null && (
                                <span className="text-sm font-medium shrink-0">
                                  ¥{Math.round(p.cost).toLocaleString()}
                                </span>
                              )}
                            </div>
                            <div className="text-xs text-muted-foreground space-y-0.5">
                              <p>設備: {p.equipment}</p>
                              <p>
                                段取 {p.setup_time_min}分 / サイクル {p.cycle_time_min}分 / ¥{p.charge_rate.toLocaleString()}/時
                              </p>
                            </div>
                            <Button
                              size="sm"
                              variant="outline"
                              className="w-full"
                              onClick={() => startEditProcess(i, p)}
                            >
                              編集する
                            </Button>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </>
            )}
          </CardContent>
        </Card>

        {/* ステータス変更 */}
        {quote.status !== "won" && quote.status !== "lost" && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">ステータスを更新する</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-3">
                {quote.status === "draft" && (
                  <Button
                    variant="outline"
                    onClick={() => updateStatus("sent")}
                    disabled={updatingStatus}
                  >
                    {updatingStatus ? "更新中..." : "送付済みにする"}
                  </Button>
                )}
                <Button
                  onClick={() => requestStatusChange("won")}
                  disabled={updatingStatus}
                  className="bg-primary text-primary-foreground hover:bg-primary/90"
                >
                  {updatingStatus ? "更新中..." : "受注として記録する"}
                </Button>
                <Button
                  variant="outline"
                  className="text-destructive hover:text-destructive"
                  onClick={() => requestStatusChange("lost")}
                  disabled={updatingStatus}
                >
                  {updatingStatus ? "更新中..." : "失注として記録する"}
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* 受注後のネクストアクション */}
        {quote.status === "won" && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">次のアクション</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-3">
                <Button
                  onClick={() =>
                    router.push(`/bpo/manufacturing/new?${prefillQuery}`)
                  }
                >
                  この見積をベースに新規作成する
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* 失注後のネクストアクション */}
        {quote.status === "lost" && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">次のアクション</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-3">
                <Button
                  variant="outline"
                  onClick={() =>
                    router.push(`/bpo/manufacturing/new?${prefillQuery}`)
                  }
                >
                  修正して再見積する
                </Button>
                <Button
                  variant="outline"
                  className="text-muted-foreground"
                  onClick={() => router.push("/bpo/manufacturing")}
                >
                  アーカイブする
                </Button>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </>
  );
}
