"use client";

import { useState } from "react";
import Link from "next/link";
import { Plus, Trash2, ChevronDown, ChevronUp, AlertTriangle, Info, CheckCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { apiFetch } from "@/lib/api";

// ---------- 型定義 ----------

interface JournalEntry {
  id: string;
  date: string;
  debit_account: string;
  credit_account: string;
  amount: string;
  description: string;
  tax_category: string;
  invoice_number: string;
}

interface CheckIssue {
  level: "error" | "warning" | "info";
  code: string;
  message: string;
  entry_index?: number;
  field?: string;
  suggestion?: string;
}

interface BookkeepingCheckResult {
  success: boolean;
  company_name: string;
  period: string;
  total_entries: number;
  issues: CheckIssue[];
  summary: {
    error_count: number;
    warning_count: number;
    info_count: number;
    debit_total: number;
    credit_total: number;
    is_balanced: boolean;
  };
  error?: string;
}

// ---------- 消費税区分 ----------

const TAX_CATEGORIES = [
  { value: "taxable_10", label: "課税10%" },
  { value: "taxable_8", label: "軽減税率8%" },
  { value: "exempt", label: "非課税" },
  { value: "excluded", label: "対象外" },
  { value: "free_export", label: "輸出免税" },
];

// ---------- よく使う勘定科目 ----------

const COMMON_ACCOUNTS = [
  "現金", "普通預金", "当座預金", "売掛金", "買掛金",
  "売上高", "仕入高", "給料手当", "地代家賃", "交際費",
  "消耗品費", "通信費", "水道光熱費", "旅費交通費", "広告宣伝費",
  "減価償却費", "支払利息", "受取利息", "未払金", "前払費用",
  "仮払消費税", "仮受消費税",
];

// ---------- 新規仕訳行の生成 ----------

function newEntry(): JournalEntry {
  return {
    id: Math.random().toString(36).slice(2),
    date: "",
    debit_account: "",
    credit_account: "",
    amount: "",
    description: "",
    tax_category: "taxable_10",
    invoice_number: "",
  };
}

// ---------- 問題レベルのスタイル ----------

function IssueBadge({ level }: { level: CheckIssue["level"] }) {
  if (level === "error")
    return <Badge variant="destructive">エラー</Badge>;
  if (level === "warning")
    return <Badge className="bg-yellow-100 text-yellow-800">注意</Badge>;
  return <Badge variant="secondary">情報</Badge>;
}

function IssueIcon({ level }: { level: CheckIssue["level"] }) {
  if (level === "error")
    return <AlertTriangle className="h-4 w-4 text-destructive shrink-0" />;
  if (level === "warning")
    return <AlertTriangle className="h-4 w-4 text-amber-600 shrink-0" />;
  return <Info className="h-4 w-4 text-blue-600 shrink-0" />;
}

// ---------- Page ----------

export default function TaxBPOPage() {
  const [companyName, setCompanyName] = useState("");
  const [period, setPeriod] = useState("");
  const [entries, setEntries] = useState<JournalEntry[]>([newEntry()]);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<BookkeepingCheckResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resultOpen, setResultOpen] = useState(true);

  function addEntry() {
    setEntries((prev) => [...prev, newEntry()]);
  }

  function removeEntry(id: string) {
    setEntries((prev) => prev.filter((e) => e.id !== id));
  }

  function updateEntry(id: string, key: keyof JournalEntry, value: string) {
    setEntries((prev) =>
      prev.map((e) => (e.id === id ? { ...e, [key]: value } : e))
    );
  }

  async function handleSubmit() {
    if (!companyName.trim()) {
      setError("会社名を入力してください。");
      return;
    }
    if (!period.trim()) {
      setError("対象年月を入力してください。");
      return;
    }
    setError(null);
    setResult(null);
    setLoading(true);
    try {
      const inputData = {
        company_name: companyName,
        period,
        journal_entries: entries.map((e, idx) => ({
          entry_number: idx + 1,
          date: e.date,
          debit_account: e.debit_account,
          credit_account: e.credit_account,
          amount: e.amount ? Number(e.amount) : null,
          description: e.description,
          tax_category: e.tax_category,
          invoice_number: e.invoice_number || null,
        })),
      };
      const res = await apiFetch<BookkeepingCheckResult>(
        "/bpo/professional/bookkeeping-check",
        { method: "POST", body: { input_data: inputData } }
      );
      setResult(res);
      setResultOpen(true);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "チェックの実行に失敗しました。しばらく経ってから再度お試しください。"
      );
    } finally {
      setLoading(false);
    }
  }

  function handleReset() {
    setResult(null);
    setError(null);
    setEntries([newEntry()]);
    setCompanyName("");
    setPeriod("");
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      {/* ヘッダー */}
      <div className="flex items-center gap-3">
        <Link href="/bpo/professional">
          <Button variant="ghost" size="sm" className="text-muted-foreground">
            ← 士業サポートに戻る
          </Button>
        </Link>
      </div>

      <div>
        <h1 className="text-2xl font-bold">記帳の自動チェック</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          仕訳データを入力すると、ミスや不整合を自動でチェックします。インボイス制度への対応も確認できます。
        </p>
      </div>

      {/* 基本情報 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">基本情報</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="company-name">
              会社名 <span className="text-destructive">*</span>
            </Label>
            <Input
              id="company-name"
              value={companyName}
              onChange={(e) => setCompanyName(e.target.value)}
              placeholder="例: 株式会社サンプル"
              disabled={loading}
              className="w-full"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="period">
              対象年月 <span className="text-destructive">*</span>
            </Label>
            <Input
              id="period"
              type="month"
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
              placeholder="例: 2024-04"
              disabled={loading}
              className="w-full"
            />
          </div>
        </CardContent>
      </Card>

      {/* 仕訳入力テーブル */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-3">
            <div>
              <CardTitle className="text-base">仕訳データ</CardTitle>
              <CardDescription className="mt-1">
                チェックしたい仕訳を入力してください（複数行まとめて入力可）
              </CardDescription>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={addEntry}
              disabled={loading}
            >
              <Plus className="mr-1 h-4 w-4" />
              行を追加
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* スマホ：カード形式、PC：テーブル形式 */}
          <div className="hidden md:block overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-xs text-muted-foreground">
                  <th className="pb-2 text-left font-medium w-[100px]">日付</th>
                  <th className="pb-2 text-left font-medium">借方科目</th>
                  <th className="pb-2 text-left font-medium">貸方科目</th>
                  <th className="pb-2 text-left font-medium w-[110px]">金額（円）</th>
                  <th className="pb-2 text-left font-medium">摘要</th>
                  <th className="pb-2 text-left font-medium w-[110px]">消費税区分</th>
                  <th className="pb-2 text-left font-medium w-[130px]">インボイス番号</th>
                  <th className="pb-2 w-8" />
                </tr>
              </thead>
              <tbody className="space-y-2">
                {entries.map((entry) => (
                  <tr key={entry.id} className="border-b last:border-0">
                    <td className="py-2 pr-2">
                      <Input
                        type="date"
                        value={entry.date}
                        onChange={(e) => updateEntry(entry.id, "date", e.target.value)}
                        disabled={loading}
                        className="w-full text-xs"
                      />
                    </td>
                    <td className="py-2 pr-2">
                      <Input
                        list="accounts-list"
                        value={entry.debit_account}
                        onChange={(e) => updateEntry(entry.id, "debit_account", e.target.value)}
                        placeholder="例: 現金"
                        disabled={loading}
                        className="w-full text-xs"
                      />
                    </td>
                    <td className="py-2 pr-2">
                      <Input
                        list="accounts-list"
                        value={entry.credit_account}
                        onChange={(e) => updateEntry(entry.id, "credit_account", e.target.value)}
                        placeholder="例: 売上高"
                        disabled={loading}
                        className="w-full text-xs"
                      />
                    </td>
                    <td className="py-2 pr-2">
                      <Input
                        type="number"
                        value={entry.amount}
                        onChange={(e) => updateEntry(entry.id, "amount", e.target.value)}
                        placeholder="例: 110000"
                        disabled={loading}
                        className="w-full text-xs"
                      />
                    </td>
                    <td className="py-2 pr-2">
                      <Input
                        value={entry.description}
                        onChange={(e) => updateEntry(entry.id, "description", e.target.value)}
                        placeholder="例: 商品売上"
                        disabled={loading}
                        className="w-full text-xs"
                      />
                    </td>
                    <td className="py-2 pr-2">
                      <select
                        value={entry.tax_category}
                        onChange={(e) => updateEntry(entry.id, "tax_category", e.target.value)}
                        disabled={loading}
                        className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {TAX_CATEGORIES.map((tc) => (
                          <option key={tc.value} value={tc.value}>
                            {tc.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="py-2 pr-2">
                      <Input
                        value={entry.invoice_number}
                        onChange={(e) => updateEntry(entry.id, "invoice_number", e.target.value)}
                        placeholder="例: T1234567890123"
                        disabled={loading}
                        className="w-full text-xs"
                      />
                    </td>
                    <td className="py-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => removeEntry(entry.id)}
                        disabled={loading || entries.length <= 1}
                        className="h-8 w-8 p-0 text-muted-foreground hover:text-destructive"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* スマホ：カード形式 */}
          <div className="space-y-4 md:hidden">
            {entries.map((entry, idx) => (
              <div key={entry.id} className="rounded-md border p-3 space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-muted-foreground">
                    仕訳 {idx + 1}
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => removeEntry(entry.id)}
                    disabled={loading || entries.length <= 1}
                    className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div className="space-y-1">
                    <Label className="text-xs">日付</Label>
                    <Input
                      type="date"
                      value={entry.date}
                      onChange={(e) => updateEntry(entry.id, "date", e.target.value)}
                      disabled={loading}
                      className="text-xs"
                    />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs">金額（円）</Label>
                    <Input
                      type="number"
                      value={entry.amount}
                      onChange={(e) => updateEntry(entry.id, "amount", e.target.value)}
                      placeholder="例: 110000"
                      disabled={loading}
                      className="text-xs"
                    />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs">借方科目</Label>
                    <Input
                      list="accounts-list"
                      value={entry.debit_account}
                      onChange={(e) => updateEntry(entry.id, "debit_account", e.target.value)}
                      placeholder="例: 現金"
                      disabled={loading}
                      className="text-xs"
                    />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs">貸方科目</Label>
                    <Input
                      list="accounts-list"
                      value={entry.credit_account}
                      onChange={(e) => updateEntry(entry.id, "credit_account", e.target.value)}
                      placeholder="例: 売上高"
                      disabled={loading}
                      className="text-xs"
                    />
                  </div>
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">摘要</Label>
                  <Input
                    value={entry.description}
                    onChange={(e) => updateEntry(entry.id, "description", e.target.value)}
                    placeholder="例: 商品売上"
                    disabled={loading}
                    className="text-xs"
                  />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div className="space-y-1">
                    <Label className="text-xs">消費税区分</Label>
                    <select
                      value={entry.tax_category}
                      onChange={(e) => updateEntry(entry.id, "tax_category", e.target.value)}
                      disabled={loading}
                      className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {TAX_CATEGORIES.map((tc) => (
                        <option key={tc.value} value={tc.value}>
                          {tc.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs">インボイス番号</Label>
                    <Input
                      value={entry.invoice_number}
                      onChange={(e) => updateEntry(entry.id, "invoice_number", e.target.value)}
                      placeholder="例: T1234567890123"
                      disabled={loading}
                      className="text-xs"
                    />
                  </div>
                </div>
              </div>
            ))}
            <Button
              variant="outline"
              size="sm"
              onClick={addEntry}
              disabled={loading}
              className="w-full"
            >
              <Plus className="mr-1 h-4 w-4" />
              行を追加する
            </Button>
          </div>

          {/* 勘定科目サジェスト */}
          <datalist id="accounts-list">
            {COMMON_ACCOUNTS.map((acc) => (
              <option key={acc} value={acc} />
            ))}
          </datalist>

          {/* エラー */}
          {error && (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
              {error}
            </div>
          )}

          {/* 実行ボタン */}
          <Button
            onClick={handleSubmit}
            disabled={loading}
            size="lg"
            className="w-full"
          >
            {loading ? (
              <>
                <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                チェックしています...
              </>
            ) : (
              "記帳データをチェックする"
            )}
          </Button>
          {loading && (
            <p className="text-center text-xs text-muted-foreground">
              AIが仕訳データを分析しています。少しお待ちください...
            </p>
          )}
        </CardContent>
      </Card>

      {/* 結果表示 */}
      {result && (
        <div className="space-y-4">
          {/* サマリー */}
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-base">チェック結果サマリー</CardTitle>
                <div className="flex items-center gap-2">
                  {result.summary.is_balanced ? (
                    <Badge className="bg-green-100 text-green-800">貸借一致</Badge>
                  ) : (
                    <Badge variant="destructive">貸借不一致</Badge>
                  )}
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-3 gap-3 text-center">
                <div className="rounded-md border p-3">
                  <p className="text-xs text-muted-foreground">エラー</p>
                  <p className="text-2xl font-bold text-destructive">
                    {result.summary.error_count}
                  </p>
                </div>
                <div className="rounded-md border p-3">
                  <p className="text-xs text-muted-foreground">注意</p>
                  <p className="text-2xl font-bold text-amber-600">
                    {result.summary.warning_count}
                  </p>
                </div>
                <div className="rounded-md border p-3">
                  <p className="text-xs text-muted-foreground">情報</p>
                  <p className="text-2xl font-bold text-blue-600">
                    {result.summary.info_count}
                  </p>
                </div>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
                <div className="rounded-md bg-muted px-3 py-2">
                  <p className="text-xs text-muted-foreground">借方合計</p>
                  <p className="font-semibold">
                    ¥{Math.round(result.summary.debit_total).toLocaleString()}
                  </p>
                </div>
                <div className="rounded-md bg-muted px-3 py-2">
                  <p className="text-xs text-muted-foreground">貸方合計</p>
                  <p className="font-semibold">
                    ¥{Math.round(result.summary.credit_total).toLocaleString()}
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* 問題一覧 */}
          {result.issues && result.issues.length > 0 ? (
            <Card>
              <CardHeader className="pb-2">
                <button
                  className="flex w-full items-center justify-between"
                  onClick={() => setResultOpen((v) => !v)}
                >
                  <CardTitle className="text-base">
                    指摘事項（{result.issues.length}件）
                  </CardTitle>
                  {resultOpen ? (
                    <ChevronUp className="h-4 w-4 text-muted-foreground" />
                  ) : (
                    <ChevronDown className="h-4 w-4 text-muted-foreground" />
                  )}
                </button>
              </CardHeader>
              {resultOpen && (
                <CardContent className="space-y-3">
                  {result.issues.map((issue, i) => (
                    <div
                      key={i}
                      className="flex items-start gap-3 rounded-md border p-3"
                    >
                      <IssueIcon level={issue.level} />
                      <div className="min-w-0 flex-1 space-y-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <IssueBadge level={issue.level} />
                          <span className="text-xs text-muted-foreground font-mono">
                            {issue.code}
                          </span>
                          {issue.entry_index != null && (
                            <span className="text-xs text-muted-foreground">
                              仕訳 {issue.entry_index + 1}行目
                            </span>
                          )}
                        </div>
                        <p className="text-sm">{issue.message}</p>
                        {issue.suggestion && (
                          <p className="text-xs text-muted-foreground">
                            対応策: {issue.suggestion}
                          </p>
                        )}
                      </div>
                    </div>
                  ))}
                </CardContent>
              )}
            </Card>
          ) : (
            <Card className="border-green-200 bg-green-50">
              <CardContent className="py-6 text-center">
                <CheckCircle className="mx-auto mb-2 h-8 w-8 text-green-600" />
                <p className="text-sm font-semibold text-green-800">
                  問題は見つかりませんでした
                </p>
                <p className="text-xs text-green-700 mt-1">
                  チェックした{result.total_entries}件の仕訳に指摘事項はありません。
                </p>
              </CardContent>
            </Card>
          )}

          {/* 注意書き */}
          <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3">
            <p className="text-xs text-amber-800">
              AIによるチェック結果は参考情報です。実際の会計処理・税務申告の際は、担当の税理士に最終確認を依頼してください。
            </p>
          </div>

          {/* リセット */}
          <Button variant="outline" onClick={handleReset} className="w-full">
            別のデータをチェックする
          </Button>
        </div>
      )}
    </div>
  );
}
