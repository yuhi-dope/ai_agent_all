"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

// ---------- Types ----------

type SubIndustry =
  | "metal_processing"
  | "plastic"
  | "machine_manufacturing"
  | "electronics"
  | "food_manufacturing"
  | "chemical"
  | "auto_parts"
  | "other";

type EmployeeRange = "10-50" | "51-100" | "101-200" | "201-300";

type DataImportMethod = "template" | "csv" | "connector";

interface WizardState {
  industry: "manufacturing";
  sub_industry: SubIndustry | null;
  employee_range: EmployeeRange | null;
  departments: string[];
  extra_department: string;
  data_import_method: DataImportMethod | null;
  selected_pipelines: string[];
}

// ---------- Constants ----------

const SUB_INDUSTRY_OPTIONS: { key: SubIndustry; label: string }[] = [
  { key: "metal_processing", label: "金属加工" },
  { key: "plastic", label: "樹脂加工" },
  { key: "machine_manufacturing", label: "機械製造" },
  { key: "electronics", label: "電子部品" },
  { key: "food_manufacturing", label: "食品製造" },
  { key: "chemical", label: "化学製品" },
  { key: "auto_parts", label: "自動車部品" },
  { key: "other", label: "その他" },
];

const EMPLOYEE_RANGE_OPTIONS: { key: EmployeeRange; label: string }[] = [
  { key: "10-50", label: "10〜50名" },
  { key: "51-100", label: "51〜100名" },
  { key: "101-200", label: "101〜200名" },
  { key: "201-300", label: "201〜300名" },
];

const DEFAULT_DEPARTMENTS = [
  "製造",
  "品質管理",
  "生産管理",
  "営業",
  "原価・在庫管理",
];

const PIPELINE_OPTIONS: {
  key: string;
  label: string;
  description: string;
  icon: string;
}[] = [
  {
    key: "quoting",
    label: "見積AI",
    description: "過去の実績をもとに適切な見積金額を自動算出します",
    icon: "📋",
  },
  {
    key: "quality_control",
    label: "品質管理AI",
    description: "不良品の傾向を分析し、品質改善のヒントを提示します",
    icon: "🔍",
  },
  {
    key: "inventory_optimization",
    label: "在庫最適化AI",
    description: "需要予測にもとづき適正在庫量を提案します",
    icon: "📦",
  },
  {
    key: "production_planning",
    label: "生産計画AI",
    description: "受注・在庫・設備稼働を考慮した生産スケジュールを作成します",
    icon: "🏭",
  },
  {
    key: "equipment_maintenance",
    label: "設備保全AI",
    description: "設備の稼働データから予防保全のタイミングを提案します",
    icon: "🔧",
  },
  {
    key: "iso_document",
    label: "ISO文書管理AI",
    description: "ISO規格に必要な文書の作成・更新をサポートします",
    icon: "📄",
  },
  {
    key: "procurement",
    label: "調達AI",
    description: "仕入れ先の比較・発注タイミングの最適化を行います",
    icon: "🛒",
  },
  {
    key: "sop_management",
    label: "SOP管理AI",
    description: "作業手順書（SOP）の作成・バージョン管理を自動化します",
    icon: "📝",
  },
];

// ---------- Step Progress Bar ----------

const STEP_LABELS = [
  "業種確認",
  "部門設定",
  "データ投入",
  "自動化選択",
  "確認",
];

function StepProgressBar({ step }: { step: number }) {
  const total = STEP_LABELS.length;
  const progressPct = ((step - 1) / (total - 1)) * 100;

  return (
    <div className="w-full space-y-3">
      {/* Step labels — scroll on small screens */}
      <div className="flex items-start justify-between gap-1">
        {STEP_LABELS.map((label, i) => {
          const num = i + 1;
          const isActive = step === num;
          const isDone = step > num;
          return (
            <div key={num} className="flex flex-1 flex-col items-center gap-1 min-w-0">
              <div
                className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold transition-colors ${
                  isDone
                    ? "bg-primary text-primary-foreground"
                    : isActive
                    ? "bg-primary text-primary-foreground ring-2 ring-primary ring-offset-2"
                    : "bg-muted text-muted-foreground"
                }`}
              >
                {isDone ? "✓" : num}
              </div>
              <span
                className={`text-center text-[11px] leading-tight ${
                  isActive || isDone
                    ? "font-medium text-primary"
                    : "text-muted-foreground"
                }`}
              >
                {label}
              </span>
            </div>
          );
        })}
      </div>
      {/* Progress bar */}
      <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
        <div
          className="h-full rounded-full bg-primary transition-all duration-500"
          style={{ width: `${progressPct}%` }}
        />
      </div>
      <p className="text-right text-xs text-muted-foreground">
        ステップ {step} / {total}
      </p>
    </div>
  );
}

// ---------- Page ----------

export default function SetupWizardPage() {
  const router = useRouter();

  const [step, setStep] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [state, setState] = useState<WizardState>({
    industry: "manufacturing",
    sub_industry: null,
    employee_range: null,
    departments: [...DEFAULT_DEPARTMENTS],
    extra_department: "",
    data_import_method: null,
    selected_pipelines: PIPELINE_OPTIONS.map((p) => p.key),
  });

  function updateState(partial: Partial<WizardState>) {
    setState((prev) => ({ ...prev, ...partial }));
  }

  function toggleDepartment(dept: string) {
    setState((prev) => ({
      ...prev,
      departments: prev.departments.includes(dept)
        ? prev.departments.filter((d) => d !== dept)
        : [...prev.departments, dept],
    }));
  }

  function togglePipeline(key: string) {
    setState((prev) => ({
      ...prev,
      selected_pipelines: prev.selected_pipelines.includes(key)
        ? prev.selected_pipelines.filter((k) => k !== key)
        : [...prev.selected_pipelines, key],
    }));
  }

  function addExtraDepartment() {
    const trimmed = state.extra_department.trim();
    if (!trimmed || state.departments.includes(trimmed)) return;
    setState((prev) => ({
      ...prev,
      departments: [...prev.departments, trimmed],
      extra_department: "",
    }));
  }

  function canAdvance(): boolean {
    switch (step) {
      case 1:
        return !!state.sub_industry && !!state.employee_range;
      case 2:
        return state.departments.length > 0;
      case 3:
        return !!state.data_import_method;
      case 4:
        return state.selected_pipelines.length > 0;
      default:
        return true;
    }
  }

  async function handleFinish() {
    setLoading(true);
    setError(null);
    try {
      await apiFetch<{ message: string }>("/onboarding/setup-wizard", {
        method: "POST",
        body: {
          industry: state.industry,
          sub_industry: state.sub_industry,
          employee_range: state.employee_range,
          departments: state.departments,
          data_import_method: state.data_import_method,
          selected_pipelines: state.selected_pipelines,
        },
      });

      // If CSV/connector selected, redirect to respective page
      if (state.data_import_method === "csv") {
        router.push("/settings/connectors?tab=csv&from=setup-wizard");
      } else if (state.data_import_method === "connector") {
        router.push("/settings/connectors?from=setup-wizard");
      } else {
        router.push("/onboarding?from=setup-wizard");
      }
    } catch {
      setError(
        "セットアップの開始に失敗しました。しばらく経ってから再度お試しください。"
      );
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-8 pb-12">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">製造業 初回セットアップ</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          5ステップで業務自動化の準備を整えましょう。約5分で完了します。
        </p>
      </div>

      {/* Progress */}
      <StepProgressBar step={step} />

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* ── Step 1: 業種確認 ── */}
      {step === 1 && (
        <div className="space-y-6">
          <div>
            <h2 className="text-lg font-semibold">業種を確認してください</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              業種・規模に合わせた初期設定が自動で適用されます。
            </p>
          </div>

          {/* 業種（固定：製造業） */}
          <div className="space-y-2">
            <Label>業種</Label>
            <div className="flex items-center gap-3 rounded-xl border-2 border-primary bg-primary/5 px-4 py-3 w-fit">
              <span className="text-2xl">🏭</span>
              <span className="text-sm font-medium text-primary">製造業</span>
              <Badge className="bg-primary/10 text-primary text-xs">選択中</Badge>
            </div>
          </div>

          {/* サブ業種 */}
          <div className="space-y-2">
            <Label>
              製造品目
              <span className="ml-1 text-destructive">*</span>
            </Label>
            <p className="text-xs text-muted-foreground">
              最も近い品目を1つ選択してください。
            </p>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 sm:gap-3">
              {SUB_INDUSTRY_OPTIONS.map((opt) => {
                const isSelected = state.sub_industry === opt.key;
                return (
                  <button
                    key={opt.key}
                    type="button"
                    onClick={() => updateState({ sub_industry: opt.key })}
                    className={`rounded-lg border-2 px-3 py-2.5 text-sm font-medium transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-primary ${
                      isSelected
                        ? "border-primary bg-primary/5 text-primary shadow-sm"
                        : "border-border bg-card text-foreground hover:border-primary/50"
                    }`}
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
          </div>

          {/* 従業員数 */}
          <div className="space-y-2">
            <Label>
              従業員数
              <span className="ml-1 text-destructive">*</span>
            </Label>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 sm:gap-3">
              {EMPLOYEE_RANGE_OPTIONS.map((opt) => {
                const isSelected = state.employee_range === opt.key;
                return (
                  <button
                    key={opt.key}
                    type="button"
                    onClick={() => updateState({ employee_range: opt.key })}
                    className={`rounded-lg border-2 px-3 py-2.5 text-sm font-medium transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-primary ${
                      isSelected
                        ? "border-primary bg-primary/5 text-primary shadow-sm"
                        : "border-border bg-card text-foreground hover:border-primary/50"
                    }`}
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* ── Step 2: 部門設定 ── */}
      {step === 2 && (
        <div className="space-y-6">
          <div>
            <h2 className="text-lg font-semibold">社内の部門を設定してください</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              選択した部門に合わせた会社のルール・ノウハウが登録されます。製造業に多い部門をあらかじめ選んでいます。
            </p>
          </div>

          {/* デフォルト部門チェックボックス */}
          <div className="space-y-2">
            <Label>部門一覧</Label>
            <div className="space-y-2">
              {DEFAULT_DEPARTMENTS.map((dept) => {
                const isChecked = state.departments.includes(dept);
                return (
                  <button
                    key={dept}
                    type="button"
                    onClick={() => toggleDepartment(dept)}
                    className={`flex w-full items-center gap-3 rounded-lg border-2 px-4 py-3 text-sm transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-primary ${
                      isChecked
                        ? "border-primary bg-primary/5"
                        : "border-border bg-card hover:border-primary/30"
                    }`}
                  >
                    <span
                      className={`flex h-5 w-5 shrink-0 items-center justify-center rounded border-2 text-xs font-bold transition-colors ${
                        isChecked
                          ? "border-primary bg-primary text-primary-foreground"
                          : "border-muted-foreground/50"
                      }`}
                    >
                      {isChecked && "✓"}
                    </span>
                    <span className={isChecked ? "font-medium text-foreground" : "text-muted-foreground"}>
                      {dept}
                    </span>
                  </button>
                );
              })}

              {/* 追加済みカスタム部門 */}
              {state.departments
                .filter((d) => !DEFAULT_DEPARTMENTS.includes(d))
                .map((dept) => (
                  <button
                    key={dept}
                    type="button"
                    onClick={() => toggleDepartment(dept)}
                    className="flex w-full items-center gap-3 rounded-lg border-2 border-primary bg-primary/5 px-4 py-3 text-sm transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                  >
                    <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded border-2 border-primary bg-primary text-xs font-bold text-primary-foreground">
                      ✓
                    </span>
                    <span className="font-medium text-foreground">{dept}</span>
                    <Badge variant="secondary" className="ml-auto text-xs">
                      追加済み
                    </Badge>
                  </button>
                ))}
            </div>
          </div>

          {/* 追加部門 自由入力 */}
          <div className="space-y-2">
            <Label htmlFor="extra-dept">部門を追加する（任意）</Label>
            <div className="flex gap-2">
              <Input
                id="extra-dept"
                value={state.extra_department}
                onChange={(e) => updateState({ extra_department: e.target.value })}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addExtraDepartment();
                  }
                }}
                placeholder="例: 設計、資材調達、総務"
                className="flex-1"
              />
              <Button
                type="button"
                variant="outline"
                onClick={addExtraDepartment}
                disabled={!state.extra_department.trim()}
              >
                追加する
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* ── Step 3: データ投入方法 ── */}
      {step === 3 && (
        <div className="space-y-6">
          <div>
            <h2 className="text-lg font-semibold">初期データの投入方法を選んでください</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              あとから変更・追加もできます。まずはテンプレートで始めることをおすすめします。
            </p>
          </div>

          <div className="space-y-3">
            {/* Option 1: テンプレートで始める */}
            <button
              type="button"
              onClick={() => updateState({ data_import_method: "template" })}
              className={`flex w-full items-start gap-4 rounded-xl border-2 px-4 py-4 text-left transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-primary ${
                state.data_import_method === "template"
                  ? "border-primary bg-primary/5 shadow-sm"
                  : "border-border bg-card hover:border-primary/50"
              }`}
            >
              <span className="mt-0.5 shrink-0 text-2xl">📄</span>
              <div className="flex-1 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium">テンプレートだけで始める</span>
                  <Badge className="bg-green-100 text-green-800 text-xs">おすすめ</Badge>
                </div>
                <p className="text-xs text-muted-foreground">
                  製造業向けのひな形をすぐに適用できます。設定完了後すぐに使い始められます。
                </p>
                <p className="text-xs text-muted-foreground">
                  自動化の精度目安: <span className="font-medium text-foreground">60〜70%</span>（テンプレートのみ）→ データを追加するとさらに向上します
                </p>
              </div>
              {state.data_import_method === "template" && (
                <span className="shrink-0 text-primary font-bold">✓</span>
              )}
            </button>

            {/* Option 2: CSV投入 */}
            <button
              type="button"
              onClick={() => updateState({ data_import_method: "csv" })}
              className={`flex w-full items-start gap-4 rounded-xl border-2 px-4 py-4 text-left transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-primary ${
                state.data_import_method === "csv"
                  ? "border-primary bg-primary/5 shadow-sm"
                  : "border-border bg-card hover:border-primary/50"
              }`}
            >
              <span className="mt-0.5 shrink-0 text-2xl">📊</span>
              <div className="flex-1 space-y-1">
                <span className="text-sm font-medium">CSVでデータを一括投入する</span>
                <p className="text-xs text-muted-foreground">
                  既存の帳票・管理表をCSVで取り込み、精度を高めます。完了後にCSV取込画面へ移動します。
                </p>
                <p className="text-xs text-muted-foreground">
                  自動化の精度目安: <span className="font-medium text-foreground">80〜90%</span>（CSV投入後）
                </p>
              </div>
              {state.data_import_method === "csv" && (
                <span className="shrink-0 text-primary font-bold">✓</span>
              )}
            </button>

            {/* Option 3: SaaS連携 */}
            <button
              type="button"
              onClick={() => updateState({ data_import_method: "connector" })}
              className={`flex w-full items-start gap-4 rounded-xl border-2 px-4 py-4 text-left transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-primary ${
                state.data_import_method === "connector"
                  ? "border-primary bg-primary/5 shadow-sm"
                  : "border-border bg-card hover:border-primary/50"
              }`}
            >
              <span className="mt-0.5 shrink-0 text-2xl">🔗</span>
              <div className="flex-1 space-y-1">
                <span className="text-sm font-medium">SaaS連携でデータを自動取得する</span>
                <p className="text-xs text-muted-foreground">
                  kintone・freee・Slack等と連携して、データを自動で取得します。完了後にコネクタ設定画面へ移動します。
                </p>
                <p className="text-xs text-muted-foreground">
                  自動化の精度目安: <span className="font-medium text-foreground">80〜90%</span>（連携後）
                </p>
              </div>
              {state.data_import_method === "connector" && (
                <span className="shrink-0 text-primary font-bold">✓</span>
              )}
            </button>
          </div>
        </div>
      )}

      {/* ── Step 4: 業務フロー選択 ── */}
      {step === 4 && (
        <div className="space-y-6">
          <div>
            <h2 className="text-lg font-semibold">使いたい業務自動化を選んでください</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              製造業向けの自動化をすべて選択しています。不要なものは外してください。あとから変更できます。
            </p>
          </div>

          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">
              {state.selected_pipelines.length}/{PIPELINE_OPTIONS.length}件 選択中
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() =>
                  updateState({ selected_pipelines: PIPELINE_OPTIONS.map((p) => p.key) })
                }
                className="text-xs text-primary underline-offset-4 hover:underline focus:outline-none"
              >
                すべて選択
              </button>
              <span className="text-xs text-muted-foreground">/</span>
              <button
                type="button"
                onClick={() => updateState({ selected_pipelines: [] })}
                className="text-xs text-muted-foreground underline-offset-4 hover:underline focus:outline-none"
              >
                すべて外す
              </button>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {PIPELINE_OPTIONS.map((pipeline) => {
              const isSelected = state.selected_pipelines.includes(pipeline.key);
              const accuracyText =
                state.data_import_method === "template"
                  ? "精度目安: 60〜70%"
                  : "精度目安: 80〜90%";
              return (
                <button
                  key={pipeline.key}
                  type="button"
                  onClick={() => togglePipeline(pipeline.key)}
                  className={`flex items-start gap-3 rounded-xl border-2 p-4 text-left transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-primary ${
                    isSelected
                      ? "border-primary bg-primary/5 shadow-sm"
                      : "border-border bg-card hover:border-primary/30"
                  }`}
                >
                  <span className="mt-0.5 shrink-0 text-xl">{pipeline.icon}</span>
                  <div className="flex-1 space-y-1 min-w-0">
                    <div className="flex items-start justify-between gap-2">
                      <span
                        className={`text-sm font-medium ${
                          isSelected ? "text-foreground" : "text-muted-foreground"
                        }`}
                      >
                        {pipeline.label}
                      </span>
                      <span
                        className={`shrink-0 text-sm font-bold transition-colors ${
                          isSelected ? "text-primary" : "text-transparent"
                        }`}
                      >
                        ✓
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground">{pipeline.description}</p>
                    <p
                      className={`text-xs font-medium ${
                        isSelected ? "text-primary/70" : "text-muted-foreground/60"
                      }`}
                    >
                      {accuracyText}
                    </p>
                  </div>
                </button>
              );
            })}
          </div>

          {state.selected_pipelines.length === 0 && (
            <div className="rounded-lg border border-yellow-200 bg-yellow-50 px-4 py-3 text-sm text-yellow-800">
              少なくとも1つの業務自動化を選択してください。
            </div>
          )}
        </div>
      )}

      {/* ── Step 5: 確認・完了 ── */}
      {step === 5 && (
        <div className="space-y-6">
          <div>
            <h2 className="text-lg font-semibold">設定内容を確認してください</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              「セットアップを開始する」を押すと設定が反映されます。
            </p>
          </div>

          <div className="space-y-4">
            {/* 業種情報サマリー */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">業種・規模</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">業種</span>
                  <span className="font-medium">製造業</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">製造品目</span>
                  <span className="font-medium">
                    {SUB_INDUSTRY_OPTIONS.find((o) => o.key === state.sub_industry)?.label ?? "—"}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">従業員数</span>
                  <span className="font-medium">
                    {EMPLOYEE_RANGE_OPTIONS.find((o) => o.key === state.employee_range)?.label ?? "—"}
                  </span>
                </div>
              </CardContent>
            </Card>

            {/* 部門サマリー */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">部門（{state.departments.length}件）</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex flex-wrap gap-2">
                  {state.departments.map((d) => (
                    <Badge key={d} variant="secondary">
                      {d}
                    </Badge>
                  ))}
                </div>
              </CardContent>
            </Card>

            {/* データ投入サマリー */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">データ投入方法</CardTitle>
              </CardHeader>
              <CardContent className="text-sm">
                {state.data_import_method === "template" && (
                  <div className="flex items-center gap-2">
                    <span>📄</span>
                    <span>テンプレートだけで始める</span>
                    <Badge className="bg-green-100 text-green-800 text-xs">おすすめ</Badge>
                  </div>
                )}
                {state.data_import_method === "csv" && (
                  <div className="flex items-center gap-2">
                    <span>📊</span>
                    <span>CSVでデータを一括投入する</span>
                  </div>
                )}
                {state.data_import_method === "connector" && (
                  <div className="flex items-center gap-2">
                    <span>🔗</span>
                    <span>SaaS連携でデータを自動取得する</span>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* 業務自動化サマリー */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">
                  業務自動化（{state.selected_pipelines.length}件）
                </CardTitle>
                <CardDescription>
                  {state.data_import_method === "template"
                    ? "精度目安: 60〜70%（テンプレートのみ）"
                    : "精度目安: 80〜90%（データ投入後）"}
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="flex flex-wrap gap-2">
                  {state.selected_pipelines.map((key) => {
                    const pl = PIPELINE_OPTIONS.find((p) => p.key === key);
                    if (!pl) return null;
                    return (
                      <Badge key={key} variant="outline">
                        {pl.icon} {pl.label}
                      </Badge>
                    );
                  })}
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Submit button */}
          <Button
            size="lg"
            className="w-full"
            disabled={loading}
            onClick={handleFinish}
          >
            {loading ? (
              <>
                <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                セットアップを開始しています...
              </>
            ) : (
              "セットアップを開始する"
            )}
          </Button>
        </div>
      )}

      {/* ── Navigation ── */}
      {step < 5 && (
        <div className="flex items-center justify-between gap-3 border-t pt-4">
          <Button
            variant="outline"
            size="lg"
            disabled={step === 1}
            onClick={() => setStep((s) => (s - 1) as typeof step)}
            className="min-w-24"
          >
            戻る
          </Button>
          <Button
            size="lg"
            disabled={!canAdvance()}
            onClick={() => setStep((s) => (s + 1) as typeof step)}
            className="min-w-32"
          >
            次へ
          </Button>
        </div>
      )}

      {/* ── Back button on step 5 ── */}
      {step === 5 && (
        <div className="border-t pt-4">
          <Button
            variant="outline"
            size="lg"
            disabled={loading}
            onClick={() => setStep(4)}
            className="min-w-24"
          >
            戻る
          </Button>
        </div>
      )}
    </div>
  );
}
