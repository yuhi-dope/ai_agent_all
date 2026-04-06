"use client";

import { useEffect, useState, useCallback } from "react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";

const STORAGE_KEY_SHOWN_AT = "nps_survey_shown_at";
const STORAGE_KEY_SUBMITTED = "nps_submitted";
const STORAGE_KEY_DEFERRED_COUNT = "nps_deferred_count";

const MAX_DEFER_COUNT = 3;
const RESHOW_INTERVAL_DAYS = 7;
const SHOW_DELAY_MS = 5000;

function getScoreColor(score: number): string {
  if (score <= 6) return "bg-red-100 text-red-700 border-red-200 hover:bg-red-200";
  if (score <= 8) return "bg-yellow-100 text-yellow-700 border-yellow-200 hover:bg-yellow-200";
  return "bg-green-100 text-green-700 border-green-200 hover:bg-green-200";
}

function getScoreSelectedColor(score: number): string {
  if (score <= 6) return "bg-red-200 text-red-800 border-red-400 ring-2 ring-red-400";
  if (score <= 8) return "bg-yellow-200 text-yellow-800 border-yellow-400 ring-2 ring-yellow-400";
  return "bg-green-200 text-green-800 border-green-400 ring-2 ring-green-400";
}

function shouldShowSurvey(): boolean {
  if (typeof window === "undefined") return false;

  const submitted = localStorage.getItem(STORAGE_KEY_SUBMITTED);
  if (submitted === "true") return false;

  const deferredCount = parseInt(
    localStorage.getItem(STORAGE_KEY_DEFERRED_COUNT) ?? "0",
    10
  );
  if (deferredCount >= MAX_DEFER_COUNT) return false;

  const shownAt = localStorage.getItem(STORAGE_KEY_SHOWN_AT);
  if (!shownAt) return true;

  const lastShown = new Date(shownAt).getTime();
  const now = Date.now();
  const daysSinceShown = (now - lastShown) / (1000 * 60 * 60 * 24);
  return daysSinceShown >= RESHOW_INTERVAL_DAYS;
}

export function NPSModal() {
  const { session } = useAuth();
  const [open, setOpen] = useState(false);
  const [selectedScore, setSelectedScore] = useState<number | null>(null);
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [toast, setToast] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => {
      if (shouldShowSurvey()) {
        localStorage.setItem(STORAGE_KEY_SHOWN_AT, new Date().toISOString());
        setOpen(true);
      }
    }, SHOW_DELAY_MS);
    return () => clearTimeout(timer);
  }, []);

  const handleSubmit = useCallback(async () => {
    if (selectedScore === null) return;
    setSubmitting(true);
    try {
      await apiFetch("/company/nps", {
        method: "POST",
        token: session?.access_token,
        body: { score: selectedScore, comment: comment.trim() || undefined },
      });
    } catch {
      // サイレントフェイル
    } finally {
      localStorage.setItem(STORAGE_KEY_SUBMITTED, "true");
      setSubmitting(false);
      setOpen(false);
      setToast(true);
      setTimeout(() => setToast(false), 3000);
    }
  }, [selectedScore, comment, session?.access_token]);

  const handleDefer = useCallback(() => {
    const current = parseInt(
      localStorage.getItem(STORAGE_KEY_DEFERRED_COUNT) ?? "0",
      10
    );
    localStorage.setItem(STORAGE_KEY_DEFERRED_COUNT, String(current + 1));
    setOpen(false);
  }, []);

  return (
    <>
      <Dialog open={open} onOpenChange={(v) => { if (!v) handleDefer(); }}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="text-lg font-semibold">
              サービスについて教えてください
            </DialogTitle>
            <DialogDescription className="text-sm text-muted-foreground">
              友人や同僚にこのサービスをおすすめする可能性はどのくらいですか？
            </DialogDescription>
          </DialogHeader>

          <div className="mt-2 space-y-5">
            {/* スコアボタン 0〜10 */}
            <div>
              <div className="flex flex-wrap gap-1.5 justify-center">
                {Array.from({ length: 11 }, (_, i) => (
                  <button
                    key={i}
                    type="button"
                    onClick={() => setSelectedScore(i)}
                    className={`w-9 h-9 rounded-md border text-sm font-medium transition-all ${
                      selectedScore === i
                        ? getScoreSelectedColor(i)
                        : getScoreColor(i)
                    }`}
                    aria-label={`${i}点`}
                    aria-pressed={selectedScore === i}
                  >
                    {i}
                  </button>
                ))}
              </div>
              <div className="mt-1.5 flex justify-between px-1 text-xs text-muted-foreground">
                <span>全くおすすめしない</span>
                <span>ぜひおすすめしたい</span>
              </div>
            </div>

            {/* コメント欄 */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-foreground">
                お使いになった感想を一言いただけますか？
                <span className="ml-1 text-xs text-muted-foreground font-normal">（任意）</span>
              </label>
              <Textarea
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder="ご自由にお書きください..."
                rows={3}
                className="resize-none"
              />
            </div>

            {/* アクションボタン */}
            <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <Button
                variant="ghost"
                size="sm"
                onClick={handleDefer}
                disabled={submitting}
                className="text-muted-foreground"
              >
                後で答える
              </Button>
              <Button
                size="sm"
                onClick={handleSubmit}
                disabled={selectedScore === null || submitting}
              >
                {submitting ? "送信中..." : "送信する"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* トースト */}
      {toast && (
        <div className="fixed bottom-6 right-6 z-50 rounded-lg bg-foreground px-4 py-3 text-sm text-background shadow-lg animate-in slide-in-from-bottom-2 fade-in duration-300">
          ありがとうございました！
        </div>
      )}
    </>
  );
}
