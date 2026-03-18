"use client";

import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Source {
  knowledge_id: string;
  title: string;
  relevance: number;
  excerpt: string;
}

interface AskResponse {
  answer: string;
  confidence: number;
  sources: Source[];
  missing_info?: string;
  model_used: string;
  cost_yen: number;
  session_id?: string;
}

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  confidence?: number;
  sources?: Source[];
  missing_info?: string;
  model_used?: string;
  cost_yen?: number;
  session_id?: string;
  rating?: 1 | -1;
}

// ---------------------------------------------------------------------------
// Markdown → HTML（軽量パーサー）
// ---------------------------------------------------------------------------

/**
 * 軽量Markdownパーサー
 *
 * 対応パターン:
 * - **太字:** 説明  → 定義リスト風カード（dt/dd）
 * - 1. 2. 3.        → 番号付きリスト（番号バッジ）
 * - * - ・           → 箇条書き（ドット）
 * - 番号リスト + サブ箇条書き
 * - 通常テキスト → 段落
 */
function renderMarkdown(text: string): string {
  const lines = text.split("\n");
  const result: string[] = [];
  let i = 0;

  while (i < lines.length) {
    const trimmed = lines[i].trim();

    // 空行スキップ
    if (!trimmed) { i++; continue; }

    // --- 番号付きリスト ---
    if (/^\d+\.\s/.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length) {
        const cur = lines[i]?.trim() ?? "";
        if (/^\d+\.\s/.test(cur)) {
          let body = cur.replace(/^\d+\.\s*/, "").trim();
          // サブアイテム収集
          const subs: string[] = [];
          while (i + 1 < lines.length) {
            const next = lines[i + 1]?.trim() ?? "";
            if (/^[\*\-・]\s/.test(next)) {
              subs.push(next.replace(/^[\*\-・]\s*/, "").trim());
              i++;
            } else { break; }
          }
          let html = renderDefinitionOrInline(body);
          if (subs.length > 0) {
            html += `<ul class="qa-sub">${subs.map((s) => `<li>${inlineMarkdown(s)}</li>`).join("")}</ul>`;
          }
          items.push(`<li>${html}</li>`);
          i++;
        } else if (/^[\*\-・]\s/.test(cur)) {
          // 番号なしサブアイテム → 直前のliに追加
          if (items.length > 0) {
            const sub = `<li>${inlineMarkdown(cur.replace(/^[\*\-・]\s*/, "").trim())}</li>`;
            const last = items[items.length - 1];
            if (last.includes("</ul></li>")) {
              items[items.length - 1] = last.replace("</ul></li>", `${sub}</ul></li>`);
            } else {
              items[items.length - 1] = last.replace("</li>", `<ul class="qa-sub">${sub}</ul></li>`);
            }
          }
          i++;
        } else if (!cur) { i++; break; }
        else { break; }
      }
      result.push(`<ol>${items.join("")}</ol>`);
      continue;
    }

    // --- 箇条書きリスト ---
    if (/^[\*\-・]\s/.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length) {
        const cur = lines[i]?.trim() ?? "";
        if (/^[\*\-・]\s/.test(cur)) {
          items.push(`<li>${inlineMarkdown(cur.replace(/^[\*\-・]\s*/, "").trim())}</li>`);
          i++;
        } else if (!cur) { i++; break; }
        else { break; }
      }
      result.push(`<ul>${items.join("")}</ul>`);
      continue;
    }

    // --- 太字見出し行（**項目名:** 内容）を連続で検知 → 定義リスト ---
    if (/^\*\*[^*]+\*\*[:：]/.test(trimmed)) {
      const dlItems: string[] = [];
      while (i < lines.length) {
        const cur = lines[i]?.trim() ?? "";
        if (!cur) { i++; break; }
        // 太字見出し行
        const m = cur.match(/^\*\*([^*]+)\*\*[:：]\s*(.*)/);
        if (m) {
          // 次の行がサブアイテムかチェック
          const subs: string[] = [];
          while (i + 1 < lines.length) {
            const next = lines[i + 1]?.trim() ?? "";
            // サブアイテム: インデントなし * - ・ or 太字なし普通行（空行で終了）
            if (/^[\*\-・]\s/.test(next)) {
              subs.push(next.replace(/^[\*\-・]\s*/, "").trim());
              i++;
            } else if (next && !/^\*\*/.test(next) && !/^\d+\.\s/.test(next)) {
              // 太字見出しでも番号でもない続き行 → 本文の続き
              subs.push(next);
              i++;
            } else { break; }
          }
          let dd = inlineMarkdown(m[2]);
          if (subs.length > 0) {
            dd += `<ul class="qa-sub">${subs.map((s) => `<li>${inlineMarkdown(s)}</li>`).join("")}</ul>`;
          }
          dlItems.push(`<div class="qa-dl-item"><dt>${inlineMarkdown(m[1])}</dt><dd>${dd}</dd></div>`);
          i++;
        } else {
          break;
        }
      }
      if (dlItems.length > 0) {
        result.push(`<dl class="qa-dl">${dlItems.join("")}</dl>`);
      }
      continue;
    }

    // --- 通常テキスト段落 ---
    const pLines: string[] = [];
    while (i < lines.length) {
      const cur = lines[i]?.trim() ?? "";
      if (!cur || /^\d+\.\s/.test(cur) || /^[\*\-・]\s/.test(cur)) break;
      pLines.push(inlineMarkdown(cur));
      i++;
    }
    result.push(`<p>${pLines.join("<br/>")}</p>`);
  }

  return result.join("");
}

/** **太字:** が含まれる行を <strong>: 内容 に変換。なければ通常のinline処理 */
function renderDefinitionOrInline(text: string): string {
  const m = text.match(/^\*\*([^*]+)\*\*[:：]\s*(.*)/);
  if (m) {
    return `<strong>${m[1]}</strong>: ${inlineMarkdown(m[2])}`;
  }
  return inlineMarkdown(text);
}

/** インライン: **太字** → <strong>, `code` → <code> */
function inlineMarkdown(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`(.+?)`/g, '<code>$1</code>');
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function KnowledgeQAPage() {
  const { session } = useAuth();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedSources, setExpandedSources] = useState<Set<string>>(new Set());

  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Auto scroll to bottom
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  // Auto focus input
  useEffect(() => {
    inputRef.current?.focus();
  }, [loading]);

  // Submit
  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const q = input.trim();
    if (!q || loading) return;

    const userMsg: Message = { id: crypto.randomUUID(), role: "user", content: q };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);
    setError(null);

    try {
      const res = await apiFetch<AskResponse>("/knowledge/ask", {
        method: "POST",
        token: session?.access_token,
        body: { question: q },
      });

      const assistantMsg: Message = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: res.answer,
        confidence: res.confidence,
        sources: res.sources,
        missing_info: res.missing_info,
        model_used: res.model_used,
        cost_yen: res.cost_yen,
        session_id: res.session_id,
      };
      setMessages((prev) => [...prev, assistantMsg]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "回答の生成に失敗しました");
    } finally {
      setLoading(false);
    }
  }

  // Enter to send, Shift+Enter for newline
  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  }

  // Toggle source expansion
  function toggleSources(msgId: string) {
    setExpandedSources((prev) => {
      const next = new Set(prev);
      next.has(msgId) ? next.delete(msgId) : next.add(msgId);
      return next;
    });
  }

  async function handleRate(msgId: string, sessionId: string, rating: 1 | -1) {
    const token = session?.access_token;
    if (!token) return;
    try {
      await apiFetch(`/knowledge/qa/${sessionId}/rate`, {
        method: "PATCH",
        token,
        body: { user_rating: rating },
      });
      setMessages((prev) =>
        prev.map((m) => (m.id === msgId ? { ...m, rating } : m))
      );
    } catch {
      // silent fail
    }
  }

  function confidenceBadge(c: number) {
    if (c >= 0.8) return { variant: "default" as const, label: "高信頼" };
    if (c >= 0.5) return { variant: "secondary" as const, label: "中信頼" };
    return { variant: "destructive" as const, label: "低信頼" };
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  return (
    <div className="flex h-[calc(100vh-4rem)] flex-col">
      {/* ---- Messages area ---- */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        {messages.length === 0 && !loading ? (
          /* Empty state */
          <div className="flex h-full flex-col items-center justify-center gap-5 text-muted-foreground px-4">
            <div className="space-y-2 text-center">
              <h1 className="text-xl font-bold text-foreground">ナレッジQ&A</h1>
              <p className="text-sm">登録済みの社内ナレッジをもとに回答します</p>
            </div>
            <div className="grid grid-cols-2 gap-2 max-w-lg w-full">
              {[
                "経費精算のルールを教えて",
                "新規取引先の与信審査フローは？",
                "残業の上限は何時間？",
                "有給の申請方法は？",
              ].map((q) => (
                <button
                  key={q}
                  type="button"
                  onClick={() => { setInput(q); inputRef.current?.focus(); }}
                  className="rounded-lg border bg-card px-4 py-3 text-left text-sm transition-all hover:bg-accent hover:shadow-sm"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        ) : (
          /* Messages */
          <div className="mx-auto max-w-2xl px-4 py-6 space-y-5">
            {messages.map((msg) => (
              <div key={msg.id}>
                {msg.role === "user" ? (
                  /* User message */
                  <div className="flex justify-end">
                    <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-primary px-4 py-2.5 text-sm text-primary-foreground whitespace-pre-wrap">
                      {msg.content}
                    </div>
                  </div>
                ) : (
                  /* Assistant message */
                  <div className="space-y-3">
                    {/* 回答本文 */}
                    <div className="rounded-xl border bg-card px-5 py-4 shadow-sm">
                      <div
                        className="qa-answer text-sm leading-7"
                        dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }}
                      />
                    </div>

                    {/* Missing info warning */}
                    {msg.missing_info && (
                      <div className="flex items-start gap-2 rounded-lg bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 px-3 py-2.5">
                        <span className="shrink-0 text-sm mt-0.5">!</span>
                        <p className="text-xs text-amber-800 dark:text-amber-300">
                          <span className="font-semibold">補足: </span>
                          {msg.missing_info}
                        </p>
                      </div>
                    )}

                    {/* Meta bar */}
                    <div className="flex items-center gap-2 px-1 flex-wrap">
                      {msg.confidence != null && (() => {
                        const b = confidenceBadge(msg.confidence);
                        return (
                          <Badge variant={b.variant} className="text-[10px] gap-1">
                            {b.label} {Math.round(msg.confidence * 100)}%
                          </Badge>
                        );
                      })()}

                      {msg.sources && msg.sources.length > 0 && (
                        <button
                          type="button"
                          onClick={() => toggleSources(msg.id)}
                          className="inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[11px] text-muted-foreground hover:bg-muted transition-colors"
                        >
                          <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                          </svg>
                          ソース {msg.sources.length}件
                          <span className="text-[10px]">{expandedSources.has(msg.id) ? "▾" : "▸"}</span>
                        </button>
                      )}

                      {/* 👍👎 フィードバックボタン */}
                      {msg.session_id && (
                        <span className="inline-flex items-center gap-1 ml-1">
                          <button
                            type="button"
                            onClick={() => handleRate(msg.id, msg.session_id!, 1)}
                            disabled={msg.rating != null}
                            className={`rounded p-1 text-xs transition-colors ${
                              msg.rating === 1
                                ? "bg-green-100 text-green-700"
                                : "text-muted-foreground hover:bg-muted"
                            }`}
                            title="良い回答"
                          >
                            👍
                          </button>
                          <button
                            type="button"
                            onClick={() => handleRate(msg.id, msg.session_id!, -1)}
                            disabled={msg.rating != null}
                            className={`rounded p-1 text-xs transition-colors ${
                              msg.rating === -1
                                ? "bg-red-100 text-red-700"
                                : "text-muted-foreground hover:bg-muted"
                            }`}
                            title="悪い回答"
                          >
                            👎
                          </button>
                          {msg.rating != null && (
                            <span className="text-[10px] text-muted-foreground">評価済み</span>
                          )}
                        </span>
                      )}

                      {msg.model_used && (
                        <span className="text-[10px] text-muted-foreground/60 ml-auto">
                          {msg.model_used}
                        </span>
                      )}
                    </div>

                    {/* Expanded sources */}
                    {msg.sources && expandedSources.has(msg.id) && (
                      <div className="rounded-lg border bg-muted/30 px-4 py-3 space-y-2">
                        <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">参照ナレッジ</p>
                        {msg.sources.map((src) => (
                          <div key={src.knowledge_id} className="flex items-center justify-between gap-2 text-xs">
                            <span className="truncate text-foreground">{src.title}</span>
                            <span className="shrink-0 text-muted-foreground">
                              関連度 {Math.round(src.relevance * 100)}%
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}

            {/* Loading indicator */}
            {loading && (
              <div className="rounded-xl border bg-card px-5 py-4 shadow-sm">
                <div className="flex items-center gap-3 text-sm text-muted-foreground">
                  <div className="flex gap-1">
                    <div className="h-1.5 w-1.5 rounded-full bg-primary/60 animate-bounce [animation-delay:0ms]" />
                    <div className="h-1.5 w-1.5 rounded-full bg-primary/60 animate-bounce [animation-delay:150ms]" />
                    <div className="h-1.5 w-1.5 rounded-full bg-primary/60 animate-bounce [animation-delay:300ms]" />
                  </div>
                  <span>回答を生成中...</span>
                </div>
              </div>
            )}

            {/* Error */}
            {error && (
              <div className="rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                {error}
              </div>
            )}
          </div>
        )}
      </div>

      {/* ---- Input area (bottom fixed) ---- */}
      <div className="shrink-0 border-t bg-background/80 backdrop-blur-sm">
        <form onSubmit={handleSubmit} className="mx-auto max-w-2xl px-4 py-3">
          <div className="flex items-end gap-2 rounded-xl border bg-card px-3 py-2 shadow-sm focus-within:ring-2 focus-within:ring-ring/50 focus-within:border-ring">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="質問を入力... (Enter で送信、Shift+Enter で改行)"
              rows={1}
              className="flex-1 resize-none bg-transparent text-sm outline-none placeholder:text-muted-foreground max-h-32"
              style={{ height: "auto", minHeight: "24px" }}
              onInput={(e) => {
                const el = e.currentTarget;
                el.style.height = "auto";
                el.style.height = Math.min(el.scrollHeight, 128) + "px";
              }}
              disabled={loading}
            />
            <Button
              type="submit"
              size="sm"
              disabled={loading || !input.trim()}
              className="shrink-0 rounded-lg h-8 px-3"
            >
              {loading ? (
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary-foreground border-t-transparent" />
              ) : (
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" />
                </svg>
              )}
            </Button>
          </div>
          <p className="mt-1.5 text-center text-[10px] text-muted-foreground">
            社内ナレッジに基づいて回答します。回答の正確性は信頼度を確認してください。
          </p>
        </form>
      </div>

      {/* Scoped styles for rendered answer content */}
      <style jsx global>{`
        /* ---- 基本 ---- */
        .qa-answer p {
          margin-bottom: 0.5rem;
          line-height: 1.7;
        }
        .qa-answer p:last-child { margin-bottom: 0; }
        .qa-answer strong { font-weight: 600; }
        .qa-answer code {
          background: hsl(var(--muted));
          padding: 0.1rem 0.35rem;
          border-radius: 0.25rem;
          font-size: 0.8125rem;
        }

        /* ---- 定義リスト (太字見出し: 内容) ---- */
        .qa-answer .qa-dl {
          display: flex;
          flex-direction: column;
          gap: 0.125rem;
          margin-bottom: 0.5rem;
        }
        .qa-answer .qa-dl-item {
          display: grid;
          grid-template-columns: 1fr;
          padding: 0.625rem 0.75rem;
          border-radius: 0.5rem;
          background: hsl(var(--muted) / 0.35);
        }
        .qa-answer .qa-dl-item dt {
          font-weight: 600;
          font-size: 0.8125rem;
          margin-bottom: 0.125rem;
        }
        .qa-answer .qa-dl-item dd {
          margin: 0;
          font-size: 0.8125rem;
          line-height: 1.6;
          color: hsl(var(--muted-foreground));
        }

        /* ---- 番号付きリスト ---- */
        .qa-answer > ol {
          list-style: none;
          counter-reset: qa-counter;
          padding-left: 0;
          margin-bottom: 0.5rem;
        }
        .qa-answer > ol > li {
          counter-increment: qa-counter;
          position: relative;
          padding-left: 2rem;
          padding-top: 0.5rem;
          padding-bottom: 0.5rem;
          border-bottom: 1px solid hsl(var(--border) / 0.3);
        }
        .qa-answer > ol > li:last-child {
          border-bottom: none;
          padding-bottom: 0;
        }
        .qa-answer > ol > li::before {
          content: counter(qa-counter);
          position: absolute;
          left: 0;
          top: 0.55rem;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 1.25rem;
          height: 1.25rem;
          border-radius: 9999px;
          background: hsl(var(--primary));
          color: hsl(var(--primary-foreground));
          font-size: 0.625rem;
          font-weight: 700;
        }

        /* ---- トップレベル箇条書き ---- */
        .qa-answer > ul {
          list-style: none;
          padding-left: 0;
          margin-bottom: 0.5rem;
        }
        .qa-answer > ul > li {
          position: relative;
          padding-left: 1rem;
          padding-top: 0.2rem;
          padding-bottom: 0.2rem;
        }
        .qa-answer > ul > li::before {
          content: "";
          position: absolute;
          left: 0;
          top: 0.6rem;
          width: 5px;
          height: 5px;
          border-radius: 9999px;
          background: hsl(var(--primary) / 0.5);
        }

        /* ---- サブリスト (.qa-sub) ---- */
        .qa-answer .qa-sub {
          list-style: none;
          padding-left: 0;
          margin-top: 0.25rem;
          margin-bottom: 0;
        }
        .qa-answer .qa-sub > li {
          position: relative;
          padding-left: 0.875rem;
          padding-top: 0.1rem;
          padding-bottom: 0.1rem;
          font-size: 0.8125rem;
          color: hsl(var(--muted-foreground));
        }
        .qa-answer .qa-sub > li::before {
          content: "";
          position: absolute;
          left: 0;
          top: 0.5rem;
          width: 4px;
          height: 4px;
          border-radius: 9999px;
          background: hsl(var(--muted-foreground) / 0.35);
        }
      `}</style>
    </div>
  );
}
