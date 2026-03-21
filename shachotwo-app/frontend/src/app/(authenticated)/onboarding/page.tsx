"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
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

// ---------- Types ----------

interface OnboardingStatusResponse {
  industry: string | null;
  template_applied: boolean;
  knowledge_count: number;
  onboarding_progress: number;
  suggested_questions: string[];
}

// ---------- Constants ----------

// 利用可能な業種（ユーザーに直接選ばせる）
const AVAILABLE_INDUSTRY_OPTIONS = [
  { key: "construction", label: "建設業", icon: "🏗️" },
  { key: "manufacturing", label: "製造業", icon: "🏭" },
] as const;

// 準備中の業種（折りたたみセクションに表示）
const COMING_SOON_INDUSTRY_OPTIONS = [
  { key: "dental", label: "歯科", icon: "🦷" },
  { key: "food", label: "飲食業", icon: "🍽️" },
  { key: "beauty", label: "美容", icon: "💇" },
  { key: "logistics", label: "物流", icon: "🚛" },
  { key: "ec", label: "EC", icon: "🛒" },
  { key: "care", label: "介護", icon: "🤝" },
  { key: "realestate", label: "不動産", icon: "🏢" },
] as const;

type AvailableIndustryKey = (typeof AVAILABLE_INDUSTRY_OPTIONS)[number]["key"];
type ComingSoonIndustryKey = (typeof COMING_SOON_INDUSTRY_OPTIONS)[number]["key"];
type IndustryKey = AvailableIndustryKey | ComingSoonIndustryKey;

const ALL_INDUSTRY_OPTIONS = [
  ...AVAILABLE_INDUSTRY_OPTIONS.map((o) => ({ ...o, available: true as const })),
  ...COMING_SOON_INDUSTRY_OPTIONS.map((o) => ({ ...o, available: false as const })),
];

// ---------- Progress bar ----------

function StepProgressBar({ step }: { step: 1 | 2 | 3 }) {
  const steps = [
    { label: "業種選択", num: 1 },
    { label: "テンプレート適用", num: 2 },
    { label: "完了", num: 3 },
  ];

  return (
    <div className="w-full">
      <div className="flex items-center justify-between mb-2">
        {steps.map((s, i) => (
          <div key={s.num} className="flex items-center flex-1">
            <div className="flex flex-col items-center">
              <div
                className={`flex h-8 w-8 items-center justify-center rounded-full text-sm font-semibold transition-colors ${
                  step >= s.num
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-muted-foreground"
                }`}
              >
                {step > s.num ? "✓" : s.num}
              </div>
              <span
                className={`mt-1 text-xs ${
                  step >= s.num ? "text-primary font-medium" : "text-muted-foreground"
                }`}
              >
                {s.label}
              </span>
            </div>
            {i < steps.length - 1 && (
              <div
                className={`mx-2 h-0.5 flex-1 transition-colors ${
                  step > s.num ? "bg-primary" : "bg-muted"
                }`}
              />
            )}
          </div>
        ))}
      </div>
      {/* Progress bar */}
      <div className="mt-3 h-1.5 w-full rounded-full bg-muted overflow-hidden">
        <div
          className="h-full rounded-full bg-primary transition-all duration-500"
          style={{ width: `${((step - 1) / 2) * 100}%` }}
        />
      </div>
      <p className="mt-1 text-right text-xs text-muted-foreground">
        ステップ {step} / 3
      </p>
    </div>
  );
}

// ---------- Page ----------

export default function OnboardingPage() {
  const { session } = useAuth();
  const router = useRouter();

  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [selectedIndustry, setSelectedIndustry] = useState<IndustryKey | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [suggestedQuestions, setSuggestedQuestions] = useState<string[]>([]);
  const [appliedIndustryLabel, setAppliedIndustryLabel] = useState<string>("");
  const [isReturningUser, setIsReturningUser] = useState(false);
  const [isComingSoonExpanded, setIsComingSoonExpanded] = useState(false);

  // Check existing onboarding status on mount
  useEffect(() => {
    if (!session?.access_token) return;

    apiFetch<OnboardingStatusResponse>("/onboarding/status", {
      token: session.access_token,
    })
      .then((status) => {
        if (status.template_applied && status.onboarding_progress >= 1.0) {
          // Already completed — skip to step 3
          setSuggestedQuestions(status.suggested_questions ?? []);
          const found = ALL_INDUSTRY_OPTIONS.find((o) => o.key === status.industry);
          setAppliedIndustryLabel(found?.label ?? status.industry ?? "");
          setIsReturningUser(true);
          setStep(3);
        } else if (status.industry) {
          // Industry selected but not yet complete
          const key = status.industry as IndustryKey;
          setSelectedIndustry(key);
        }
      })
      .catch(() => {
        // Ignore fetch errors — user can start fresh
      });
  }, [session?.access_token]);

  async function handleApplyTemplate() {
    if (!selectedIndustry || !session?.access_token) return;
    setApplyError(null);
    setStep(2);

    try {
      await apiFetch<{ message: string }>("/onboarding/apply-template", {
        method: "POST",
        token: session.access_token,
        body: { industry: selectedIndustry },
      });

      // Fetch updated status to get suggested_questions
      const status = await apiFetch<OnboardingStatusResponse>("/onboarding/status", {
        token: session.access_token,
      });
      setSuggestedQuestions(status.suggested_questions ?? []);

      const found = ALL_INDUSTRY_OPTIONS.find((o) => o.key === selectedIndustry);
      setAppliedIndustryLabel(found?.label ?? selectedIndustry);

      setStep(3);
    } catch {
      setApplyError("テンプレートの適用に失敗しました。しばらく経ってから再度お試しください。");
      setStep(1);
    }
  }

  function handleQuestionClick(question: string) {
    router.push(`/knowledge/qa?q=${encodeURIComponent(question)}`);
  }

  return (
    <div className="mx-auto max-w-2xl space-y-8 pb-12">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">初期セットアップ</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          業種を選択してナレッジテンプレートを適用しましょう。
        </p>
      </div>

      {/* Progress */}
      <StepProgressBar step={step} />

      {/* Error */}
      {applyError && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          {applyError}
        </div>
      )}

      {/* Step 1: 業種選択 */}
      {step === 1 && (
        <div className="space-y-6">
          <div>
            <h2 className="text-lg font-semibold">業種を選択してください</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              選択した業種に合わせた「会社のルール・ノウハウ（ナレッジ）」のひな形が自動で登録されます。
            </p>
          </div>

          {/* 利用可能な業種 */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {AVAILABLE_INDUSTRY_OPTIONS.map((option) => {
              const isSelected = selectedIndustry === option.key;
              return (
                <button
                  key={option.key}
                  type="button"
                  onClick={() => setSelectedIndustry(option.key)}
                  className={`flex flex-col items-center gap-2 rounded-xl border-2 p-4 text-center transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-primary ${
                    isSelected
                      ? "border-primary bg-primary/5 shadow-md"
                      : "border-border bg-card hover:border-primary/50 hover:shadow-md"
                  }`}
                >
                  <span className="text-3xl">{option.icon}</span>
                  <span
                    className={`text-sm font-medium ${
                      isSelected ? "text-primary" : "text-foreground"
                    }`}
                  >
                    {option.label}
                  </span>
                  {isSelected && (
                    <Badge className="text-xs">選択中</Badge>
                  )}
                </button>
              );
            })}
          </div>

          {/* 準備中の業種（折りたたみ） */}
          <div className="rounded-lg border border-dashed border-muted-foreground/30 p-4 space-y-3">
            <button
              type="button"
              onClick={() => setIsComingSoonExpanded((prev) => !prev)}
              className="flex w-full items-center justify-between text-sm font-medium text-muted-foreground hover:text-foreground transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded"
            >
              <span>その他の業種（準備中）</span>
              <span aria-hidden="true">{isComingSoonExpanded ? "▲" : "▼"}</span>
            </button>

            {isComingSoonExpanded && (
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                  {COMING_SOON_INDUSTRY_OPTIONS.map((option) => (
                    <div
                      key={option.key}
                      className="flex flex-col items-center gap-2 rounded-xl border-2 border-border bg-muted p-4 text-center opacity-60 cursor-not-allowed"
                    >
                      <span className="text-3xl">{option.icon}</span>
                      <span className="text-sm font-medium text-muted-foreground">
                        {option.label}
                      </span>
                      <Badge variant="secondary" className="text-xs">準備中</Badge>
                    </div>
                  ))}
                </div>
                <p className="text-xs text-muted-foreground text-center">
                  対応開始時にお知らせします。
                </p>
              </div>
            )}
          </div>

          <div className="flex justify-end">
            <Button
              onClick={handleApplyTemplate}
              disabled={!selectedIndustry}
              size="lg"
              className="min-w-40"
            >
              テンプレートを適用する
            </Button>
          </div>
        </div>
      )}

      {/* Step 2: 適用中 */}
      {step === 2 && (
        <Card>
          <CardContent className="flex flex-col items-center gap-6 py-16">
            <div className="relative flex items-center justify-center">
              <div className="h-16 w-16 animate-spin rounded-full border-4 border-primary border-t-transparent" />
              <span className="absolute text-2xl">
                {ALL_INDUSTRY_OPTIONS.find((o) => o.key === selectedIndustry)?.icon ?? "⚙️"}
              </span>
            </div>
            <div className="text-center">
              <p className="text-lg font-semibold">業界ナレッジを初期設定中...</p>
              <p className="mt-2 text-sm text-muted-foreground">
                {ALL_INDUSTRY_OPTIONS.find((o) => o.key === selectedIndustry)?.label ?? ""}
                のテンプレートを適用しています。しばらくお待ちください。
              </p>
              <p className="mt-3 text-xs text-muted-foreground">
                通常10〜20秒で完了します
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Step 3: 完了 */}
      {step === 3 && (
        <div className="space-y-6">
          {/* 再訪ユーザー向けメッセージ */}
          {isReturningUser && (
            <div className="rounded-lg border border-primary/30 bg-primary/5 px-4 py-3 text-sm text-primary font-medium">
              すでにセットアップが完了しています
            </div>
          )}

          <Card className="border-primary/30 bg-primary/5">
            <CardHeader>
              <div className="flex items-center gap-3">
                <span className="text-4xl">🎉</span>
                <div>
                  <CardTitle>セットアップ完了！</CardTitle>
                  <CardDescription className="mt-1">
                    {appliedIndustryLabel && (
                      <>
                        <Badge variant="secondary" className="mr-2">
                          {appliedIndustryLabel}
                        </Badge>
                      </>
                    )}
                    のナレッジテンプレートが適用されました。
                  </CardDescription>
                </div>
              </div>
            </CardHeader>
          </Card>

          {/* Suggested Questions */}
          {suggestedQuestions.length > 0 && (
            <div className="space-y-3">
              <h2 className="text-base font-semibold">さっそくAIに聞いてみましょう</h2>
              <p className="text-sm text-muted-foreground">
                以下の質問例をクリックすると、AIがすぐに答えます。
              </p>
              <div className="space-y-2">
                {suggestedQuestions.map((q, i) => (
                  <button
                    key={i}
                    type="button"
                    onClick={() => handleQuestionClick(q)}
                    className="flex w-full items-center gap-3 rounded-lg border bg-card px-4 py-3 text-left text-sm transition-colors hover:border-primary/50 hover:bg-primary/5 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                  >
                    <span className="shrink-0 text-primary">Q</span>
                    <span>{q}</span>
                    <span className="ml-auto shrink-0 text-muted-foreground">→</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Action Buttons — AIに質問する が最優先CTA */}
          <div className="flex flex-col gap-3">
            <Button
              size="lg"
              className="w-full"
              onClick={() => router.push("/knowledge/qa")}
            >
              AIに質問する
            </Button>
            <div className="flex flex-col gap-2 sm:flex-row">
              <Button
                variant="outline"
                size="sm"
                className="flex-1"
                onClick={() => router.push("/knowledge/input")}
              >
                ナレッジを入力する
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="flex-1"
                onClick={() => router.push("/bpo")}
              >
                業務自動化を試す
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
