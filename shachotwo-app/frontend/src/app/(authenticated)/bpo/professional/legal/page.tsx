"use client";

import { useState } from "react";
import Link from "next/link";
import { ChevronDown, ChevronUp, AlertTriangle, CheckCircle, Info, FileText } from "lucide-react";
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

type PermitType =
  | "construction_new"
  | "construction_renew"
  | "construction_financial_report"
  | "freight_general"
  | "industrial_waste";

interface RequirementCheck {
  item: string;
  satisfied: boolean;
  note?: string;
}

interface ComplianceWarning {
  level: "error" | "warning" | "info";
  message: string;
  field?: string;
}

interface PermitGenerationResult {
  success: boolean;
  permit_type: string;
  permit_type_label: string;
  requirement_checks: RequirementCheck[];
  required_documents: string[];
  compliance_warnings: ComplianceWarning[];
  application_data: Record<string, unknown>;
  estimated_processing_days?: number;
  error?: string;
}

// ---------- 許可種別定義 ----------

const PERMIT_TYPES: { value: PermitType; label: string; description: string }[] = [
  {
    value: "construction_new",
    label: "建設業許可（新規）",
    description: "建設業を新たに営む際の許可申請",
  },
  {
    value: "construction_renew",
    label: "建設業許可（更新）",
    description: "5年ごとの許可更新申請",
  },
  {
    value: "construction_financial_report",
    label: "決算変更届（建設業）",
    description: "毎事業年度終了後4ヶ月以内に必要な届出",
  },
  {
    value: "freight_general",
    label: "一般貨物自動車運送事業許可",
    description: "トラックを使った貨物輸送事業の許可",
  },
  {
    value: "industrial_waste",
    label: "産業廃棄物収集運搬業許可",
    description: "産業廃棄物の収集・運搬事業の許可",
  },
];

// ---------- 許可種別ごとのフィールド定義 ----------

interface FieldDef {
  key: string;
  label: string;
  type: "text" | "date" | "number" | "select" | "textarea";
  placeholder: string;
  required: boolean;
  options?: { value: string; label: string }[];
}

const COMMON_APPLICANT_FIELDS: FieldDef[] = [
  { key: "company_name", label: "申請者名（法人名または氏名）", type: "text", placeholder: "例: 株式会社建設商事", required: true },
  { key: "representative_name", label: "代表者名", type: "text", placeholder: "例: 山田 太郎", required: true },
  { key: "address", label: "主たる営業所の所在地", type: "text", placeholder: "例: 東京都渋谷区〇〇1-2-3", required: true },
  { key: "phone", label: "電話番号", type: "text", placeholder: "例: 03-1234-5678", required: true },
];

const PERMIT_FIELDS: Record<PermitType, FieldDef[]> = {
  construction_new: [
    ...COMMON_APPLICANT_FIELDS,
    { key: "license_type", label: "許可の種類", type: "select", placeholder: "選択してください", required: true, options: [
      { value: "general", label: "一般建設業" },
      { value: "special", label: "特定建設業" },
    ]},
    { key: "license_area", label: "許可区分", type: "select", placeholder: "選択してください", required: true, options: [
      { value: "ministerial", label: "大臣許可（2都道府県以上に営業所あり）" },
      { value: "prefectural", label: "知事許可（1都道府県のみ）" },
    ]},
    { key: "business_types", label: "業種（許可を受けたい業種）", type: "textarea", placeholder: "例: 土木工事業、建築工事業、管工事業", required: true },
    { key: "capital", label: "資本金または出資の総額（円）", type: "number", placeholder: "例: 2000000", required: true },
    { key: "chief_engineer_name", label: "経営業務管理責任者氏名", type: "text", placeholder: "例: 鈴木 一郎", required: true },
    { key: "qualified_engineer_name", label: "専任技術者氏名", type: "text", placeholder: "例: 佐藤 二郎", required: true },
    { key: "qualified_engineer_qualification", label: "専任技術者の資格・免許", type: "text", placeholder: "例: 1級建築施工管理技士", required: true },
  ],
  construction_renew: [
    ...COMMON_APPLICANT_FIELDS,
    { key: "current_license_number", label: "現在の許可番号", type: "text", placeholder: "例: 国土交通大臣許可（般-XX）第XXXXXX号", required: true },
    { key: "license_expiry", label: "許可の有効期限", type: "date", placeholder: "例: 2024-10-31", required: true },
    { key: "business_types", label: "更新する業種", type: "textarea", placeholder: "例: 土木工事業、建築工事業", required: true },
  ],
  construction_financial_report: [
    ...COMMON_APPLICANT_FIELDS,
    { key: "current_license_number", label: "許可番号", type: "text", placeholder: "例: 国土交通大臣許可（般-XX）第XXXXXX号", required: true },
    { key: "fiscal_year_end", label: "事業年度終了日", type: "date", placeholder: "例: 2024-03-31", required: true },
    { key: "construction_revenue", label: "建設業の完成工事高（円）", type: "number", placeholder: "例: 50000000", required: true },
    { key: "total_revenue", label: "総売上高（円）", type: "number", placeholder: "例: 60000000", required: true },
    { key: "total_assets", label: "総資産（円）", type: "number", placeholder: "例: 20000000", required: true },
    { key: "net_assets", label: "純資産（円）", type: "number", placeholder: "例: 5000000", required: true },
  ],
  freight_general: [
    ...COMMON_APPLICANT_FIELDS,
    { key: "service_area", label: "事業区域", type: "text", placeholder: "例: 関東一円", required: true },
    { key: "truck_count", label: "使用するトラック台数", type: "number", placeholder: "例: 5", required: true },
    { key: "garage_address", label: "車庫の所在地", type: "text", placeholder: "例: 神奈川県横浜市〇〇1-2-3", required: true },
    { key: "garage_capacity", label: "車庫収容能力（台）", type: "number", placeholder: "例: 10", required: true },
    { key: "safety_officer", label: "運行管理者氏名", type: "text", placeholder: "例: 高橋 三郎", required: true },
    { key: "maintenance_officer", label: "整備管理者氏名", type: "text", placeholder: "例: 田中 四郎", required: true },
  ],
  industrial_waste: [
    ...COMMON_APPLICANT_FIELDS,
    { key: "collection_area", label: "収集運搬区域（都道府県）", type: "text", placeholder: "例: 東京都、神奈川県", required: true },
    { key: "waste_types", label: "取り扱う産業廃棄物の種類", type: "textarea", placeholder: "例: 廃プラスチック類、金属くず、ガラスくず", required: true },
    { key: "vehicle_types", label: "収集運搬に使用する車両", type: "text", placeholder: "例: 2tトラック×3台", required: true },
    { key: "storage_place", label: "積替え保管施設の有無", type: "select", placeholder: "選択してください", required: true, options: [
      { value: "none", label: "なし" },
      { value: "exists", label: "あり" },
    ]},
  ],
};

// ---------- 警告スタイル ----------

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
  if (level === "error") return "要対応";
  if (level === "warning") return "注意";
  return "情報";
}

// ---------- Page ----------

export default function LegalBPOPage() {
  const [permitType, setPermitType] = useState<PermitType>("construction_new");
  const [formValues, setFormValues] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<PermitGenerationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [requirementsOpen, setRequirementsOpen] = useState(true);
  const [warningsOpen, setWarningsOpen] = useState(true);

  const fields = PERMIT_FIELDS[permitType] ?? [];

  function handleFieldChange(key: string, value: string) {
    setFormValues((prev) => ({ ...prev, [key]: value }));
  }

  function handlePermitTypeChange(value: PermitType) {
    setPermitType(value);
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
        permit_type: permitType,
        applicant_info: formValues,
      };
      const res = await apiFetch<PermitGenerationResult>(
        "/bpo/professional/permit-generation",
        { method: "POST", body: { input_data: inputData } }
      );
      setResult(res);
      setRequirementsOpen(true);
      setWarningsOpen(true);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "申請書の生成に失敗しました。しばらく経ってから再度お試しください。"
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

  const selectedPermit = PERMIT_TYPES.find((p) => p.value === permitType);

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
        <h1 className="text-2xl font-bold">許可申請書の自動生成</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          許可の種類と申請者情報を入力すると、要件チェックと必要書類リストを自動で作成します。
        </p>
      </div>

      {/* 入力フォーム */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">申請情報の入力</CardTitle>
          <CardDescription>許可の種類と申請者情報を入力してください</CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {/* 許可種別 */}
          <div className="space-y-2">
            <Label htmlFor="permit-type">
              許可種別 <span className="text-destructive">*</span>
            </Label>
            <select
              id="permit-type"
              value={permitType}
              onChange={(e) => handlePermitTypeChange(e.target.value as PermitType)}
              disabled={loading}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {PERMIT_TYPES.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
            {selectedPermit && (
              <p className="text-xs text-muted-foreground">{selectedPermit.description}</p>
            )}
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
              ) : field.type === "textarea" ? (
                <textarea
                  id={field.key}
                  value={formValues[field.key] ?? ""}
                  onChange={(e) => handleFieldChange(field.key, e.target.value)}
                  placeholder={field.placeholder}
                  disabled={loading}
                  rows={3}
                  className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                />
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
                申請書を生成しています...
              </>
            ) : (
              "要件をチェックして書類を生成する"
            )}
          </Button>
          {loading && (
            <p className="text-center text-xs text-muted-foreground">
              AIが申請要件を確認しています。少しお待ちください...
            </p>
          )}
        </CardContent>
      </Card>

      {/* 結果表示 */}
      {result && (
        <div className="space-y-4">
          {/* 成功バナー */}
          <div className="flex items-center gap-3 rounded-lg border border-green-200 bg-green-50 px-4 py-3">
            <CheckCircle className="h-5 w-5 shrink-0 text-green-600" />
            <div>
              <p className="text-sm font-semibold text-green-800">
                {result.permit_type_label ?? selectedPermit?.label} — 生成完了
              </p>
              {result.estimated_processing_days && (
                <p className="text-xs text-green-700">
                  標準審査期間の目安: 約{result.estimated_processing_days}日
                </p>
              )}
            </div>
          </div>

          {/* 要件チェック */}
          {result.requirement_checks && result.requirement_checks.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <button
                  className="flex w-full items-center justify-between"
                  onClick={() => setRequirementsOpen((v) => !v)}
                >
                  <CardTitle className="text-base">申請要件チェック</CardTitle>
                  {requirementsOpen ? (
                    <ChevronUp className="h-4 w-4 text-muted-foreground" />
                  ) : (
                    <ChevronDown className="h-4 w-4 text-muted-foreground" />
                  )}
                </button>
              </CardHeader>
              {requirementsOpen && (
                <CardContent className="space-y-2">
                  {result.requirement_checks.map((req, i) => (
                    <div
                      key={i}
                      className="flex items-start gap-3 rounded-md border p-3"
                    >
                      {req.satisfied ? (
                        <CheckCircle className="h-4 w-4 shrink-0 text-green-600 mt-0.5" />
                      ) : (
                        <AlertTriangle className="h-4 w-4 shrink-0 text-destructive mt-0.5" />
                      )}
                      <div>
                        <p className="text-sm font-medium">{req.item}</p>
                        {req.note && (
                          <p className="text-xs text-muted-foreground mt-0.5">{req.note}</p>
                        )}
                      </div>
                      <div className="ml-auto shrink-0">
                        {req.satisfied ? (
                          <Badge className="bg-green-100 text-green-800">充足</Badge>
                        ) : (
                          <Badge variant="destructive">未充足</Badge>
                        )}
                      </div>
                    </div>
                  ))}
                </CardContent>
              )}
            </Card>
          )}

          {/* 必要書類リスト */}
          {result.required_documents && result.required_documents.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">必要書類リスト</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-2">
                  {result.required_documents.map((doc, i) => (
                    <li key={i} className="flex items-center gap-3">
                      <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                      <span className="text-sm">{doc}</span>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}

          {/* コンプライアンス警告 */}
          {result.compliance_warnings && result.compliance_warnings.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <button
                  className="flex w-full items-center justify-between"
                  onClick={() => setWarningsOpen((v) => !v)}
                >
                  <CardTitle className="text-base">確認事項</CardTitle>
                  {warningsOpen ? (
                    <ChevronUp className="h-4 w-4 text-muted-foreground" />
                  ) : (
                    <ChevronDown className="h-4 w-4 text-muted-foreground" />
                  )}
                </button>
              </CardHeader>
              {warningsOpen && (
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
                        </div>
                        <p className="text-sm">{w.message}</p>
                      </div>
                    </div>
                  ))}
                </CardContent>
              )}
            </Card>
          )}

          {/* 注意書き */}
          <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3">
            <p className="text-xs text-amber-800">
              AIが生成した要件チェックと書類リストは参考情報です。実際の申請の際は、担当の行政書士または管轄行政機関に最終確認を依頼してください。
            </p>
          </div>

          {/* リセット */}
          <Button variant="outline" onClick={handleReset} className="w-full">
            別の申請を作成する
          </Button>
        </div>
      )}
    </div>
  );
}
