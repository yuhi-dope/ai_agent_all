"use client";

import { useState } from "react";
import Link from "next/link";
import { ChevronDown, ChevronUp, AlertTriangle, CheckCircle, Info } from "lucide-react";
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

type ProcedureType =
  | "health_pension_acquire"
  | "health_pension_lose"
  | "standard_monthly_report"
  | "monthly_change"
  | "bonus_report"
  | "overtime_agreement"
  | "employment_insurance_acquire"
  | "employment_insurance_lose";

interface ComplianceWarning {
  level: "error" | "warning" | "info";
  message: string;
  field?: string;
}

interface GeneratedDocument {
  document_type: string;
  form_data: Record<string, unknown>;
  submission_deadline?: string;
  required_attachments?: string[];
}

interface ProcedureResult {
  success: boolean;
  procedure_type: string;
  generated_documents: GeneratedDocument[];
  compliance_warnings: ComplianceWarning[];
  next_steps?: string[];
  error?: string;
}

// ---------- 届出種別定義 ----------

const PROCEDURE_TYPES: { value: ProcedureType; label: string }[] = [
  { value: "health_pension_acquire", label: "健康保険・厚生年金保険 資格取得届" },
  { value: "health_pension_lose", label: "健康保険・厚生年金保険 資格喪失届" },
  { value: "standard_monthly_report", label: "算定基礎届" },
  { value: "monthly_change", label: "月額変更届（随時改定）" },
  { value: "bonus_report", label: "賞与支払届" },
  { value: "overtime_agreement", label: "時間外労働・休日労働に関する協定届（36協定）" },
  { value: "employment_insurance_acquire", label: "雇用保険 資格取得届" },
  { value: "employment_insurance_lose", label: "雇用保険 資格喪失届" },
];

// ---------- 届出種別ごとのフィールド定義 ----------

interface FieldDef {
  key: string;
  label: string;
  type: "text" | "date" | "number" | "select";
  placeholder: string;
  required: boolean;
  options?: { value: string; label: string }[];
}

const COMMON_EMPLOYEE_FIELDS: FieldDef[] = [
  { key: "employee_name", label: "従業員氏名", type: "text", placeholder: "例: 山田 太郎", required: true },
  { key: "employee_kana", label: "フリガナ", type: "text", placeholder: "例: ヤマダ タロウ", required: true },
  { key: "employee_number", label: "従業員番号", type: "text", placeholder: "例: EMP-001", required: false },
  { key: "birth_date", label: "生年月日", type: "date", placeholder: "例: 1990-04-01", required: true },
];

const PROCEDURE_FIELDS: Record<ProcedureType, FieldDef[]> = {
  health_pension_acquire: [
    ...COMMON_EMPLOYEE_FIELDS,
    { key: "acquire_date", label: "資格取得年月日", type: "date", placeholder: "例: 2024-04-01", required: true },
    { key: "standard_monthly_salary", label: "報酬月額（円）", type: "number", placeholder: "例: 300000", required: true },
    { key: "employment_type", label: "雇用形態", type: "select", placeholder: "選択してください", required: true, options: [
      { value: "full_time", label: "正社員" },
      { value: "part_time", label: "パートタイム" },
      { value: "contract", label: "契約社員" },
    ]},
  ],
  health_pension_lose: [
    ...COMMON_EMPLOYEE_FIELDS,
    { key: "lose_date", label: "資格喪失年月日", type: "date", placeholder: "例: 2024-03-31", required: true },
    { key: "lose_reason", label: "喪失理由", type: "select", placeholder: "選択してください", required: true, options: [
      { value: "retirement", label: "退職" },
      { value: "death", label: "死亡" },
      { value: "qualification_loss", label: "被保険者資格の喪失" },
    ]},
  ],
  standard_monthly_report: [
    ...COMMON_EMPLOYEE_FIELDS,
    { key: "april_salary", label: "4月の報酬（円）", type: "number", placeholder: "例: 300000", required: true },
    { key: "may_salary", label: "5月の報酬（円）", type: "number", placeholder: "例: 300000", required: true },
    { key: "june_salary", label: "6月の報酬（円）", type: "number", placeholder: "例: 300000", required: true },
  ],
  monthly_change: [
    ...COMMON_EMPLOYEE_FIELDS,
    { key: "change_date", label: "報酬変動年月日", type: "date", placeholder: "例: 2024-04-01", required: true },
    { key: "before_salary", label: "変動前の報酬（円）", type: "number", placeholder: "例: 300000", required: true },
    { key: "after_salary", label: "変動後の報酬（円）", type: "number", placeholder: "例: 350000", required: true },
    { key: "change_reason", label: "変動理由", type: "text", placeholder: "例: 昇給", required: true },
  ],
  bonus_report: [
    ...COMMON_EMPLOYEE_FIELDS,
    { key: "bonus_payment_date", label: "賞与支払年月日", type: "date", placeholder: "例: 2024-06-30", required: true },
    { key: "bonus_amount", label: "賞与額（円）", type: "number", placeholder: "例: 500000", required: true },
  ],
  overtime_agreement: [
    { key: "company_name", label: "事業場名称", type: "text", placeholder: "例: 株式会社サンプル 本社", required: true },
    { key: "company_address", label: "所在地", type: "text", placeholder: "例: 東京都渋谷区〇〇1-2-3", required: true },
    { key: "business_type", label: "業種", type: "text", placeholder: "例: 情報処理サービス業", required: true },
    { key: "employee_count", label: "従業員数", type: "number", placeholder: "例: 50", required: true },
    { key: "max_overtime_daily", label: "1日の限度時間（時間）", type: "number", placeholder: "例: 2", required: true },
    { key: "max_overtime_monthly", label: "1ヶ月の限度時間（時間）", type: "number", placeholder: "例: 45", required: true },
    { key: "max_overtime_yearly", label: "1年の限度時間（時間）", type: "number", placeholder: "例: 360", required: true },
    { key: "agreement_start_date", label: "協定の有効期間（開始）", type: "date", placeholder: "例: 2024-04-01", required: true },
    { key: "agreement_end_date", label: "協定の有効期間（終了）", type: "date", placeholder: "例: 2025-03-31", required: true },
  ],
  employment_insurance_acquire: [
    ...COMMON_EMPLOYEE_FIELDS,
    { key: "hire_date", label: "雇用年月日", type: "date", placeholder: "例: 2024-04-01", required: true },
    { key: "job_title", label: "職種", type: "text", placeholder: "例: 営業", required: true },
    { key: "weekly_hours", label: "週所定労働時間（時間）", type: "number", placeholder: "例: 40", required: true },
    { key: "monthly_salary", label: "賃金月額（円）", type: "number", placeholder: "例: 250000", required: true },
    { key: "employment_type", label: "雇用形態", type: "select", placeholder: "選択してください", required: true, options: [
      { value: "full_time", label: "正社員" },
      { value: "part_time", label: "パートタイム" },
      { value: "contract", label: "有期契約" },
    ]},
  ],
  employment_insurance_lose: [
    ...COMMON_EMPLOYEE_FIELDS,
    { key: "lose_date", label: "喪失年月日（離職日の翌日）", type: "date", placeholder: "例: 2024-04-01", required: true },
    { key: "lose_reason", label: "喪失原因", type: "select", placeholder: "選択してください", required: true, options: [
      { value: "resignation", label: "自己都合退職" },
      { value: "dismissal", label: "解雇" },
      { value: "expiration", label: "契約期間満了" },
      { value: "death", label: "死亡" },
    ]},
    { key: "last_salary", label: "直前6ヶ月の賃金総額（円）", type: "number", placeholder: "例: 1800000", required: true },
  ],
};

// ---------- 警告アイコン ----------

function WarningIcon({ level }: { level: ComplianceWarning["level"] }) {
  if (level === "error") return <AlertTriangle className="h-4 w-4 text-destructive shrink-0" />;
  if (level === "warning") return <AlertTriangle className="h-4 w-4 text-amber-600 shrink-0" />;
  return <Info className="h-4 w-4 text-blue-600 shrink-0" />;
}

function warningBadgeClass(level: ComplianceWarning["level"]) {
  if (level === "error") return "bg-destructive/10 text-destructive";
  if (level === "warning") return "bg-yellow-100 text-yellow-800";
  return "bg-blue-50 text-blue-800";
}

function warningLabel(level: ComplianceWarning["level"]) {
  if (level === "error") return "エラー";
  if (level === "warning") return "注意";
  return "情報";
}

// ---------- Page ----------

export default function LaborBPOPage() {
  const [procedureType, setProcedureType] = useState<ProcedureType>("health_pension_acquire");
  const [formValues, setFormValues] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ProcedureResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resultOpen, setResultOpen] = useState(true);

  const fields = PROCEDURE_FIELDS[procedureType] ?? [];

  function handleFieldChange(key: string, value: string) {
    setFormValues((prev) => ({ ...prev, [key]: value }));
  }

  function handleProcedureChange(value: ProcedureType) {
    setProcedureType(value);
    setFormValues({});
    setResult(null);
    setError(null);
  }

  async function handleSubmit() {
    setError(null);
    setResult(null);
    setLoading(true);
    try {
      const inputData = {
        procedure_type: procedureType,
        employee_info: formValues,
      };
      const res = await apiFetch<ProcedureResult>(
        "/bpo/professional/procedure-generation",
        { method: "POST", body: { input_data: inputData } }
      );
      setResult(res);
      setResultOpen(true);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "書類の生成に失敗しました。しばらく経ってから再度お試しください。"
      );
    } finally {
      setLoading(false);
    }
  }

  function handleReset() {
    setResult(null);
    setError(null);
    setFormValues({});
  }

  const selectedLabel =
    PROCEDURE_TYPES.find((p) => p.value === procedureType)?.label ?? "";

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      {/* ヘッダー */}
      <div className="flex items-center gap-3">
        <Link href="/bpo/professional">
          <Button variant="ghost" size="sm" className="text-muted-foreground">
            ← 士業サポートに戻る
          </Button>
        </Link>
      </div>

      <div>
        <h1 className="text-2xl font-bold">社会保険・労務手続き</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          届出種別を選択し、従業員情報を入力すると書類を自動生成します。
        </p>
      </div>

      {/* 入力フォーム */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">届出内容の入力</CardTitle>
          <CardDescription>必要事項を入力して書類を生成してください</CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {/* 届出種別 */}
          <div className="space-y-2">
            <Label htmlFor="procedure-type">届出種別</Label>
            <select
              id="procedure-type"
              value={procedureType}
              onChange={(e) => handleProcedureChange(e.target.value as ProcedureType)}
              disabled={loading}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {PROCEDURE_TYPES.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>

          {/* 動的フィールド */}
          {fields.map((field) => (
            <div key={field.key} className="space-y-2">
              <Label htmlFor={field.key}>
                {field.label}
                {field.required && (
                  <span className="ml-1 text-destructive">*</span>
                )}
              </Label>
              {field.type === "select" ? (
                <select
                  id={field.key}
                  value={formValues[field.key] ?? ""}
                  onChange={(e) => handleFieldChange(field.key, e.target.value)}
                  disabled={loading}
                  className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <option value="">選択してください</option>
                  {field.options?.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              ) : (
                <Input
                  id={field.key}
                  type={field.type}
                  value={formValues[field.key] ?? ""}
                  onChange={(e) => handleFieldChange(field.key, e.target.value)}
                  placeholder={field.placeholder}
                  disabled={loading}
                  className="w-full"
                />
              )}
            </div>
          ))}

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
                書類を生成しています...
              </>
            ) : (
              "書類を自動生成する"
            )}
          </Button>
          {loading && (
            <p className="text-center text-xs text-muted-foreground">
              AIが書類情報を確認しています。少しお待ちください...
            </p>
          )}
        </CardContent>
      </Card>

      {/* 結果表示 */}
      {result && (
        <div className="space-y-4">
          {/* 成功バナー */}
          {result.success && (
            <div className="flex items-center gap-3 rounded-lg border border-green-200 bg-green-50 px-4 py-3">
              <CheckCircle className="h-5 w-5 shrink-0 text-green-600" />
              <div>
                <p className="text-sm font-semibold text-green-800">書類を生成しました</p>
                <p className="text-xs text-green-700">{selectedLabel}</p>
              </div>
            </div>
          )}

          {/* コンプライアンス警告 */}
          {result.compliance_warnings && result.compliance_warnings.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <button
                  className="flex w-full items-center justify-between"
                  onClick={() => setResultOpen((v) => !v)}
                >
                  <CardTitle className="text-base">確認事項</CardTitle>
                  {resultOpen ? (
                    <ChevronUp className="h-4 w-4 text-muted-foreground" />
                  ) : (
                    <ChevronDown className="h-4 w-4 text-muted-foreground" />
                  )}
                </button>
              </CardHeader>
              {resultOpen && (
                <CardContent className="space-y-3">
                  {result.compliance_warnings.map((w, i) => (
                    <div
                      key={i}
                      className="flex items-start gap-2 rounded-md border p-3"
                    >
                      <WarningIcon level={w.level} />
                      <div className="min-w-0 flex-1">
                        <div className="mb-1 flex items-center gap-2">
                          <Badge className={warningBadgeClass(w.level)}>
                            {warningLabel(w.level)}
                          </Badge>
                          {w.field && (
                            <span className="text-xs text-muted-foreground">
                              {w.field}
                            </span>
                          )}
                        </div>
                        <p className="text-sm">{w.message}</p>
                      </div>
                    </div>
                  ))}
                </CardContent>
              )}
            </Card>
          )}

          {/* 生成書類情報 */}
          {result.generated_documents && result.generated_documents.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">生成された書類情報</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                {result.generated_documents.map((doc, i) => (
                  <div key={i} className="space-y-3">
                    <p className="text-sm font-semibold">{doc.document_type}</p>
                    {doc.submission_deadline && (
                      <div className="flex items-center gap-2 rounded-md bg-amber-50 px-3 py-2">
                        <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
                        <p className="text-sm text-amber-800">
                          提出期限: <span className="font-semibold">{doc.submission_deadline}</span>
                        </p>
                      </div>
                    )}
                    {doc.required_attachments && doc.required_attachments.length > 0 && (
                      <div>
                        <p className="mb-1 text-xs font-medium text-muted-foreground">
                          必要な添付書類
                        </p>
                        <ul className="space-y-1">
                          {doc.required_attachments.map((att, j) => (
                            <li key={j} className="flex items-center gap-2 text-sm">
                              <span className="h-1.5 w-1.5 rounded-full bg-primary shrink-0" />
                              {att}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    <div>
                      <p className="mb-2 text-xs font-medium text-muted-foreground">
                        入力情報
                      </p>
                      <pre className="overflow-x-auto rounded-md bg-muted px-3 py-3 text-xs leading-relaxed">
                        {JSON.stringify(doc.form_data, null, 2)}
                      </pre>
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>
          )}

          {/* 次のステップ */}
          {result.next_steps && result.next_steps.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">次のステップ</CardTitle>
              </CardHeader>
              <CardContent>
                <ol className="space-y-2">
                  {result.next_steps.map((step, i) => (
                    <li key={i} className="flex items-start gap-3 text-sm">
                      <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary text-[11px] font-bold text-primary-foreground">
                        {i + 1}
                      </span>
                      {step}
                    </li>
                  ))}
                </ol>
              </CardContent>
            </Card>
          )}

          {/* 注意書き */}
          <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3">
            <p className="text-xs text-amber-800">
              AIが生成した書類情報は参考資料です。実際の届出の際は、担当の社会保険労務士または行政機関に最終確認を依頼してください。
            </p>
          </div>

          {/* リセット */}
          <Button variant="outline" onClick={handleReset} className="w-full">
            別の届出を作成する
          </Button>
        </div>
      )}
    </div>
  );
}
