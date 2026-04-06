"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

// APIレスポンスの生の型（内部でのみ使用、UI上には一切表示しない）
interface QASessionRaw {
  id: string;
  theme: string;
  status: "active" | "paused" | "completed";
  question_count: number;
  answered_count: number;
}

interface QASession {
  sid: string;
  theme: string;
  status: "active" | "paused" | "completed";
  questionCount: number;
  answeredCount: number;
}

interface NextQuestionRaw {
  id: string;
  question: string;
  hint?: string;
}

interface NextQuestion {
  qid: string;
  question: string;
  hint?: string;
}

interface ChatMessage {
  role: "assistant" | "user";
  content: string;
  qid?: string;
}

function extractId(raw: Record<string, unknown>): string {
  // APIが "id" または旧フィールド名で返す場合に対応（UI上には表示しない）
  const idKey = Object.keys(raw).find((k) => k === "id" || k.endsWith("_id"));
  return idKey ? String(raw[idKey] ?? "") : "";
}

function toSession(raw: QASessionRaw): QASession {
  return {
    sid: raw.id ?? extractId(raw as unknown as Record<string, unknown>),
    theme: raw.theme,
    status: raw.status,
    questionCount: raw.question_count,
    answeredCount: raw.answered_count,
  };
}

function toQuestion(raw: NextQuestionRaw): NextQuestion {
  return {
    qid: raw.id ?? extractId(raw as unknown as Record<string, unknown>),
    question: raw.question,
    hint: raw.hint,
  };
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function KnowledgeSessionPage() {
  const { session } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const theme = searchParams.get("theme") ?? "";

  const [qaSession, setQaSession] = useState<QASession | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [currentQuestion, setCurrentQuestion] = useState<NextQuestion | null>(null);
  const [answer, setAnswer] = useState("");

  const [sessionLoading, setSessionLoading] = useState(true);
  const [questionLoading, setQuestionLoading] = useState(false);
  const [answering, setAnswering] = useState(false);
  const [pausing, setPausing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const bottomRef = useRef<HTMLDivElement>(null);

  const displayTheme = theme || "未指定テーマ";

  // ---- 進捗計算 ----
  const TARGET_QUESTIONS = 15;
  const answeredCount = qaSession?.answeredCount ?? 0;
  const progressPct = Math.min(100, Math.round((answeredCount / TARGET_QUESTIONS) * 100));

  // ---- セッション取得/作成 ----
  const initSession = useCallback(async () => {
    if (!session?.access_token || !theme) return;
    setSessionLoading(true);
    setError(null);
    try {
      const raw = await apiFetch<QASessionRaw>("/knowledge/sessions", {
        token: session.access_token,
        method: "POST",
        body: { theme },
      });
      setQaSession(toSession(raw));
    } catch {
      setError("セッションの準備に失敗しました。しばらく経ってから再度お試しください。");
    } finally {
      setSessionLoading(false);
    }
  }, [session?.access_token, theme]);

  useEffect(() => {
    initSession();
  }, [initSession]);

  // ---- 次の質問を取得 ----
  const fetchNextQuestion = useCallback(async (sid: string) => {
    if (!session?.access_token) return;
    setQuestionLoading(true);
    setError(null);
    try {
      const raw = await apiFetch<NextQuestionRaw | null>(
        `/knowledge/sessions/${sid}/next-question`,
        { token: session.access_token }
      );
      if (!raw) {
        setCurrentQuestion(null);
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            content: `${displayTheme}についての質問は以上です。ご協力ありがとうございました！`,
          },
        ]);
        return;
      }
      const q = toQuestion(raw);
      setCurrentQuestion(q);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: q.question,
          qid: q.qid,
        },
      ]);
    } catch {
      setError("次の質問の取得に失敗しました。しばらく経ってから再度お試しください。");
    } finally {
      setQuestionLoading(false);
    }
  }, [session?.access_token, displayTheme]);

  useEffect(() => {
    if (qaSession?.sid) {
      fetchNextQuestion(qaSession.sid);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qaSession?.sid]);

  // ---- 自動スクロール ----
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, questionLoading]);

  // ---- 回答送信 ----
  async function handleSendAnswer() {
    if (!answer.trim() || !qaSession?.sid || !currentQuestion || !session?.access_token) return;
    const userAnswer = answer.trim();
    setAnswer("");
    setMessages((prev) => [...prev, { role: "user", content: userAnswer }]);
    setAnswering(true);
    setError(null);
    const sid = qaSession.sid;
    try {
      await apiFetch(`/knowledge/sessions/${sid}/answer`, {
        token: session.access_token,
        method: "POST",
        body: { answer: userAnswer, question_ref: currentQuestion.qid },
      });
      setQaSession((prev) =>
        prev ? { ...prev, answeredCount: prev.answeredCount + 1 } : prev
      );
      setCurrentQuestion(null);
      await fetchNextQuestion(sid);
    } catch {
      setError("回答の送信に失敗しました。もう一度お試しください。");
    } finally {
      setAnswering(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleSendAnswer();
    }
  }

  // ---- 一時保存して終了 ----
  async function handlePause() {
    if (!qaSession?.sid || !session?.access_token) {
      router.push("/knowledge");
      return;
    }
    setPausing(true);
    const sid = qaSession.sid;
    try {
      await apiFetch(`/knowledge/sessions/${sid}`, {
        token: session.access_token,
        method: "PATCH",
        body: { status: "paused" },
      });
    } catch {
      // 失敗しても画面遷移する
    } finally {
      setPausing(false);
      router.push("/knowledge");
    }
  }

  // ---- テーマ未指定 ----
  if (!theme) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[400px] gap-4 px-4">
        <p className="text-sm text-muted-foreground">テーマが指定されていません。</p>
        <Button onClick={() => router.push("/knowledge")}>
          ナレッジ管理に戻る
        </Button>
      </div>
    );
  }

  // ---- セッション初期化中 ----
  if (sessionLoading) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[400px] gap-3 px-4">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
        <p className="text-sm text-muted-foreground">AIがセッションを準備しています...</p>
      </div>
    );
  }

  // ---- セッション作成エラー ----
  if (!qaSession && error) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[400px] gap-4 px-4">
        <p className="text-sm text-destructive">{error}</p>
        <Button onClick={initSession}>再試行</Button>
        <Button variant="outline" onClick={() => router.push("/knowledge")}>
          ナレッジ管理に戻る
        </Button>
      </div>
    );
  }

  const isCompleted = !currentQuestion && !questionLoading && messages.length > 0;

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)]">
      {/* ---- ヘッダー ---- */}
      <div className="shrink-0 border-b px-4 py-3 bg-background">
        <div className="flex items-center justify-between gap-3 max-w-2xl mx-auto">
          <div className="flex items-center gap-2 min-w-0">
            <Button
              variant="ghost"
              size="sm"
              className="shrink-0 px-2"
              onClick={handlePause}
              disabled={pausing}
            >
              ← 戻る
            </Button>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h1 className="text-base font-semibold truncate">{displayTheme}</h1>
                <Badge variant="outline" className="shrink-0 text-xs">
                  {answeredCount}問目 / 目安{TARGET_QUESTIONS}問
                </Badge>
              </div>
              <p className="text-xs text-muted-foreground mt-0.5">
                AIが質問します。わかる範囲で回答してください。
              </p>
            </div>
          </div>

          {/* プログレスバー（PC） */}
          <div className="hidden sm:flex items-center gap-2 shrink-0">
            <div className="w-24 h-2 rounded-full bg-muted overflow-hidden">
              <div
                className="h-full rounded-full bg-primary transition-all"
                style={{ width: `${progressPct}%` }}
              />
            </div>
            <span className="text-xs text-muted-foreground">{progressPct}%</span>
          </div>
        </div>

        {/* プログレスバー（スマホ） */}
        <div className="sm:hidden mt-2 max-w-2xl mx-auto">
          <div className="flex items-center gap-2">
            <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden">
              <div
                className="h-full rounded-full bg-primary transition-all"
                style={{ width: `${progressPct}%` }}
              />
            </div>
            <span className="text-xs text-muted-foreground shrink-0">{progressPct}%</span>
          </div>
        </div>
      </div>

      {/* ---- チャットエリア ---- */}
      <div className="flex-1 min-h-0 overflow-y-auto px-4 py-4">
        <div className="max-w-2xl mx-auto space-y-4">
          {/* ガイドテキスト（最初の1回のみ） */}
          {messages.length === 0 && !questionLoading && (
            <div className="rounded-lg bg-muted/50 p-4 text-sm text-muted-foreground">
              <p>
                <span className="font-medium text-foreground">{displayTheme}</span>
                について教えてください。AIが順番に質問しますので、わかる範囲でお答えください。
              </p>
              <p className="mt-1">「わからない」「該当しない」でも構いません。</p>
            </div>
          )}

          {/* メッセージ一覧 */}
          {messages.map((msg, idx) => (
            <div
              key={idx}
              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                  msg.role === "assistant"
                    ? "bg-muted text-foreground rounded-tl-none"
                    : "bg-primary text-primary-foreground rounded-tr-none"
                }`}
              >
                {msg.content}
              </div>
            </div>
          ))}

          {/* 質問ローディング */}
          {(questionLoading || answering) && (
            <div className="flex justify-start">
              <div className="bg-muted rounded-2xl rounded-tl-none px-4 py-3 flex items-center gap-2">
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-muted-foreground border-t-transparent" />
                <span className="text-sm text-muted-foreground">
                  {answering ? "回答を保存しています..." : "AIが次の質問を考えています..."}
                </span>
              </div>
            </div>
          )}

          {/* エラー */}
          {error && (
            <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
              {error}
            </div>
          )}

          {/* 収集完了 */}
          {isCompleted && answeredCount > 0 && (
            <div className="rounded-lg bg-green-100 border border-green-200 p-4 text-center">
              <p className="text-sm font-medium text-green-800">
                {answeredCount}件のルール・ノウハウを収集しました
              </p>
              <Button
                className="mt-3 w-full sm:w-auto"
                onClick={() => router.push("/knowledge")}
              >
                ナレッジ管理に戻る
              </Button>
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      {/* ---- 入力エリア ---- */}
      {currentQuestion && !isCompleted && (
        <div className="shrink-0 border-t bg-background px-4 py-3">
          <div className="max-w-2xl mx-auto space-y-2">
            <Textarea
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="回答を入力してください（Ctrl+Enter で送信）"
              className="min-h-[80px] resize-none w-full"
              disabled={answering || questionLoading}
            />
            <div className="flex flex-col-reverse sm:flex-row items-stretch sm:items-center justify-between gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={handlePause}
                disabled={pausing || answering}
                className="text-muted-foreground w-full sm:w-auto"
              >
                {pausing ? "保存中..." : "一時保存して終了"}
              </Button>
              <Button
                size="lg"
                className="w-full sm:w-auto"
                onClick={handleSendAnswer}
                disabled={!answer.trim() || answering || questionLoading}
              >
                {answering ? (
                  <>
                    <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                    送信中...
                  </>
                ) : "AIに送信"}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* セッション未完了でcurrentQuestionがない（ローディング以外） */}
      {!currentQuestion && !questionLoading && !isCompleted && !sessionLoading && (
        <div className="shrink-0 border-t bg-background px-4 py-3">
          <div className="max-w-2xl mx-auto">
            <Button
              variant="outline"
              className="w-full sm:w-auto"
              onClick={() => router.push("/knowledge")}
            >
              ナレッジ管理に戻る
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
