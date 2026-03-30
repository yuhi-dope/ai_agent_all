"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { apiFetch } from "@/lib/api";

// ---------- 型定義 ----------

export interface StepResult {
  step_no: number;
  step_name: string;
  result_summary: string;
  confidence: number;
}

export interface PipelineFeedbackProps {
  executionId: string;
  pipelineName: string;
  stepResults: StepResult[];
  onFeedbackSubmit?: () => void;
}

interface StepFeedbackState {
  approved: boolean | null; // null = 未選択
  comment: string;
}

// ---------- 確度ラベル変換（UI_RULES準拠） ----------

function confidenceLabel(confidence: number): {
  label: string;
  badgeClass: string;
} {
  if (confidence >= 0.8)
    return {
      label: "確度：高",
      badgeClass: "bg-green-100 text-green-800",
    };
  if (confidence >= 0.6)
    return {
      label: "確度：中",
      badgeClass: "bg-yellow-100 text-yellow-800",
    };
  return {
    label: "要確認",
    badgeClass: "bg-orange-100 text-orange-800",
  };
}

// ---------- ステップフィードバック行 ----------

interface StepFeedbackRowProps {
  step: StepResult;
  state: StepFeedbackState;
  isOpen: boolean;
  onToggle: () => void;
  onThumbUp: () => void;
  onThumbDown: () => void;
  onCommentChange: (value: string) => void;
}

function StepFeedbackRow({
  step,
  state,
  isOpen,
  onToggle,
  onThumbUp,
  onThumbDown,
  onCommentChange,
}: StepFeedbackRowProps) {
  const { label, badgeClass } = confidenceLabel(step.confidence);

  return (
    <div className="rounded-md border bg-background">
      {/* アコーディオンヘッダー */}
      <button
        type="button"
        className="w-full flex items-center justify-between gap-3 p-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 rounded-md"
        onClick={onToggle}
        aria-expanded={isOpen}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="shrink-0 text-xs font-mono text-muted-foreground w-6 text-center">
            {step.step_no}
          </span>
          <span className="text-sm font-medium truncate">{step.step_name}</span>
          <span className={`shrink-0 inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${badgeClass}`}>
            {label}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {/* 👍/👎 ステータス表示 */}
          {state.approved === true && (
            <span className="text-green-600 text-sm" aria-label="良い">👍</span>
          )}
          {state.approved === false && (
            <span className="text-destructive text-sm" aria-label="修正が必要">👎</span>
          )}
          <span className="text-muted-foreground text-xs" aria-hidden="true">
            {isOpen ? "▲" : "▼"}
          </span>
        </div>
      </button>

      {/* アコーディオンコンテンツ */}
      {isOpen && (
        <div className="border-t px-3 pb-3 pt-2 space-y-3">
          <p className="text-sm text-muted-foreground leading-relaxed">
            {step.result_summary}
          </p>
          {step.confidence < 0.6 && (
            <p className="text-xs text-orange-700 bg-orange-50 rounded px-2 py-1">
              データを追加すると精度が向上します
            </p>
          )}
          {/* 👍/👎 ボタン */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">このステップの評価:</span>
            <button
              type="button"
              onClick={onThumbUp}
              className={`inline-flex items-center gap-1 rounded-md px-3 py-1.5 text-sm border transition-colors ${
                state.approved === true
                  ? "border-green-500 bg-green-50 text-green-700"
                  : "border-border bg-background hover:bg-muted"
              }`}
              aria-pressed={state.approved === true}
            >
              👍 <span className="sr-only sm:not-sr-only">良い</span>
            </button>
            <button
              type="button"
              onClick={onThumbDown}
              className={`inline-flex items-center gap-1 rounded-md px-3 py-1.5 text-sm border transition-colors ${
                state.approved === false
                  ? "border-destructive bg-destructive/5 text-destructive"
                  : "border-border bg-background hover:bg-muted"
              }`}
              aria-pressed={state.approved === false}
            >
              👎 <span className="sr-only sm:not-sr-only">修正が必要</span>
            </button>
          </div>

          {/* 👎時のフィードバックテキスト */}
          {state.approved === false && (
            <div className="space-y-1">
              <Label htmlFor={`step-comment-${step.step_no}`} className="text-xs">
                修正内容を教えてください
              </Label>
              <Textarea
                id={`step-comment-${step.step_no}`}
                placeholder="例: 金額の計算方法が違います。材料費は税抜き価格で計算してください"
                value={state.comment}
                onChange={(e) => onCommentChange(e.target.value)}
                rows={2}
                className="text-sm"
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------- メインコンポーネント ----------

export function PipelineFeedback({
  executionId,
  pipelineName,
  stepResults,
  onFeedbackSubmit,
}: PipelineFeedbackProps) {
  // 全体承認/却下
  const [overallApproved, setOverallApproved] = useState<boolean | null>(null);
  const [overallComment, setOverallComment] = useState("");

  // ステップ別フィードバック
  const [stepFeedbacks, setStepFeedbacks] = useState<Record<number, StepFeedbackState>>(
    () =>
      Object.fromEntries(
        stepResults.map((s) => [s.step_no, { approved: null, comment: "" }])
      )
  );

  // 開閉状態（アコーディオン）
  const [openSteps, setOpenSteps] = useState<Set<number>>(new Set());

  // 送信状態
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitSuccess, setSubmitSuccess] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  function toggleStep(stepNo: number) {
    setOpenSteps((prev) => {
      const next = new Set(prev);
      next.has(stepNo) ? next.delete(stepNo) : next.add(stepNo);
      return next;
    });
  }

  function setStepApproval(stepNo: number, approved: boolean) {
    setStepFeedbacks((prev) => ({
      ...prev,
      [stepNo]: { ...prev[stepNo], approved },
    }));
  }

  function setStepComment(stepNo: number, comment: string) {
    setStepFeedbacks((prev) => ({
      ...prev,
      [stepNo]: { ...prev[stepNo], comment },
    }));
  }

  async function handleSubmit() {
    if (overallApproved === null) return;
    setIsSubmitting(true);
    setSubmitError(null);
    try {
      await apiFetch<unknown>(`/execution/${executionId}/feedback`, {
        method: "POST",
        body: {
          overall_approved: overallApproved,
          overall_comment: overallComment.trim(),
          step_feedbacks: stepResults
            .map((s) => {
              const fb = stepFeedbacks[s.step_no];
              if (fb.approved === null) return null;
              return {
                step_no: s.step_no,
                approved: fb.approved,
                comment: fb.comment.trim(),
              };
            })
            .filter(Boolean),
        },
      });
      setSubmitSuccess(true);
      onFeedbackSubmit?.();
    } catch {
      setSubmitError("フィードバックの送信に失敗しました。しばらく経ってからもう一度お試しください。");
    } finally {
      setIsSubmitting(false);
    }
  }

  if (submitSuccess) {
    return (
      <div className="rounded-lg border border-green-200 bg-green-50 px-4 py-6 text-center space-y-2">
        <span className="text-2xl" aria-hidden="true">✅</span>
        <p className="text-sm font-medium text-green-800">
          フィードバックを送信しました。ありがとうございます。
        </p>
        <p className="text-xs text-green-700">
          いただいた内容をもとにAIの精度を改善します。
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* 全体承認/却下バー */}
      <div className="sticky top-0 z-10 rounded-lg border bg-background shadow-sm p-3 space-y-3">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold">{pipelineName}の実行結果を確認</span>
          <Badge variant="outline" className="text-xs">フィードバック</Badge>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            className={`flex-1 sm:flex-none ${
              overallApproved === true
                ? "bg-green-600 hover:bg-green-700 text-white"
                : "bg-green-50 border border-green-300 text-green-700 hover:bg-green-100"
            }`}
            onClick={() => setOverallApproved(true)}
            aria-pressed={overallApproved === true}
          >
            ✓ 承認する
          </Button>
          <Button
            size="sm"
            variant={overallApproved === false ? "destructive" : "outline"}
            className="flex-1 sm:flex-none"
            onClick={() => setOverallApproved(false)}
            aria-pressed={overallApproved === false}
          >
            修正が必要
          </Button>
        </div>

        {/* 却下時: 修正内容入力 */}
        {overallApproved === false && (
          <div className="space-y-1">
            <Label htmlFor="overall-comment" className="text-xs">
              修正内容を教えてください（任意）
            </Label>
            <Textarea
              id="overall-comment"
              placeholder="例: 合計金額が違います。3ページ目の数量を確認してください"
              value={overallComment}
              onChange={(e) => setOverallComment(e.target.value)}
              rows={2}
              className="text-sm"
            />
          </div>
        )}
      </div>

      {/* ステップ別フィードバック */}
      {stepResults.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">
            ステップごとに評価することでAIの精度がさらに向上します（任意）
          </p>
          {stepResults.map((step) => (
            <StepFeedbackRow
              key={step.step_no}
              step={step}
              state={stepFeedbacks[step.step_no] ?? { approved: null, comment: "" }}
              isOpen={openSteps.has(step.step_no)}
              onToggle={() => toggleStep(step.step_no)}
              onThumbUp={() => setStepApproval(step.step_no, true)}
              onThumbDown={() => setStepApproval(step.step_no, false)}
              onCommentChange={(v) => setStepComment(step.step_no, v)}
            />
          ))}
        </div>
      )}

      {/* エラー表示 */}
      {submitError && (
        <p className="text-sm text-destructive">{submitError}</p>
      )}

      {/* 送信ボタン */}
      <Button
        className="w-full sm:w-auto"
        disabled={overallApproved === null || isSubmitting}
        onClick={handleSubmit}
        size="lg"
      >
        {isSubmitting ? (
          <>
            <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
            送信中...
          </>
        ) : (
          "AIにフィードバックを伝える"
        )}
      </Button>

      {overallApproved === null && (
        <p className="text-xs text-muted-foreground">
          「承認する」または「修正が必要」を選択してから送信できます
        </p>
      )}
    </div>
  );
}
