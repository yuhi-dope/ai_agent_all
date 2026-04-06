"use client";

import { useState } from "react";
import Link from "next/link";
import { ChevronDown, ChevronUp, AlertTriangle, CheckCircle, Info, Lightbulb } from "lucide-react";
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
import { Textarea } from "@/components/ui/textarea";
import { apiFetch } from "@/lib/api";

// ---------- 型定義 ----------

type ContractType =
  | "nda"
  | "outsourcing"
  | "sales"
  | "license"
  | "employment"
  | "lease"
  | "terms_of_service"
  | "other";

interface RiskItem {
  level: "high" | "medium" | "low";
  category: string;
  clause?: string;
  description: string;
  suggestion?: string;
}

interface ContractReviewResult {
  success: boolean;
  contract_type: string;
  contract_type_label: string;
  risk_score: number;
  risk_level: "high" | "medium" | "low";
  risks: RiskItem[];
  positive_points?: string[];
  overall_comment?: string;
  error?: string;
}

// ---------- 契約書種別 ----------

const CONTRACT_TYPES: { value: ContractType; label: string }[] = [
  { value: "nda", label: "秘密保持契約（NDA）" },
  { value: "outsourcing", label: "業務委託契約" },
  { value: "sales", label: "売買契約" },
  { value: "license", label: "ライセンス契約" },
  { value: "employment", label: "雇用契約・労働契約" },
  { value: "lease", label: "賃貸借契約" },
  { value: "terms_of_service", label: "利用規約" },
  { value: "other", label: "その他" },
];

// ---------- リスクレベル設定 ----------

function riskScoreLabel(score: number): string {
  if (score >= 70) return "高リスク";
  if (score >= 40) return "中リスク";
  return "低リスク";
}

function riskScoreClass(score: number): string {
  if (score >= 70) return "text-destructive";
  if (score >= 40) return "text-amber-600";
  return "text-green-600";
}

function riskBadge(level: RiskItem["level"]) {
  if (level === "high") return <Badge variant="destructive">高リスク</Badge>;
  if (level === "medium") return <Badge className="bg-yellow-100 text-yellow-800">中リスク</Badge>;
  return <Badge variant="secondary">低リスク</Badge>;
}

function RiskIcon({ level }: { level: RiskItem["level"] }) {
  if (level === "high") return <AlertTriangle className="h-4 w-4 text-destructive shrink-0" />;
  if (level === "medium") return <AlertTriangle className="h-4 w-4 text-amber-600 shrink-0" />;
  return <Info className="h-4 w-4 text-blue-600 shrink-0" />;
}

function overallRiskBadge(level: ContractReviewResult["risk_level"]) {
  if (level === "high") return <Badge variant="destructive" className="text-base px-3 py-1">高リスク</Badge>;
  if (level === "medium") return <Badge className="bg-yellow-100 text-yellow-800 text-base px-3 py-1">中リスク</Badge>;
  return <Badge className="bg-green-100 text-green-800 text-base px-3 py-1">低リスク</Badge>;
}

// ---------- Page ----------

export default function AttorneyBPOPage() {
  const [contractType, setContractType] = useState<ContractType>("nda");
  const [clientName, setClientName] = useState("");
  const [counterpartyName, setCounterpartyName] = useState("");
  const [contractText, setContractText] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ContractReviewResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [risksOpen, setRisksOpen] = useState(true);
  const [positiveOpen, setPositiveOpen] = useState(true);

  async function handleSubmit() {
    if (!contractText.trim()) {
      setError("契約書のテキストを入力してください。");
      return;
    }
    setError(null);
    setResult(null);
    setLoading(true);
    try {
      const inputData = {
        contract_type: contractType,
        client_name: clientName || null,
        counterparty_name: counterpartyName || null,
        contract_text: contractText,
      };
      const res = await apiFetch<ContractReviewResult>(
        "/bpo/professional/contract-review",
        { method: "POST", body: { input_data: inputData }, timeoutMs: 60_000 }
      );
      setResult(res);
      setRisksOpen(true);
      setPositiveOpen(true);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "レビューの実行に失敗しました。しばらく経ってから再度お試しください。"
      );
    } finally {
      setLoading(false);
    }
  }

  function handleReset() {
    setResult(null);
    setError(null);
    setContractText("");
    setClientName("");
    setCounterpartyName("");
  }

  const highRisks = result?.risks.filter((r) => r.level === "high") ?? [];
  const mediumRisks = result?.risks.filter((r) => r.level === "medium") ?? [];
  const lowRisks = result?.risks.filter((r) => r.level === "low") ?? [];

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      {/* ヘッダー */}
      <div className="flex items-center gap-3">
        <Link href="/bpo/professional">
          <Button variant="ghost" size="sm" className="text-muted-foreground">
            ← 士業サポートに戻る
          </Button>
        </Link>
      </div>

      <div>
        <h1 className="text-2xl font-bold">契約書のリスクレビュー</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          契約書のテキストを貼り付けると、AIがリスク箇所を分析し、修正提案を提示します。
        </p>
      </div>

      {/* 入力フォーム */}
      {!result && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">契約書の情報を入力</CardTitle>
            <CardDescription>
              契約書全文を貼り付けてください。個人情報は事前に伏せてからご利用ください。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            {/* 契約書種別 */}
            <div className="space-y-2">
              <Label htmlFor="contract-type">
                契約書の種別 <span className="text-destructive">*</span>
              </Label>
              <select
                id="contract-type"
                value={contractType}
                onChange={(e) => setContractType(e.target.value as ContractType)}
                disabled={loading}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {CONTRACT_TYPES.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            </div>

            {/* 当事者情報 */}
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="client-name">顧問先名（自社）</Label>
                <Input
                  id="client-name"
                  value={clientName}
                  onChange={(e) => setClientName(e.target.value)}
                  placeholder="例: 株式会社サンプル"
                  disabled={loading}
                  className="w-full"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="counterparty-name">相手方名</Label>
                <Input
                  id="counterparty-name"
                  value={counterpartyName}
                  onChange={(e) => setCounterpartyName(e.target.value)}
                  placeholder="例: 株式会社取引先"
                  disabled={loading}
                  className="w-full"
                />
              </div>
            </div>

            {/* 契約書テキスト */}
            <div className="space-y-2">
              <Label htmlFor="contract-text">
                契約書全文 <span className="text-destructive">*</span>
              </Label>
              <Textarea
                id="contract-text"
                value={contractText}
                onChange={(e) => setContractText(e.target.value)}
                placeholder="契約書のテキストをここに貼り付けてください。&#10;&#10;例：&#10;秘密保持契約書&#10;&#10;株式会社〇〇（以下「甲」という）と株式会社△△（以下「乙」という）は、..."
                disabled={loading}
                className="min-h-64 w-full text-sm leading-relaxed"
              />
              <p className="text-xs text-muted-foreground">
                ※ 個人情報（氏名・住所等）は事前に伏せ字にしてからご利用ください
              </p>
            </div>

            {/* エラー */}
            {error && (
              <div className="rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
                {error}
              </div>
            )}

            {/* 実行ボタン */}
            <Button
              onClick={handleSubmit}
              disabled={loading || !contractText.trim()}
              size="lg"
              className="w-full"
            >
              {loading ? (
                <>
                  <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  レビューしています...
                </>
              ) : (
                "契約書をレビューする"
              )}
            </Button>
            {loading && (
              <p className="text-center text-xs text-muted-foreground">
                AIが契約書を分析しています。文量によっては少しお時間がかかります...
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {/* 結果表示 */}
      {result && (
        <div className="space-y-4">
          {/* リスクスコア */}
          <Card>
            <CardContent className="py-6">
              <div className="flex flex-wrap items-center justify-between gap-4">
                <div>
                  <p className="text-sm text-muted-foreground">リスク評価</p>
                  <div className="mt-1 flex items-baseline gap-3">
                    <span
                      className={`text-4xl font-bold ${riskScoreClass(result.risk_score)}`}
                    >
                      {result.risk_score}
                    </span>
                    <span className="text-muted-foreground text-sm">/ 100点</span>
                    <span
                      className={`text-sm font-semibold ${riskScoreClass(result.risk_score)}`}
                    >
                      {riskScoreLabel(result.risk_score)}
                    </span>
                  </div>
                </div>
                <div className="flex flex-col items-end gap-2">
                  {overallRiskBadge(result.risk_level)}
                  <div className="flex gap-3 text-xs text-muted-foreground">
                    <span className="text-destructive font-semibold">
                      高 {highRisks.length}件
                    </span>
                    <span className="text-amber-600 font-semibold">
                      中 {mediumRisks.length}件
                    </span>
                    <span className="text-blue-600 font-semibold">
                      低 {lowRisks.length}件
                    </span>
                  </div>
                </div>
              </div>
              {result.overall_comment && (
                <p className="mt-4 text-sm text-muted-foreground border-t pt-4">
                  {result.overall_comment}
                </p>
              )}
            </CardContent>
          </Card>

          {/* リスク一覧 */}
          {result.risks && result.risks.length > 0 ? (
            <Card>
              <CardHeader className="pb-2">
                <button
                  className="flex w-full items-center justify-between"
                  onClick={() => setRisksOpen((v) => !v)}
                >
                  <CardTitle className="text-base">
                    リスク一覧（{result.risks.length}件）
                  </CardTitle>
                  {risksOpen ? (
                    <ChevronUp className="h-4 w-4 text-muted-foreground" />
                  ) : (
                    <ChevronDown className="h-4 w-4 text-muted-foreground" />
                  )}
                </button>
              </CardHeader>
              {risksOpen && (
                <CardContent className="space-y-3">
                  {/* 高リスクを先頭に表示 */}
                  {[...highRisks, ...mediumRisks, ...lowRisks].map((risk, i) => (
                    <div
                      key={i}
                      className="space-y-2 rounded-md border p-3"
                    >
                      <div className="flex flex-wrap items-start gap-2">
                        <RiskIcon level={risk.level} />
                        <div className="flex flex-wrap items-center gap-2 flex-1">
                          {riskBadge(risk.level)}
                          <span className="text-xs text-muted-foreground">
                            {risk.category}
                          </span>
                          {risk.clause && (
                            <span className="text-xs font-mono text-muted-foreground">
                              {risk.clause}
                            </span>
                          )}
                        </div>
                      </div>
                      <p className="text-sm">{risk.description}</p>
                      {risk.suggestion && (
                        <div className="flex items-start gap-2 rounded-md bg-blue-50 px-3 py-2">
                          <Lightbulb className="h-3.5 w-3.5 shrink-0 text-blue-600 mt-0.5" />
                          <p className="text-xs text-blue-800">{risk.suggestion}</p>
                        </div>
                      )}
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
                  重大なリスクは見つかりませんでした
                </p>
              </CardContent>
            </Card>
          )}

          {/* ポジティブな評価 */}
          {result.positive_points && result.positive_points.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <button
                  className="flex w-full items-center justify-between"
                  onClick={() => setPositiveOpen((v) => !v)}
                >
                  <CardTitle className="text-base">適切に記載されている点</CardTitle>
                  {positiveOpen ? (
                    <ChevronUp className="h-4 w-4 text-muted-foreground" />
                  ) : (
                    <ChevronDown className="h-4 w-4 text-muted-foreground" />
                  )}
                </button>
              </CardHeader>
              {positiveOpen && (
                <CardContent>
                  <ul className="space-y-2">
                    {result.positive_points.map((point, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm">
                        <CheckCircle className="h-4 w-4 shrink-0 text-green-600 mt-0.5" />
                        {point}
                      </li>
                    ))}
                  </ul>
                </CardContent>
              )}
            </Card>
          )}

          {/* 注意書き */}
          <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3">
            <p className="text-xs text-amber-800">
              AIによるレビューは参考情報です。重要な契約の締結・更新の際は、必ず担当の弁護士に最終確認を依頼してください。
            </p>
          </div>

          {/* リセット */}
          <div className="flex flex-col gap-3 sm:flex-row">
            <Button variant="outline" onClick={handleReset} className="flex-1">
              別の契約書をレビューする
            </Button>
            <Link href="/bpo/professional" className="flex-1">
              <Button variant="ghost" className="w-full">
                士業サポートに戻る
              </Button>
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
