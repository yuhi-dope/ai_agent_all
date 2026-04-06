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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

// ---------------------------------------------------------------------------
// 型定義
// ---------------------------------------------------------------------------

interface Quotation {
  id: string;
  opportunity_id: string;
  version: number;
  quotation_number: string;
  line_items: {
    module: string;
    unit_price: number;
    quantity: number;
    subtotal: number;
  }[];
  subtotal: number;
  tax: number;
  total: number;
  valid_until: string;
  pdf_storage_path: string | null;
  sent_at: string | null;
  sent_to: string | null;
  status: "draft" | "sent" | "accepted" | "rejected" | "expired";
  created_at: string;
}

interface Contract {
  id: string;
  opportunity_id: string;
  quotation_id: string | null;
  contract_number: string;
  contract_type: "subscription" | "spot" | "custom";
  start_date: string;
  end_date: string | null;
  monthly_amount: number;
  annual_amount: number;
  cloudsign_document_id: string | null;
  cloudsign_status: "not_sent" | "pending" | "signed" | "declined";
  signed_at: string | null;
  status: "draft" | "pending_signature" | "active" | "terminated";
  created_at: string;
}

// ---------------------------------------------------------------------------
// 定数
// ---------------------------------------------------------------------------

const QUOTATION_STATUS: Record<
  Quotation["status"],
  { label: string; className: string }
> = {
  draft: { label: "下書き", className: "bg-muted text-muted-foreground" },
  sent: { label: "送付済み", className: "bg-blue-100 text-blue-800" },
  accepted: { label: "承諾", className: "bg-green-100 text-green-800" },
  rejected: { label: "辞退", className: "bg-destructive/15 text-destructive" },
  expired: { label: "期限切れ", className: "bg-muted text-muted-foreground" },
};

const CONTRACT_STATUS: Record<
  Contract["status"],
  { label: string; className: string }
> = {
  draft: { label: "下書き", className: "bg-muted text-muted-foreground" },
  pending_signature: {
    label: "署名待ち",
    className: "bg-yellow-100 text-yellow-800",
  },
  active: { label: "有効", className: "bg-green-100 text-green-800" },
  terminated: {
    label: "終了",
    className: "bg-muted text-muted-foreground",
  },
};

const CLOUDSIGN_STATUS: Record<
  Contract["cloudsign_status"],
  { label: string; className: string }
> = {
  not_sent: { label: "未送信", className: "bg-muted text-muted-foreground" },
  pending: { label: "署名待ち", className: "bg-yellow-100 text-yellow-800" },
  signed: { label: "署名済み", className: "bg-green-100 text-green-800" },
  declined: {
    label: "却下",
    className: "bg-destructive/15 text-destructive",
  },
};

const CONTRACT_TYPE_LABELS: Record<Contract["contract_type"], string> = {
  subscription: "月額サブスクリプション",
  spot: "スポット",
  custom: "カスタム",
};

function formatAmount(yen: number): string {
  return `¥${yen.toLocaleString()}`;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("ja-JP", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

// ---------------------------------------------------------------------------
// スケルトン
// ---------------------------------------------------------------------------

function RowSkeleton() {
  return (
    <div className="flex flex-col gap-2 rounded-md border p-3 sm:flex-row sm:items-center">
      <div className="flex-1 space-y-1.5">
        <div className="h-4 w-40 animate-pulse rounded bg-muted" />
        <div className="h-3 w-28 animate-pulse rounded bg-muted" />
      </div>
      <div className="flex gap-2">
        <div className="h-5 w-14 animate-pulse rounded-full bg-muted" />
        <div className="h-7 w-20 animate-pulse rounded bg-muted" />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 見積書タブ
// ---------------------------------------------------------------------------

function QuotationTab({
  quotations,
  loading,
  error,
  token,
  onStatusChange,
}: {
  quotations: Quotation[];
  loading: boolean;
  error: string | null;
  token: string;
  onStatusChange: (id: string, status: Quotation["status"]) => void;
}) {
  const [actionId, setActionId] = useState<string | null>(null);

  async function handleAction(q: Quotation, action: "send" | "accept") {
    setActionId(q.id);
    try {
      if (action === "send") {
        await apiFetch(`/sales/quotations/${q.id}/send`, {
          token,
          method: "POST",
        });
        onStatusChange(q.id, "sent");
      } else {
        await apiFetch(`/sales/quotations/${q.id}`, {
          token,
          method: "PATCH",
          body: { status: "accepted" },
        });
        onStatusChange(q.id, "accepted");
      }
    } catch {
      // エラーは親で表示
    } finally {
      setActionId(null);
    }
  }

  if (loading) {
    return (
      <div className="space-y-2">
        {[1, 2, 3].map((i) => (
          <RowSkeleton key={i} />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-md border border-destructive/50 bg-destructive/5 p-3">
        <p className="text-sm text-destructive">{error}</p>
      </div>
    );
  }

  if (quotations.length === 0) {
    return (
      <div className="flex flex-col items-center gap-4 py-10 text-center">
        <p className="text-sm text-muted-foreground">
          まだ見積書がありません。
        </p>
        <p className="text-xs text-muted-foreground">
          商談管理で「見積提示」ステージに進めると見積書が自動生成されます。
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {quotations.map((q) => {
        const statusCfg = QUOTATION_STATUS[q.status];
        const isExpired =
          q.status === "sent" && new Date(q.valid_until) < new Date();
        return (
          <div
            key={q.id}
            className="flex flex-col gap-2 rounded-md border p-3 sm:flex-row sm:items-center"
          >
            <div className="flex-1 space-y-1 min-w-0">
              <div className="flex items-center gap-2">
                <p className="text-sm font-medium">{q.quotation_number}</p>
                <span className="text-xs text-muted-foreground">
                  v{q.version}
                </span>
              </div>
              <p className="text-base font-semibold">
                {formatAmount(q.total)}/月
              </p>
              <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                <span>作成: {formatDate(q.created_at)}</span>
                <span>有効期限: {formatDate(q.valid_until)}</span>
                {q.sent_to && <span>送付先: {q.sent_to}</span>}
              </div>
              {/* 明細 */}
              <div className="mt-1 space-y-0.5">
                {q.line_items.map((item, idx) => (
                  <p key={idx} className="text-xs text-muted-foreground">
                    {item.module}: {formatAmount(item.unit_price)} × {item.quantity}
                  </p>
                ))}
              </div>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              {isExpired ? (
                <Badge className="bg-muted text-muted-foreground text-[11px]">
                  期限切れ
                </Badge>
              ) : (
                <Badge className={`text-[11px] ${statusCfg.className}`}>
                  {statusCfg.label}
                </Badge>
              )}
              {q.pdf_storage_path && (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs"
                  onClick={() =>
                    window.open(
                      `/api/v1/sales/quotations/${q.id}/pdf`,
                      "_blank"
                    )
                  }
                >
                  PDFを開く
                </Button>
              )}
              {q.status === "draft" && (
                <Button
                  size="sm"
                  className="h-7 text-xs"
                  disabled={actionId === q.id}
                  onClick={() => handleAction(q, "send")}
                >
                  {actionId === q.id ? (
                    <>
                      <span className="mr-1.5 h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
                      送付中...
                    </>
                  ) : (
                    "送付する"
                  )}
                </Button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 契約書タブ
// ---------------------------------------------------------------------------

function ContractTab({
  contracts,
  loading,
  error,
  token,
  onSendSign,
}: {
  contracts: Contract[];
  loading: boolean;
  error: string | null;
  token: string;
  onSendSign: (contractId: string) => void;
}) {
  const [actionId, setActionId] = useState<string | null>(null);

  async function handleSendSign(c: Contract) {
    setActionId(c.id);
    try {
      await apiFetch(`/sales/contracts/${c.id}/send-sign`, {
        token,
        method: "POST",
      });
      onSendSign(c.id);
    } catch {
      // エラーは親で表示
    } finally {
      setActionId(null);
    }
  }

  if (loading) {
    return (
      <div className="space-y-2">
        {[1, 2, 3].map((i) => (
          <RowSkeleton key={i} />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-md border border-destructive/50 bg-destructive/5 p-3">
        <p className="text-sm text-destructive">{error}</p>
      </div>
    );
  }

  if (contracts.length === 0) {
    return (
      <div className="flex flex-col items-center gap-4 py-10 text-center">
        <p className="text-sm text-muted-foreground">
          まだ契約書がありません。
        </p>
        <p className="text-xs text-muted-foreground">
          見積書が承諾されると契約書が自動生成されます。
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {contracts.map((c) => {
        const statusCfg = CONTRACT_STATUS[c.status];
        const signCfg = CLOUDSIGN_STATUS[c.cloudsign_status];
        return (
          <div
            key={c.id}
            className="flex flex-col gap-2 rounded-md border p-3 sm:flex-row sm:items-center"
          >
            <div className="flex-1 space-y-1 min-w-0">
              <p className="text-sm font-medium">{c.contract_number}</p>
              <p className="text-xs text-muted-foreground">
                {CONTRACT_TYPE_LABELS[c.contract_type]}
              </p>
              <p className="text-base font-semibold">
                {formatAmount(c.monthly_amount)}/月
              </p>
              <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                <span>開始: {formatDate(c.start_date)}</span>
                {c.end_date && <span>終了: {formatDate(c.end_date)}</span>}
                {c.signed_at && (
                  <span className="text-green-700">
                    署名: {formatDate(c.signed_at)}
                  </span>
                )}
              </div>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <Badge className={`text-[11px] ${statusCfg.className}`}>
                {statusCfg.label}
              </Badge>
              <Badge className={`text-[11px] ${signCfg.className}`}>
                署名: {signCfg.label}
              </Badge>
              {c.status === "draft" && (
                <Button
                  size="sm"
                  className="h-7 text-xs"
                  disabled={actionId === c.id}
                  onClick={() => handleSendSign(c)}
                >
                  {actionId === c.id ? (
                    <>
                      <span className="mr-1.5 h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
                      送付中...
                    </>
                  ) : (
                    "電子署名を依頼する"
                  )}
                </Button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ページ
// ---------------------------------------------------------------------------

export default function ContractsPage() {
  const { session } = useAuth();
  const [quotations, setQuotations] = useState<Quotation[]>([]);
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [loadingQ, setLoadingQ] = useState(true);
  const [loadingC, setLoadingC] = useState(true);
  const [errorQ, setErrorQ] = useState<string | null>(null);
  const [errorC, setErrorC] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    if (!session?.access_token) return;
    const token = session.access_token;

    setLoadingQ(true);
    setLoadingC(true);
    setErrorQ(null);
    setErrorC(null);

    apiFetch<{ items: Quotation[]; total: number }>("/sales/quotations", {
      token,
      params: { limit: "100" },
    })
      .then((res) => setQuotations(res.items))
      .catch(() =>
        setErrorQ(
          "見積書の取得に失敗しました。しばらく経ってから再度お試しください。"
        )
      )
      .finally(() => setLoadingQ(false));

    apiFetch<{ items: Contract[]; total: number }>("/sales/contracts", {
      token,
      params: { limit: "100" },
    })
      .then((res) => setContracts(res.items))
      .catch(() =>
        setErrorC(
          "契約書の取得に失敗しました。しばらく経ってから再度お試しください。"
        )
      )
      .finally(() => setLoadingC(false));
  }, [session?.access_token]);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  function handleQuotationStatusChange(id: string, status: Quotation["status"]) {
    setQuotations((prev) =>
      prev.map((q) => (q.id === id ? { ...q, status } : q))
    );
    if (status === "sent") {
      setSuccessMsg("見積書を送付しました。");
      setTimeout(() => setSuccessMsg(null), 5000);
    }
  }

  function handleSendSign(contractId: string) {
    setContracts((prev) =>
      prev.map((c) =>
        c.id === contractId
          ? { ...c, status: "pending_signature", cloudsign_status: "pending" }
          : c
      )
    );
    setSuccessMsg("電子署名の依頼を送付しました。署名完了後にステータスが更新されます。");
    setTimeout(() => setSuccessMsg(null), 6000);
  }

  const activeContracts = contracts.filter((c) => c.status === "active");
  const totalMRR = activeContracts.reduce(
    (sum, c) => sum + c.monthly_amount,
    0
  );

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      {/* ヘッダー */}
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold">見積・契約管理</h1>
          <p className="text-sm text-muted-foreground">
            見積書の送付・承諾管理と、契約書の電子署名状況を確認できます。
          </p>
        </div>
        <div className="flex flex-col items-end gap-0.5">
          <p className="text-xs text-muted-foreground">有効契約 月額合計</p>
          <p className="text-xl font-bold text-primary">
            {formatAmount(totalMRR)}/月
          </p>
        </div>
      </div>

      {/* 成功メッセージ */}
      {successMsg && (
        <div className="rounded-md border border-green-200 bg-green-50 p-3">
          <p className="text-sm text-green-800">{successMsg}</p>
        </div>
      )}

      {/* タブ */}
      <Tabs defaultValue="quotations">
        <TabsList className="w-full sm:w-auto">
          <TabsTrigger value="quotations" className="flex-1 sm:flex-none">
            見積書
            {quotations.length > 0 && (
              <Badge variant="secondary" className="ml-1.5 text-[11px]">
                {quotations.length}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="contracts" className="flex-1 sm:flex-none">
            契約書
            {contracts.length > 0 && (
              <Badge variant="secondary" className="ml-1.5 text-[11px]">
                {contracts.length}
              </Badge>
            )}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="quotations" className="mt-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base font-semibold">
                見積書一覧
              </CardTitle>
            </CardHeader>
            <CardContent>
              <QuotationTab
                quotations={quotations}
                loading={loadingQ}
                error={errorQ}
                token={session?.access_token ?? ""}
                onStatusChange={handleQuotationStatusChange}
              />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="contracts" className="mt-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base font-semibold">
                契約書一覧
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ContractTab
                contracts={contracts}
                loading={loadingC}
                error={errorC}
                token={session?.access_token ?? ""}
                onSendSign={handleSendSign}
              />
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
