"use client";

import { useEffect, useState, useCallback } from "react";
import { usePathname } from "next/navigation";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";

const GUIDE_STORAGE = {
  COMPLETED: "step_guide_completed",
  CURRENT_STEP: "step_guide_current",
  DEFER_COUNT: "step_guide_defer_count",
  INDUSTRY_SELECTED: "step_guide_industry",
} as const;

const MAX_DEFER = 3;

const INDUSTRIES = [
  { key: "construction", label: "建設業" },
  { key: "manufacturing", label: "製造業" },
  { key: "medical", label: "医療・福祉" },
  { key: "realestate", label: "不動産" },
  { key: "logistics", label: "物流・運送" },
  { key: "wholesale", label: "卸売業" },
  { key: "other", label: "その他" },
] as const;

type Industry = (typeof INDUSTRIES)[number]["key"];

type Step = 1 | 2 | 3 | "complete";

interface QAResponse {
  answer: string;
  confidence?: string;
}

function shouldShowGuide(): boolean {
  if (typeof window === "undefined") return false;
  if (localStorage.getItem(GUIDE_STORAGE.COMPLETED) === "true") return false;
  const deferCount = parseInt(
    localStorage.getItem(GUIDE_STORAGE.DEFER_COUNT) ?? "0",
    10
  );
  if (deferCount >= MAX_DEFER) return false;
  return true;
}

function StepDots({ current }: { current: Step }) {
  const steps: Array<1 | 2 | 3> = [1, 2, 3];
  return (
    <div className="flex items-center justify-center gap-2 mb-6">
      {steps.map((s) => (
        <div
          key={s}
          className={`h-2.5 w-2.5 rounded-full transition-colors ${
            current === s
              ? "bg-blue-600"
              : current === "complete"
              ? "bg-blue-600"
              : typeof current === "number" && s < current
              ? "bg-blue-400"
              : "bg-gray-200"
          }`}
        />
      ))}
    </div>
  );
}

function getSampleQuestion(industry: Industry | null): string {
  switch (industry) {
    case "construction":
      return "「見積もりの諸経費率はいくらですか？」と聞いてみる";
    case "manufacturing":
      return "「製品の標準工数はどのように管理しますか？」と聞いてみる";
    case "medical":
      return "「スタッフのシフト管理はどのようにしていますか？」と聞いてみる";
    case "realestate":
      return "「賃貸物件の仲介手数料の上限はいくらですか？」と聞いてみる";
    case "logistics":
      return "「配送時の重量制限はどのくらいですか？」と聞いてみる";
    case "wholesale":
      return "「発注ロットの最小数量はどのくらいですか？」と聞いてみる";
    default:
      return "「御社の業務で大切にしていることは何ですか？」と聞いてみる";
  }
}

function getQuestionText(industry: Industry | null): string {
  switch (industry) {
    case "construction":
      return "見積もりの諸経費率はいくらですか？";
    case "manufacturing":
      return "製品の標準工数はどのように管理しますか？";
    case "medical":
      return "スタッフのシフト管理はどのようにしていますか？";
    case "realestate":
      return "賃貸物件の仲介手数料の上限はいくらですか？";
    case "logistics":
      return "配送時の重量制限はどのくらいですか？";
    case "wholesale":
      return "発注ロットの最小数量はどのくらいですか？";
    default:
      return "御社の業務で大切にしていることは何ですか？";
  }
}

function getKnowledgePlaceholder(industry: Industry | null): string {
  switch (industry) {
    case "construction":
      return "例: うちの諸経費率は28%です";
    case "manufacturing":
      return "例: 標準工数はExcelで管理しています";
    case "medical":
      return "例: シフトは2週間前までに確定します";
    case "realestate":
      return "例: 仲介手数料は原則1ヶ月分です";
    case "logistics":
      return "例: 最大積載量は2トンです";
    case "wholesale":
      return "例: 最小発注ロットは10個です";
    default:
      return "例: 見積書の有効期限は30日間";
  }
}

export function StepGuide() {
  const { session } = useAuth();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState<Step>(1);
  const [selectedIndustry, setSelectedIndustry] = useState<Industry | null>(null);
  const [applyingTemplate, setApplyingTemplate] = useState(false);
  const [templateApplied, setTemplateApplied] = useState(false);
  const [qaLoading, setQaLoading] = useState(false);
  const [qaAnswer, setQaAnswer] = useState<string | null>(null);
  const [knowledgeText, setKnowledgeText] = useState("");
  const [savingKnowledge, setSavingKnowledge] = useState(false);
  const [completing, setCompleting] = useState(false);

  // /dashboard ページでのみ表示
  const isDashboard =
    pathname === "/dashboard" || pathname === "/dashboard/";

  useEffect(() => {
    if (!isDashboard) return;
    const savedStep = localStorage.getItem(GUIDE_STORAGE.CURRENT_STEP);
    if (savedStep === "2") setStep(2);
    else if (savedStep === "3") setStep(3);
    else if (savedStep === "complete") setStep("complete");
    const savedIndustry = localStorage.getItem(GUIDE_STORAGE.INDUSTRY_SELECTED);
    if (savedIndustry) setSelectedIndustry(savedIndustry as Industry);
    if (shouldShowGuide()) {
      setOpen(true);
    }
  }, [isDashboard]);

  const handleDefer = useCallback(() => {
    const current = parseInt(
      localStorage.getItem(GUIDE_STORAGE.DEFER_COUNT) ?? "0",
      10
    );
    localStorage.setItem(GUIDE_STORAGE.DEFER_COUNT, String(current + 1));
    setOpen(false);
  }, []);

  const handleSelectIndustry = useCallback(
    async (industry: Industry) => {
      setSelectedIndustry(industry);
      localStorage.setItem(GUIDE_STORAGE.INDUSTRY_SELECTED, industry);
      setApplyingTemplate(true);
      try {
        await apiFetch("/onboarding/apply-template", {
          method: "POST",
          token: session?.access_token,
          body: { industry },
        });
        setTemplateApplied(true);
      } catch {
        // テンプレート適用失敗時も次へ進める
        setTemplateApplied(true);
      } finally {
        setApplyingTemplate(false);
      }
    },
    [session?.access_token]
  );

  const handleGoToStep2 = useCallback(() => {
    setStep(2);
    localStorage.setItem(GUIDE_STORAGE.CURRENT_STEP, "2");
  }, []);

  const handleTryQA = useCallback(async () => {
    setQaLoading(true);
    const question = getQuestionText(selectedIndustry);
    try {
      const res = await apiFetch<QAResponse>("/knowledge/qa", {
        method: "POST",
        token: session?.access_token,
        body: { question },
      });
      setQaAnswer(res.answer);
    } catch {
      setQaAnswer(
        "業界の一般的なルールをもとに回答できます。御社独自のルールを保存すると、より正確な回答ができます。"
      );
    } finally {
      setQaLoading(false);
      setStep(3);
      localStorage.setItem(GUIDE_STORAGE.CURRENT_STEP, "3");
    }
  }, [selectedIndustry, session?.access_token]);

  const handleSaveKnowledge = useCallback(async () => {
    if (!knowledgeText.trim()) return;
    setSavingKnowledge(true);
    try {
      await apiFetch("/knowledge/items", {
        method: "POST",
        token: session?.access_token,
        body: {
          content: knowledgeText.trim(),
          category: "その他",
        },
      });
    } catch {
      // 保存失敗時も完了画面へ進める
    } finally {
      setSavingKnowledge(false);
      handleComplete();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [knowledgeText, session?.access_token]);

  const handleComplete = useCallback(async () => {
    setCompleting(true);
    try {
      await apiFetch("/onboarding/complete-step", {
        method: "POST",
        token: session?.access_token,
        body: { step: "guide_completed" },
      });
    } catch {
      // サイレントフェイル
    } finally {
      localStorage.setItem(GUIDE_STORAGE.COMPLETED, "true");
      localStorage.setItem(GUIDE_STORAGE.CURRENT_STEP, "complete");
      setStep("complete");
      setCompleting(false);
    }
  }, [session?.access_token]);

  const handleFinish = useCallback(() => {
    setOpen(false);
  }, []);

  if (!isDashboard || !open) return null;

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) handleDefer(); }}>
      <DialogContent
        className="sm:max-w-lg w-full mx-2"
        showCloseButton={false}
      >
        {step !== "complete" && <StepDots current={step} />}

        {/* ステップ1: 業種を選ぶ */}
        {step === 1 && (
          <div className="space-y-5">
            <DialogHeader>
              <DialogTitle className="text-xl font-bold text-center">
                ようこそ、シャチョツーへ！
              </DialogTitle>
              <DialogDescription className="text-base text-center text-foreground mt-2">
                まずは業種を選んでください。
                <br />
                業界の基本ルールが自動で設定されます。
              </DialogDescription>
            </DialogHeader>

            {applyingTemplate ? (
              <div className="flex flex-col items-center gap-3 py-6">
                <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
                <p className="text-sm text-muted-foreground">
                  業界のルールを設定しています。少しお待ちください...
                </p>
              </div>
            ) : templateApplied && selectedIndustry ? (
              <div className="space-y-4">
                <div className="rounded-lg bg-green-50 border border-green-200 p-4 text-center">
                  <p className="text-green-700 font-medium text-base">
                    ✅ 「{INDUSTRIES.find((i) => i.key === selectedIndustry)?.label}」を設定しました
                  </p>
                </div>
                <Button
                  className="w-full h-11 text-base bg-blue-600 hover:bg-blue-700 text-white"
                  onClick={handleGoToStep2}
                >
                  次へ進む
                </Button>
                <button
                  type="button"
                  className="w-full text-sm text-muted-foreground underline-offset-4 hover:underline"
                  onClick={handleDefer}
                >
                  あとで設定する
                </button>
              </div>
            ) : (
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {INDUSTRIES.map((industry) => (
                    <button
                      key={industry.key}
                      type="button"
                      onClick={() => handleSelectIndustry(industry.key)}
                      className={`rounded-lg border px-3 py-3 text-base font-medium transition-all text-left sm:text-center ${
                        selectedIndustry === industry.key
                          ? "border-blue-500 bg-blue-50 text-blue-700 ring-2 ring-blue-400"
                          : "border-border bg-background hover:bg-muted hover:border-blue-300"
                      }`}
                    >
                      {industry.label}
                    </button>
                  ))}
                </div>
                <button
                  type="button"
                  className="w-full text-sm text-muted-foreground underline-offset-4 hover:underline"
                  onClick={handleDefer}
                >
                  あとで設定する
                </button>
              </div>
            )}
          </div>
        )}

        {/* ステップ2: 試しに質問する */}
        {step === 2 && (
          <div className="space-y-5">
            <DialogHeader>
              <DialogTitle className="text-xl font-bold text-center">
                ✅ 業種を設定しました！
              </DialogTitle>
              <DialogDescription className="text-base text-center text-foreground mt-2">
                業界の基本ルールを50件設定しました。
                <br />
                試しに質問してみましょう。
              </DialogDescription>
            </DialogHeader>

            {qaLoading ? (
              <div className="flex flex-col items-center gap-3 py-6">
                <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
                <p className="text-sm text-muted-foreground">
                  AIが回答を生成しています。少しお待ちください...
                </p>
              </div>
            ) : (
              <div className="space-y-4">
                <p className="text-sm text-muted-foreground text-center">
                  下のボタンを押すだけでOKです：
                </p>
                <Button
                  className="w-full h-auto min-h-[3rem] text-base bg-blue-600 hover:bg-blue-700 text-white py-3 whitespace-normal text-center leading-snug"
                  onClick={handleTryQA}
                >
                  {getSampleQuestion(selectedIndustry)}
                </Button>
                <button
                  type="button"
                  className="w-full text-sm text-muted-foreground underline-offset-4 hover:underline"
                  onClick={handleDefer}
                >
                  あとで試す
                </button>
              </div>
            )}
          </div>
        )}

        {/* ステップ3: 御社のルールを1つ保存する */}
        {step === 3 && (
          <div className="space-y-5">
            <DialogHeader>
              <DialogTitle className="text-xl font-bold text-center">
                ✅ AIが回答しました！
              </DialogTitle>
              <DialogDescription className="text-base text-center text-foreground mt-2">
                これは業界の一般的なルールです。
                <br />
                御社のルールを保存すると、御社専用の回答ができます。
              </DialogDescription>
            </DialogHeader>

            {qaAnswer && (
              <div className="rounded-lg bg-muted p-4">
                <p className="text-sm text-muted-foreground mb-1">AIの回答：</p>
                <p className="text-sm leading-relaxed">{qaAnswer}</p>
              </div>
            )}

            <div className="space-y-2">
              <label className="text-sm font-medium">
                御社のルールを1つ入力してみましょう
                <span className="ml-1 text-xs text-muted-foreground font-normal">（任意）</span>
              </label>
              <textarea
                value={knowledgeText}
                onChange={(e) => setKnowledgeText(e.target.value)}
                placeholder={getKnowledgePlaceholder(selectedIndustry)}
                rows={3}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-base resize-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring placeholder:text-muted-foreground"
              />
            </div>

            <div className="flex flex-col gap-2">
              <Button
                className="w-full h-11 text-base bg-blue-600 hover:bg-blue-700 text-white"
                onClick={handleSaveKnowledge}
                disabled={!knowledgeText.trim() || savingKnowledge}
              >
                {savingKnowledge ? (
                  <>
                    <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                    保存中...
                  </>
                ) : (
                  "このルールを保存する"
                )}
              </Button>
              <Button
                variant="outline"
                className="w-full h-11 text-base"
                onClick={handleComplete}
                disabled={completing}
              >
                {completing ? "完了中..." : "あとで入力する"}
              </Button>
            </div>
          </div>
        )}

        {/* 完了画面 */}
        {step === "complete" && (
          <div className="space-y-5">
            <div className="text-center py-2">
              <div className="text-5xl mb-4">🎉</div>
              <DialogTitle className="text-xl font-bold">
                設定完了！
              </DialogTitle>
              <DialogDescription className="text-base text-foreground mt-3">
                <p>シャチョツーを使う準備ができました。</p>
                <p className="text-sm text-muted-foreground mt-2">
                  いつでも左のメニューから各機能を使えます。
                </p>
                <p className="text-sm text-muted-foreground mt-2">
                  困ったときは右下の「困った」ボタンから
                  <br />
                  AIに質問できます。
                </p>
              </DialogDescription>
            </div>
            <Button
              className="w-full h-11 text-base bg-blue-600 hover:bg-blue-700 text-white"
              onClick={handleFinish}
            >
              はじめる
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
