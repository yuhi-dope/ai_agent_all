"use client";

import { useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select } from "@/components/ui/select";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";

// ---------- 型定義 ----------

interface ConstructionMethodDetail {
  work_type: string;
  method: string;
}

interface ConstructionMethods {
  overview: string;
  work_type_details: ConstructionMethodDetail[];
}

interface QualityManagementPlan {
  policy: string;
  checkpoints: string[];
}

interface EnvironmentalMeasures {
  policy: string;
  measures: string[];
}

interface ConstructionSystem {
  superintendent: string;
  safety_manager: string;
  description: string;
}

interface SafetyRiskItem {
  hazard: string;
  category: string;
  countermeasures: string[];
}

interface SafetyManagementPlan {
  policy: string;
  high_risk_items: SafetyRiskItem[];
  mid_risk_items: SafetyRiskItem[];
  daily_activities: string[];
  emergency_contacts: string;
}

interface PlanResult {
  project_name: string;
  project_type: string;
  owner_type: string;
  scale: string;
  conditions: string;
  work_types: string[];
  site_name: string;
  start_date: string;
  end_date: string;
  superintendent: string;
  safety_manager: string;
  template_name: string;
  sections_required: string[];
  compliance_standards: string[];
  construction_policy: string;
  construction_methods: ConstructionMethods;
  safety_management_plan: SafetyManagementPlan;
  quality_management_plan: QualityManagementPlan;
  environmental_measures: EnvironmentalMeasures;
  schedule_overview: string;
  construction_system: ConstructionSystem;
  compliance_warnings: string[];
  compliance_ok_items: string[];
}

interface RunResponse {
  success: boolean;
  pipeline_key: string;
  output: PlanResult;
  summary: string;
  requires_approval: boolean;
}

// ---------- フォームの初期値 ----------

const INITIAL_FORM = {
  project_name: "",
  project_type: "土木",
  owner_type: "公共",
  scale: "",
  conditions: "",
  work_types: "",
  site_name: "",
  start_date: "",
  end_date: "",
  superintendent: "",
  safety_manager: "",
};

// ---------- セクション折りたたみコンポーネント ----------

interface AccordionSectionProps {
  title: string;
  badge?: string;
  badgeVariant?: "default" | "secondary" | "outline" | "destructive";
  children: React.ReactNode;
  defaultOpen?: boolean;
}

function AccordionSection({
  title,
  badge,
  badgeVariant = "secondary",
  children,
  defaultOpen = false,
}: AccordionSectionProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="rounded-lg border">
      <button
        type="button"
        className="flex w-full items-center justify-between px-4 py-3 text-left"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <div className="flex items-center gap-2">
          <span className="text-base font-semibold">{title}</span>
          {badge && <Badge variant={badgeVariant}>{badge}</Badge>}
        </div>
        <span
          className="text-muted-foreground text-sm transition-transform duration-200"
          style={{ display: "inline-block", transform: open ? "rotate(180deg)" : "rotate(0deg)" }}
          aria-hidden="true"
        >
          ▼
        </span>
      </button>
      {open && (
        <div className="border-t px-4 pb-4 pt-3">
          {children}
        </div>
      )}
    </div>
  );
}

// ---------- 安全管理計画テーブル ----------

interface RiskTableProps {
  items: SafetyRiskItem[];
  level: "高" | "中";
}

function RiskTable({ items, level }: RiskTableProps) {
  if (items.length === 0) return null;
  const badgeClass =
    level === "高"
      ? "bg-red-100 text-red-800"
      : "bg-yellow-100 text-yellow-800";

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Badge className={badgeClass}>リスク：{level}</Badge>
      </div>
      {/* スマホはカード形式、PC以上はテーブル */}
      <div className="hidden sm:block overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="bg-muted/50 text-left">
              <th className="border px-3 py-2 font-medium w-1/4">区分</th>
              <th className="border px-3 py-2 font-medium w-2/5">危険内容</th>
              <th className="border px-3 py-2 font-medium">対策</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item, i) => (
              <tr key={i} className="align-top">
                <td className="border px-3 py-2 text-muted-foreground">{item.category}</td>
                <td className="border px-3 py-2">{item.hazard}</td>
                <td className="border px-3 py-2">
                  <ul className="list-disc list-inside space-y-0.5">
                    {item.countermeasures.map((c, j) => (
                      <li key={j}>{c}</li>
                    ))}
                  </ul>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* スマホ用カード */}
      <div className="sm:hidden space-y-3">
        {items.map((item, i) => (
          <div key={i} className="rounded-md border p-3 space-y-1">
            <p className="text-xs text-muted-foreground">{item.category}</p>
            <p className="text-sm font-medium">{item.hazard}</p>
            <ul className="text-xs list-disc list-inside space-y-0.5 text-muted-foreground">
              {item.countermeasures.map((c, j) => (
                <li key={j}>{c}</li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------- メインページ ----------

export default function ConstructionPlanPage() {
  const { session } = useAuth();
  const [form, setForm] = useState(INITIAL_FORM);
  const [errors, setErrors] = useState<Partial<typeof INITIAL_FORM>>({});
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<PlanResult | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);

  function handleChange(
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>
  ) {
    const { name, value } = e.target;
    setForm((prev) => ({ ...prev, [name]: value }));
    setErrors((prev) => ({ ...prev, [name]: undefined }));
  }

  function validate(): boolean {
    const newErrors: Partial<typeof INITIAL_FORM> = {};
    if (!form.project_name.trim()) newErrors.project_name = "工事名を入力してください";
    if (!form.scale.trim()) newErrors.scale = "工事規模を入力してください";
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!validate()) return;
    if (!session?.access_token) return;

    setLoading(true);
    setApiError(null);
    setResult(null);

    try {
      const workTypesList = form.work_types
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);

      const inputData = {
        project_name: form.project_name.trim(),
        project_type: form.project_type,
        owner_type: form.owner_type,
        scale: form.scale.trim(),
        conditions: form.conditions.trim(),
        work_types: workTypesList,
        site_name: form.site_name.trim() || form.project_name.trim(),
        start_date: form.start_date,
        end_date: form.end_date,
        superintendent: form.superintendent.trim(),
        safety_manager: form.safety_manager.trim(),
      };

      const data = await apiFetch<RunResponse>("/execution/run", {
        token: session.access_token,
        method: "POST",
        body: {
          pipeline_key: "construction/construction_plan",
          input_data: inputData,
        },
      });

      if (data.output) {
        setResult(data.output);
      } else {
        setApiError("施工計画書の生成に失敗しました。しばらく経ってからもう一度お試しください。");
      }
    } catch {
      setApiError("施工計画書の生成に失敗しました。しばらく経ってからもう一度お試しください。");
    } finally {
      setLoading(false);
    }
  }

  function handleReset() {
    setResult(null);
    setApiError(null);
    setErrors({});
  }

  return (
    <div className="space-y-6">
      {/* ページヘッダー */}
      <div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
          <Link href="/bpo" className="hover:underline">
            業務自動化
          </Link>
          <span aria-hidden="true">/</span>
          <span>施工計画書AI</span>
        </div>
        <h1 className="text-2xl font-bold">施工計画書AI</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          工事情報を入力すると、AIが施工計画書の各章を自動生成します。安全管理計画・品質管理計画・コンプライアンスチェックも含まれます。
        </p>
      </div>

      {/* 結果表示 */}
      {result ? (
        <div className="space-y-4">
          {/* 完了バナー */}
          <div className="rounded-md bg-green-50 border border-green-200 px-4 py-3 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
            <div>
              <p className="text-sm font-medium text-green-700">施工計画書を生成しました</p>
              <p className="text-xs text-green-600 mt-0.5">
                {result.template_name} / 適用基準: {result.compliance_standards.join("・")}
              </p>
            </div>
            <div className="flex gap-2 shrink-0">
              <Button size="sm" variant="outline" disabled>
                Word出力（準備中）
              </Button>
              <Button size="sm" variant="outline" onClick={handleReset}>
                新たに生成する
              </Button>
            </div>
          </div>

          {/* コンプライアンスチェック結果 */}
          {(result.compliance_warnings.length > 0 || result.compliance_ok_items.length > 0) && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base font-semibold flex items-center gap-2">
                  コンプライアンスチェック結果
                  {result.compliance_warnings.length > 0 ? (
                    <Badge variant="destructive">{result.compliance_warnings.length}件の注意</Badge>
                  ) : (
                    <Badge className="bg-green-100 text-green-800">問題なし</Badge>
                  )}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {result.compliance_warnings.length > 0 && (
                  <div className="space-y-1.5">
                    <p className="text-xs font-medium text-destructive">注意が必要な項目</p>
                    <ul className="space-y-1">
                      {result.compliance_warnings.map((w, i) => (
                        <li
                          key={i}
                          className="flex items-start gap-2 text-sm text-destructive bg-destructive/5 rounded px-3 py-2"
                        >
                          <span aria-hidden="true" className="shrink-0 mt-0.5">⚠</span>
                          {w}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {result.compliance_ok_items.length > 0 && (
                  <div className="space-y-1.5">
                    <p className="text-xs font-medium text-muted-foreground">確認済み・参考情報</p>
                    <ul className="space-y-1">
                      {result.compliance_ok_items.map((item, i) => (
                        <li
                          key={i}
                          className="flex items-start gap-2 text-xs text-muted-foreground px-3 py-1"
                        >
                          <span aria-hidden="true" className="shrink-0">✓</span>
                          {item}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {/* 生成された各章 */}
          <div className="space-y-3">
            {/* 施工方針 */}
            <AccordionSection title="施工方針" defaultOpen>
              <p className="text-sm leading-relaxed whitespace-pre-wrap">
                {result.construction_policy}
              </p>
            </AccordionSection>

            {/* 施工方法 */}
            <AccordionSection title="施工方法（工種別）" defaultOpen>
              <div className="space-y-3">
                <p className="text-sm leading-relaxed text-muted-foreground">
                  {result.construction_methods.overview}
                </p>
                {result.construction_methods.work_type_details?.length > 0 && (
                  <div className="space-y-3 mt-2">
                    {result.construction_methods.work_type_details.map((detail, i) => (
                      <div key={i} className="rounded-md border p-3 space-y-1">
                        <p className="text-sm font-medium">{detail.work_type}</p>
                        <p className="text-sm leading-relaxed text-muted-foreground">
                          {detail.method}
                        </p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </AccordionSection>

            {/* 安全管理計画 */}
            <AccordionSection title="安全管理計画" defaultOpen>
              <div className="space-y-4">
                <p className="text-sm leading-relaxed">
                  {result.safety_management_plan.policy}
                </p>
                <RiskTable items={result.safety_management_plan.high_risk_items} level="高" />
                <RiskTable items={result.safety_management_plan.mid_risk_items} level="中" />
                {result.safety_management_plan.daily_activities?.length > 0 && (
                  <div className="space-y-1.5">
                    <p className="text-sm font-medium">日常の安全活動</p>
                    <ul className="space-y-1">
                      {result.safety_management_plan.daily_activities.map((act, i) => (
                        <li key={i} className="text-sm flex items-start gap-2">
                          <span aria-hidden="true" className="text-muted-foreground shrink-0">・</span>
                          {act}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                <p className="text-xs text-muted-foreground border-t pt-3">
                  {result.safety_management_plan.emergency_contacts}
                </p>
              </div>
            </AccordionSection>

            {/* 品質管理計画 */}
            <AccordionSection title="品質管理計画">
              <div className="space-y-3">
                <p className="text-sm leading-relaxed">
                  {result.quality_management_plan.policy}
                </p>
                {result.quality_management_plan.checkpoints?.length > 0 && (
                  <div className="space-y-1.5">
                    <p className="text-sm font-medium">管理項目</p>
                    <ul className="space-y-1">
                      {result.quality_management_plan.checkpoints.map((cp, i) => (
                        <li key={i} className="text-sm flex items-start gap-2">
                          <span aria-hidden="true" className="text-muted-foreground shrink-0">・</span>
                          {cp}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </AccordionSection>

            {/* 環境対策 */}
            <AccordionSection title="環境対策">
              <div className="space-y-3">
                <p className="text-sm leading-relaxed">
                  {result.environmental_measures.policy}
                </p>
                {result.environmental_measures.measures?.length > 0 && (
                  <ul className="space-y-1">
                    {result.environmental_measures.measures.map((m, i) => (
                      <li key={i} className="text-sm flex items-start gap-2">
                        <span aria-hidden="true" className="text-muted-foreground shrink-0">・</span>
                        {m}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </AccordionSection>

            {/* 工程計画 */}
            <AccordionSection title="工程計画">
              <p className="text-sm leading-relaxed whitespace-pre-wrap">
                {result.schedule_overview}
              </p>
            </AccordionSection>
          </div>
        </div>
      ) : (
        /* 入力フォーム */
        <form onSubmit={handleSubmit} className="space-y-6" noValidate>
          {/* エラーバナー */}
          {apiError && (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
              {apiError}
            </div>
          )}

          {/* 基本情報 */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base font-semibold">工事の基本情報</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* 工事名 */}
              <div className="space-y-1.5">
                <Label htmlFor="project_name">
                  工事名
                  <span className="ml-1 text-destructive text-xs" aria-label="必須">*</span>
                </Label>
                <Input
                  id="project_name"
                  name="project_name"
                  value={form.project_name}
                  onChange={handleChange}
                  placeholder="例: 〇〇市道改良工事（第3工区）"
                  className="w-full"
                  aria-required="true"
                  aria-invalid={!!errors.project_name}
                />
                {errors.project_name && (
                  <p className="text-xs text-destructive">{errors.project_name}</p>
                )}
              </div>

              {/* 工事種別・発注者種別 */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <Label htmlFor="project_type">工事種別</Label>
                  <Select
                    id="project_type"
                    name="project_type"
                    value={form.project_type}
                    onChange={handleChange}
                    className="w-full"
                  >
                    <option value="土木">土木</option>
                    <option value="建築">建築</option>
                    <option value="道路">道路</option>
                    <option value="橋梁">橋梁</option>
                    <option value="河川">河川</option>
                    <option value="上下水道">上下水道</option>
                    <option value="造成">造成</option>
                    <option value="RC造">RC造</option>
                    <option value="S造">S造</option>
                    <option value="木造">木造</option>
                    <option value="解体">解体</option>
                    <option value="内装">内装</option>
                    <option value="設備">設備</option>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="owner_type">発注者種別</Label>
                  <Select
                    id="owner_type"
                    name="owner_type"
                    value={form.owner_type}
                    onChange={handleChange}
                    className="w-full"
                  >
                    <option value="公共">公共</option>
                    <option value="民間">民間</option>
                  </Select>
                </div>
              </div>

              {/* 工事規模 */}
              <div className="space-y-1.5">
                <Label htmlFor="scale">
                  工事規模
                  <span className="ml-1 text-destructive text-xs" aria-label="必須">*</span>
                </Label>
                <Input
                  id="scale"
                  name="scale"
                  value={form.scale}
                  onChange={handleChange}
                  placeholder="例: 橋長45m、幅員6m / 延床面積3,000m2、地上5階"
                  className="w-full"
                  aria-invalid={!!errors.scale}
                />
                {errors.scale && (
                  <p className="text-xs text-destructive">{errors.scale}</p>
                )}
              </div>

              {/* 施工条件 */}
              <div className="space-y-1.5">
                <Label htmlFor="conditions">施工条件</Label>
                <Textarea
                  id="conditions"
                  name="conditions"
                  value={form.conditions}
                  onChange={handleChange}
                  placeholder="例: 交通規制あり（片側通行）、夜間作業含む、近接施工（既存構造物より1m以内）"
                  rows={3}
                  className="w-full"
                />
              </div>

              {/* 工種 */}
              <div className="space-y-1.5">
                <Label htmlFor="work_types">工種（カンマ区切りで複数入力）</Label>
                <Input
                  id="work_types"
                  name="work_types"
                  value={form.work_types}
                  onChange={handleChange}
                  placeholder="例: コンクリート補修工事, 塗装工事, 足場工事"
                  className="w-full"
                />
              </div>
            </CardContent>
          </Card>

          {/* 現場情報 */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base font-semibold">現場・工期情報</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* 現場名 */}
              <div className="space-y-1.5">
                <Label htmlFor="site_name">現場名</Label>
                <Input
                  id="site_name"
                  name="site_name"
                  value={form.site_name}
                  onChange={handleChange}
                  placeholder="例: 〇〇市道3丁目現場"
                  className="w-full"
                />
              </div>

              {/* 工期 */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <Label htmlFor="start_date">工期開始日</Label>
                  <Input
                    id="start_date"
                    name="start_date"
                    type="date"
                    value={form.start_date}
                    onChange={handleChange}
                    className="w-full"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="end_date">工期終了日</Label>
                  <Input
                    id="end_date"
                    name="end_date"
                    type="date"
                    value={form.end_date}
                    onChange={handleChange}
                    className="w-full"
                  />
                </div>
              </div>
            </CardContent>
          </Card>

          {/* 担当者情報 */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base font-semibold">担当者情報</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <Label htmlFor="superintendent">現場代理人</Label>
                  <Input
                    id="superintendent"
                    name="superintendent"
                    value={form.superintendent}
                    onChange={handleChange}
                    placeholder="例: 山田 太郎"
                    className="w-full"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="safety_manager">安全管理者</Label>
                  <Input
                    id="safety_manager"
                    name="safety_manager"
                    value={form.safety_manager}
                    onChange={handleChange}
                    placeholder="例: 鈴木 一郎"
                    className="w-full"
                  />
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                現場代理人は建設業法第19条の2、安全管理者は労働安全衛生法第11条に基づく必須記載事項です。
              </p>
            </CardContent>
          </Card>

          {/* ローディング中 */}
          {loading && (
            <div className="flex flex-col items-center gap-3 py-8">
              <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
              <p className="text-sm text-muted-foreground">AIが施工計画書を生成しています...</p>
              <p className="text-xs text-muted-foreground">
                危険要因の分析・各章の生成・コンプライアンスチェックを順に行っています。しばらくお待ちください。
              </p>
            </div>
          )}

          {/* 送信ボタン */}
          {!loading && (
            <Button type="submit" size="lg" className="w-full sm:w-auto">
              施工計画書を生成する
            </Button>
          )}
        </form>
      )}
    </div>
  );
}
