"use client";

import { useState, useCallback } from "react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

// ---------------------------------------------------------------------------
// 型定義
// ---------------------------------------------------------------------------

interface AccountingSummary {
  ar_balance: number; // 売掛残高
  ap_balance: number; // 買掛残高
  bank_balance: number; // 銀行残高
  monthly_revenue: number; // 今月請求額
}

interface Invoice {
  id: string;
  client_name: string;
  amount: number;
  issue_date: string;
  due_date: string;
  status: "pending_approval" | "unpaid" | "paid";
}

interface BankRecord {
  date: string;
  description: string;
  credit: number;
  debit: number;
  reconciled: boolean;
}

interface JournalEntry {
  id: string;
  date: string;
  debit_account: string;
  credit_account: string;
  amount: number;
  description: string;
  confirmed: boolean;
}

// ---------------------------------------------------------------------------
// モックデータ
// ---------------------------------------------------------------------------

const MOCK_SUMMARY: AccountingSummary = {
  ar_balance: 4_250_000,
  ap_balance: 1_830_000,
  bank_balance: 8_920_000,
  monthly_revenue: 6_100_000,
};

const MOCK_INVOICES: Invoice[] = [
  {
    id: "inv-001",
    client_name: "株式会社山田建設",
    amount: 1_320_000,
    issue_date: "2026-04-01",
    due_date: "2026-04-30",
    status: "unpaid",
  },
  {
    id: "inv-002",
    client_name: "田中製造株式会社",
    amount: 880_000,
    issue_date: "2026-03-25",
    due_date: "2026-04-25",
    status: "paid",
  },
  {
    id: "inv-003",
    client_name: "佐藤物流有限会社",
    amount: 540_000,
    issue_date: "2026-04-05",
    due_date: "2026-05-05",
    status: "pending_approval",
  },
  {
    id: "inv-004",
    client_name: "鈴木不動産株式会社",
    amount: 1_050_000,
    issue_date: "2026-03-31",
    due_date: "2026-04-30",
    status: "unpaid",
  },
];

const MOCK_BANK_RECORDS: BankRecord[] = [
  {
    date: "2026-04-01",
    description: "株式会社山田建設 入金",
    credit: 1_320_000,
    debit: 0,
    reconciled: true,
  },
  {
    date: "2026-04-02",
    description: "クラウド会計ソフト 月額利用料",
    credit: 0,
    debit: 12_000,
    reconciled: true,
  },
  {
    date: "2026-04-03",
    description: "原因不明の入金",
    credit: 50_000,
    debit: 0,
    reconciled: false,
  },
  {
    date: "2026-04-04",
    description: "事務所賃料 4月分",
    credit: 0,
    debit: 250_000,
    reconciled: true,
  },
  {
    date: "2026-04-05",
    description: "田中製造株式会社 入金",
    credit: 880_000,
    debit: 0,
    reconciled: true,
  },
];

const MOCK_JOURNAL_ENTRIES: JournalEntry[] = [
  {
    id: "jnl-001",
    date: "2026-04-01",
    debit_account: "普通預金",
    credit_account: "売掛金",
    amount: 1_320_000,
    description: "山田建設 4月入金",
    confirmed: false,
  },
  {
    id: "jnl-002",
    date: "2026-04-02",
    debit_account: "支払手数料",
    credit_account: "普通預金",
    amount: 12_000,
    description: "クラウド会計ソフト利用料",
    confirmed: true,
  },
  {
    id: "jnl-003",
    date: "2026-04-04",
    debit_account: "地代家賃",
    credit_account: "普通預金",
    amount: 250_000,
    description: "事務所賃料 4月分",
    confirmed: false,
  },
  {
    id: "jnl-004",
    date: "2026-04-05",
    debit_account: "普通預金",
    credit_account: "売掛金",
    amount: 880_000,
    description: "田中製造 4月入金",
    confirmed: false,
  },
];

// ---------------------------------------------------------------------------
// ステータス表示ヘルパー
// ---------------------------------------------------------------------------

function InvoiceStatusBadge({ status }: { status: Invoice["status"] }) {
  if (status === "paid") {
    return (
      <Badge className="bg-green-100 text-green-800">入金済み</Badge>
    );
  }
  if (status === "pending_approval") {
    return (
      <Badge className="bg-yellow-100 text-yellow-800">承認待ち</Badge>
    );
  }
  return <Badge variant="destructive">未払い</Badge>;
}

// ---------------------------------------------------------------------------
// メインページ
// ---------------------------------------------------------------------------

export default function AccountingPage() {
  const { session } = useAuth();

  // 月次サマリー
  const [summary] = useState<AccountingSummary>(MOCK_SUMMARY);

  // 月次決算
  const [monthlyCloseLoading, setMonthlyCloseLoading] = useState(false);
  const [monthlyCloseApprovalPending, setMonthlyCloseApprovalPending] =
    useState(false);
  const [monthlyCloseError, setMonthlyCloseError] = useState<string | null>(
    null
  );

  // 請求書
  const [invoices, setInvoices] = useState<Invoice[]>(MOCK_INVOICES);
  const [invoiceIssueLoading, setInvoiceIssueLoading] = useState(false);
  const [invoiceIssueError, setInvoiceIssueError] = useState<string | null>(
    null
  );
  const [invoiceIssueSuccess, setInvoiceIssueSuccess] = useState(false);

  // 銀行照合
  const [bankRecords] = useState<BankRecord[]>(MOCK_BANK_RECORDS);
  const [reconcileLoading, setReconcileLoading] = useState(false);
  const [reconcileError, setReconcileError] = useState<string | null>(null);
  const [reconcileSuccess, setReconcileSuccess] = useState(false);

  // 仕訳
  const [journalEntries, setJournalEntries] =
    useState<JournalEntry[]>(MOCK_JOURNAL_ENTRIES);

  // ---------------------------------------------------------------------------
  // 月次決算実行
  // ---------------------------------------------------------------------------
  const handleMonthlyClose = useCallback(async () => {
    setMonthlyCloseLoading(true);
    setMonthlyCloseError(null);
    try {
      await apiFetch("/bpo/backoffice/monthly-close", {
        method: "POST",
        token: session?.access_token,
      });
      setMonthlyCloseApprovalPending(true);
    } catch {
      setMonthlyCloseError(
        "月次決算の実行に失敗しました。しばらく経ってから再度お試しください。"
      );
    } finally {
      setMonthlyCloseLoading(false);
    }
  }, [session]);

  // ---------------------------------------------------------------------------
  // 請求書発行
  // ---------------------------------------------------------------------------
  const handleInvoiceIssue = useCallback(async () => {
    setInvoiceIssueLoading(true);
    setInvoiceIssueError(null);
    setInvoiceIssueSuccess(false);
    try {
      await apiFetch("/bpo/backoffice/invoice-issue", {
        method: "POST",
        token: session?.access_token,
      });
      setInvoiceIssueSuccess(true);
      // モックとして新規請求書を追加
      const newInvoice: Invoice = {
        id: `inv-${Date.now()}`,
        client_name: "新規取引先",
        amount: 500_000,
        issue_date: new Date().toISOString().slice(0, 10),
        due_date: new Date(Date.now() + 30 * 86_400_000)
          .toISOString()
          .slice(0, 10),
        status: "pending_approval",
      };
      setInvoices((prev) => [newInvoice, ...prev]);
    } catch {
      setInvoiceIssueError(
        "請求書の発行に失敗しました。入力内容を確認してもう一度お試しください。"
      );
    } finally {
      setInvoiceIssueLoading(false);
    }
  }, [session]);

  // ---------------------------------------------------------------------------
  // 銀行照合実行
  // ---------------------------------------------------------------------------
  const handleReconcile = useCallback(async () => {
    setReconcileLoading(true);
    setReconcileError(null);
    setReconcileSuccess(false);
    try {
      await apiFetch("/bpo/backoffice/bank-reconciliation", {
        method: "POST",
        token: session?.access_token,
      });
      setReconcileSuccess(true);
    } catch {
      setReconcileError(
        "銀行照合の実行に失敗しました。しばらく経ってから再度お試しください。"
      );
    } finally {
      setReconcileLoading(false);
    }
  }, [session]);

  // ---------------------------------------------------------------------------
  // 仕訳の確定
  // ---------------------------------------------------------------------------
  const handleConfirmJournal = useCallback((id: string) => {
    setJournalEntries((prev) =>
      prev.map((entry) =>
        entry.id === id ? { ...entry, confirmed: true } : entry
      )
    );
  }, []);

  const allJournalsConfirmed = journalEntries.every((e) => e.confirmed);

  return (
    <div className="p-4 sm:p-6 max-w-5xl mx-auto">
      {/* ページヘッダー */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">経理・財務</h1>
        <p className="text-sm text-gray-500 mt-1">
          請求・入金・仕訳・月次決算を自動化します
        </p>
      </div>

      {/* 承認待ちバナー */}
      {monthlyCloseApprovalPending && (
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4 mb-6 flex flex-col sm:flex-row sm:items-center gap-2">
          <span className="text-yellow-800 text-sm font-medium">
            月次決算を実行しました。担当者の承認をお待ちください。
          </span>
          <Button
            variant="outline"
            size="sm"
            className="sm:ml-auto"
            onClick={() => setMonthlyCloseApprovalPending(false)}
          >
            閉じる
          </Button>
        </div>
      )}

      <Tabs defaultValue="summary">
        <TabsList className="mb-6 flex flex-wrap gap-1 h-auto">
          <TabsTrigger value="summary">月次サマリー</TabsTrigger>
          <TabsTrigger value="invoices">請求書</TabsTrigger>
          <TabsTrigger value="bank">銀行照合</TabsTrigger>
          <TabsTrigger value="journal">仕訳確認</TabsTrigger>
        </TabsList>

        {/* ================================================================
            タブ1: 月次サマリー
        ================================================================ */}
        <TabsContent value="summary">
          {/* 数字カード */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>売掛残高（未収金合計）</CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-bold text-gray-900">
                  ¥{summary.ar_balance.toLocaleString()}
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>買掛残高（未払金合計）</CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-bold text-gray-900">
                  ¥{summary.ap_balance.toLocaleString()}
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>銀行残高（照合済み）</CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-bold text-gray-900">
                  ¥{summary.bank_balance.toLocaleString()}
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>今月の請求総額</CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-bold text-gray-900">
                  ¥{summary.monthly_revenue.toLocaleString()}
                </p>
              </CardContent>
            </Card>
          </div>

          {/* 今月のステータステーブル */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base font-medium">
                今月のステータス
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              {/* PC向けテーブル */}
              <div className="hidden sm:block overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>処理</TableHead>
                      <TableHead>ステータス</TableHead>
                      <TableHead>実行日</TableHead>
                      <TableHead>アクション</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    <TableRow>
                      <TableCell className="font-medium">請求書発行</TableCell>
                      <TableCell>
                        <Badge className="bg-yellow-100 text-yellow-800">
                          承認待ち
                        </Badge>
                      </TableCell>
                      <TableCell className="text-sm text-gray-600">
                        月末
                      </TableCell>
                      <TableCell>
                        <Button variant="outline" size="sm">
                          内容を確認する
                        </Button>
                      </TableCell>
                    </TableRow>
                    <TableRow>
                      <TableCell className="font-medium">月次決算</TableCell>
                      <TableCell>
                        <Badge variant="secondary">未実行</Badge>
                      </TableCell>
                      <TableCell className="text-sm text-gray-600">
                        翌月5日
                      </TableCell>
                      <TableCell>
                        <Button
                          size="sm"
                          onClick={handleMonthlyClose}
                          disabled={monthlyCloseLoading}
                        >
                          {monthlyCloseLoading ? (
                            <>
                              <span className="mr-2 h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
                              実行中...
                            </>
                          ) : (
                            "月次決算を実行する"
                          )}
                        </Button>
                      </TableCell>
                    </TableRow>
                    <TableRow>
                      <TableCell className="font-medium">銀行照合</TableCell>
                      <TableCell>
                        <Badge className="bg-green-100 text-green-800">
                          完了
                        </Badge>
                      </TableCell>
                      <TableCell className="text-sm text-gray-600">
                        毎日自動
                      </TableCell>
                      <TableCell>
                        <Button variant="outline" size="sm">
                          詳細を見る
                        </Button>
                      </TableCell>
                    </TableRow>
                  </TableBody>
                </Table>
              </div>

              {/* スマホ向けカードリスト */}
              <div className="sm:hidden divide-y">
                {[
                  {
                    label: "請求書発行",
                    statusEl: (
                      <Badge className="bg-yellow-100 text-yellow-800">
                        承認待ち
                      </Badge>
                    ),
                    date: "月末",
                    actionEl: (
                      <Button variant="outline" size="sm" className="w-full">
                        内容を確認する
                      </Button>
                    ),
                  },
                  {
                    label: "月次決算",
                    statusEl: <Badge variant="secondary">未実行</Badge>,
                    date: "翌月5日",
                    actionEl: (
                      <Button
                        size="sm"
                        className="w-full"
                        onClick={handleMonthlyClose}
                        disabled={monthlyCloseLoading}
                      >
                        {monthlyCloseLoading ? (
                          <>
                            <span className="mr-2 h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
                            実行中...
                          </>
                        ) : (
                          "月次決算を実行する"
                        )}
                      </Button>
                    ),
                  },
                  {
                    label: "銀行照合",
                    statusEl: (
                      <Badge className="bg-green-100 text-green-800">
                        完了
                      </Badge>
                    ),
                    date: "毎日自動",
                    actionEl: (
                      <Button
                        variant="outline"
                        size="sm"
                        className="w-full"
                      >
                        詳細を見る
                      </Button>
                    ),
                  },
                ].map((item) => (
                  <div key={item.label} className="p-4 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-sm">{item.label}</span>
                      {item.statusEl}
                    </div>
                    <p className="text-xs text-gray-500">実行日: {item.date}</p>
                    {item.actionEl}
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* エラー表示 */}
          {monthlyCloseError && (
            <div className="mt-4 bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-700">
              {monthlyCloseError}
            </div>
          )}

          {/* 月次決算ボタン（カード下部） */}
          <div className="mt-4 flex justify-end">
            <Button
              size="lg"
              onClick={handleMonthlyClose}
              disabled={monthlyCloseLoading}
              className="w-full sm:w-auto"
            >
              {monthlyCloseLoading ? (
                <>
                  <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  実行中...
                </>
              ) : (
                "月次決算を実行する"
              )}
            </Button>
          </div>
        </TabsContent>

        {/* ================================================================
            タブ2: 請求書
        ================================================================ */}
        <TabsContent value="invoices">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
            <h2 className="text-lg font-semibold">請求書一覧</h2>
            <Button
              onClick={handleInvoiceIssue}
              disabled={invoiceIssueLoading}
              className="w-full sm:w-auto"
            >
              {invoiceIssueLoading ? (
                <>
                  <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  発行中...
                </>
              ) : (
                "請求書を発行する"
              )}
            </Button>
          </div>

          {invoiceIssueSuccess && (
            <div className="mb-4 bg-green-50 border border-green-200 rounded-lg p-4 text-sm text-green-700">
              請求書を発行しました。承認後に取引先へ送付されます。
            </div>
          )}
          {invoiceIssueError && (
            <div className="mb-4 bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-700">
              {invoiceIssueError}
            </div>
          )}

          {invoices.length === 0 ? (
            <Card>
              <CardContent className="flex flex-col items-center gap-4 py-12">
                <p className="text-sm text-gray-500">
                  まだ請求書がありません
                </p>
                <Button onClick={handleInvoiceIssue} className="w-full sm:w-auto">
                  はじめての請求書を発行する
                </Button>
              </CardContent>
            </Card>
          ) : (
            <>
              {/* PC向けテーブル */}
              <div className="hidden sm:block overflow-x-auto">
                <Card>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>取引先</TableHead>
                        <TableHead>金額</TableHead>
                        <TableHead>発行日</TableHead>
                        <TableHead>支払期限</TableHead>
                        <TableHead>ステータス</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {invoices.map((invoice) => (
                        <TableRow key={invoice.id}>
                          <TableCell className="font-medium">
                            {invoice.client_name}
                          </TableCell>
                          <TableCell>
                            ¥{invoice.amount.toLocaleString()}
                          </TableCell>
                          <TableCell className="text-sm text-gray-600">
                            {invoice.issue_date}
                          </TableCell>
                          <TableCell className="text-sm text-gray-600">
                            {invoice.due_date}
                          </TableCell>
                          <TableCell>
                            <InvoiceStatusBadge status={invoice.status} />
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </Card>
              </div>

              {/* スマホ向けカードリスト */}
              <div className="sm:hidden space-y-3">
                {invoices.map((invoice) => (
                  <Card key={invoice.id}>
                    <CardContent className="pt-4 space-y-2">
                      <div className="flex items-start justify-between gap-2">
                        <span className="font-medium text-sm">
                          {invoice.client_name}
                        </span>
                        <InvoiceStatusBadge status={invoice.status} />
                      </div>
                      <p className="text-2xl font-bold">
                        ¥{invoice.amount.toLocaleString()}
                      </p>
                      <div className="flex gap-4 text-xs text-gray-500">
                        <span>発行日: {invoice.issue_date}</span>
                        <span>期限: {invoice.due_date}</span>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </>
          )}
        </TabsContent>

        {/* ================================================================
            タブ3: 銀行照合
        ================================================================ */}
        <TabsContent value="bank">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
            <div>
              <h2 className="text-lg font-semibold">当月の入出金明細</h2>
              <p className="text-xs text-gray-500 mt-0.5">
                赤色の行は照合が取れていない取引です。内容を確認してください。
              </p>
            </div>
            <Button
              onClick={handleReconcile}
              disabled={reconcileLoading}
              className="w-full sm:w-auto"
            >
              {reconcileLoading ? (
                <>
                  <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  照合中...
                </>
              ) : (
                "照合を実行する"
              )}
            </Button>
          </div>

          {reconcileSuccess && (
            <div className="mb-4 bg-green-50 border border-green-200 rounded-lg p-4 text-sm text-green-700">
              銀行照合が完了しました。未照合の取引を確認してください。
            </div>
          )}
          {reconcileError && (
            <div className="mb-4 bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-700">
              {reconcileError}
            </div>
          )}

          {/* PC向けテーブル */}
          <div className="hidden sm:block overflow-x-auto">
            <Card>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>日付</TableHead>
                    <TableHead>摘要</TableHead>
                    <TableHead className="text-right">入金額</TableHead>
                    <TableHead className="text-right">出金額</TableHead>
                    <TableHead>照合状況</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {bankRecords.map((record, index) => (
                    <TableRow
                      key={index}
                      className={!record.reconciled ? "bg-red-50" : ""}
                    >
                      <TableCell className="text-sm text-gray-600">
                        {record.date}
                      </TableCell>
                      <TableCell className="font-medium">
                        {record.description}
                      </TableCell>
                      <TableCell className="text-right text-sm">
                        {record.credit > 0
                          ? `¥${record.credit.toLocaleString()}`
                          : "—"}
                      </TableCell>
                      <TableCell className="text-right text-sm">
                        {record.debit > 0
                          ? `¥${record.debit.toLocaleString()}`
                          : "—"}
                      </TableCell>
                      <TableCell>
                        {record.reconciled ? (
                          <Badge className="bg-green-100 text-green-800">
                            照合済み
                          </Badge>
                        ) : (
                          <Badge variant="destructive">未照合</Badge>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Card>
          </div>

          {/* スマホ向けカードリスト */}
          <div className="sm:hidden space-y-3">
            {bankRecords.map((record, index) => (
              <Card
                key={index}
                className={!record.reconciled ? "border-red-200 bg-red-50" : ""}
              >
                <CardContent className="pt-4 space-y-2">
                  <div className="flex items-start justify-between gap-2">
                    <span className="font-medium text-sm">
                      {record.description}
                    </span>
                    {record.reconciled ? (
                      <Badge className="bg-green-100 text-green-800 shrink-0">
                        照合済み
                      </Badge>
                    ) : (
                      <Badge variant="destructive" className="shrink-0">
                        未照合
                      </Badge>
                    )}
                  </div>
                  <p className="text-xs text-gray-500">{record.date}</p>
                  <div className="flex gap-4 text-sm">
                    {record.credit > 0 && (
                      <span className="text-green-700 font-medium">
                        入金 ¥{record.credit.toLocaleString()}
                      </span>
                    )}
                    {record.debit > 0 && (
                      <span className="text-red-700 font-medium">
                        出金 ¥{record.debit.toLocaleString()}
                      </span>
                    )}
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </TabsContent>

        {/* ================================================================
            タブ4: 仕訳確認
        ================================================================ */}
        <TabsContent value="journal">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
            <div>
              <h2 className="text-lg font-semibold">AI作成の仕訳一覧</h2>
              <p className="text-xs text-gray-500 mt-0.5">
                AIが自動で作成した仕訳です。内容を確認して「確定する」を押してください。
              </p>
            </div>
            <Button
              disabled={!allJournalsConfirmed}
              className="w-full sm:w-auto"
              onClick={() => setMonthlyCloseApprovalPending(true)}
            >
              月次決算へ進む
            </Button>
          </div>

          {!allJournalsConfirmed && (
            <div className="mb-4 bg-yellow-50 border border-yellow-200 rounded-lg p-4 text-sm text-yellow-800">
              すべての仕訳を確定してから月次決算へ進んでください。
            </div>
          )}

          {journalEntries.length === 0 ? (
            <Card>
              <CardContent className="flex flex-col items-center gap-4 py-12">
                <p className="text-sm text-gray-500">
                  今月の仕訳データがまだありません
                </p>
                <p className="text-xs text-gray-400">
                  銀行照合が完了すると自動で仕訳が作成されます
                </p>
              </CardContent>
            </Card>
          ) : (
            <>
              {/* PC向けテーブル */}
              <div className="hidden sm:block overflow-x-auto">
                <Card>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>日付</TableHead>
                        <TableHead>借方科目</TableHead>
                        <TableHead>貸方科目</TableHead>
                        <TableHead className="text-right">金額</TableHead>
                        <TableHead>摘要</TableHead>
                        <TableHead>操作</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {journalEntries.map((entry) => (
                        <TableRow key={entry.id}>
                          <TableCell className="text-sm text-gray-600">
                            {entry.date}
                          </TableCell>
                          <TableCell className="font-medium">
                            {entry.debit_account}
                          </TableCell>
                          <TableCell className="font-medium">
                            {entry.credit_account}
                          </TableCell>
                          <TableCell className="text-right text-sm">
                            ¥{entry.amount.toLocaleString()}
                          </TableCell>
                          <TableCell className="text-sm text-gray-600">
                            {entry.description}
                          </TableCell>
                          <TableCell>
                            {entry.confirmed ? (
                              <Badge className="bg-green-100 text-green-800">
                                確定済み
                              </Badge>
                            ) : (
                              <div className="flex gap-2">
                                <Button
                                  size="sm"
                                  onClick={() => handleConfirmJournal(entry.id)}
                                >
                                  確定する
                                </Button>
                                <Button variant="outline" size="sm">
                                  修正する
                                </Button>
                              </div>
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </Card>
              </div>

              {/* スマホ向けカードリスト */}
              <div className="sm:hidden space-y-3">
                {journalEntries.map((entry) => (
                  <Card key={entry.id}>
                    <CardContent className="pt-4 space-y-3">
                      <div className="flex items-start justify-between gap-2">
                        <div className="space-y-0.5">
                          <p className="text-sm font-medium">
                            {entry.debit_account} ／ {entry.credit_account}
                          </p>
                          <p className="text-xs text-gray-500">
                            {entry.date} · {entry.description}
                          </p>
                        </div>
                        <p className="text-sm font-bold shrink-0">
                          ¥{entry.amount.toLocaleString()}
                        </p>
                      </div>
                      {entry.confirmed ? (
                        <Badge className="bg-green-100 text-green-800">
                          確定済み
                        </Badge>
                      ) : (
                        <div className="flex gap-2">
                          <Button
                            size="sm"
                            className="flex-1"
                            onClick={() => handleConfirmJournal(entry.id)}
                          >
                            確定する
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            className="flex-1"
                          >
                            修正する
                          </Button>
                        </div>
                      )}
                    </CardContent>
                  </Card>
                ))}
              </div>
            </>
          )}

          {/* 月次決算へ進むボタン（下部） */}
          <div className="mt-6 flex justify-end">
            <Button
              size="lg"
              disabled={!allJournalsConfirmed}
              className="w-full sm:w-auto"
              onClick={() => setMonthlyCloseApprovalPending(true)}
            >
              月次決算へ進む
            </Button>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
