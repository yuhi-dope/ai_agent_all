"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { usePathname } from "next/navigation";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";

interface ChatMessage {
  role: "user" | "ai";
  content: string;
}

interface QAResponse {
  answer: string;
}

function PAGE_CONTEXT_LABEL(pathname: string): string {
  if (pathname.startsWith("/dashboard")) return "ホーム";
  if (pathname.startsWith("/knowledge/qa")) return "AIへの質問";
  if (pathname.startsWith("/knowledge/input")) return "ルール入力";
  if (pathname.startsWith("/knowledge")) return "ルール一覧";
  if (pathname.startsWith("/bpo/approvals")) return "承認フロー";
  if (pathname.startsWith("/bpo")) return "業務自動化";
  if (pathname.startsWith("/twin")) return "会社の状態";
  if (pathname.startsWith("/proposals")) return "AI提案";
  if (pathname.startsWith("/sales/leads")) return "リード一覧";
  if (pathname.startsWith("/sales/pipeline")) return "商談パイプライン";
  if (pathname.startsWith("/sales/proposals")) return "提案書一覧";
  if (pathname.startsWith("/sales/contracts")) return "見積・契約";
  if (pathname.startsWith("/sales/forecast")) return "売上予測";
  if (pathname.startsWith("/marketing/outreach")) return "アウトリーチ";
  if (pathname.startsWith("/marketing")) return "マーケティング";
  if (pathname.startsWith("/crm/customers")) return "顧客一覧";
  if (pathname.startsWith("/crm/revenue")) return "売上集計";
  if (pathname.startsWith("/crm/requests")) return "要望一覧";
  if (pathname.startsWith("/crm")) return "顧客対応";
  if (pathname.startsWith("/support")) return "サポート";
  if (pathname.startsWith("/upsell")) return "アップセル";
  if (pathname.startsWith("/learning")) return "学習・改善";
  if (pathname.startsWith("/settings/members")) return "メンバー設定";
  if (pathname.startsWith("/settings/connectors")) return "外部ツール連携";
  return "この画面";
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm leading-relaxed ${
          isUser
            ? "bg-blue-600 text-white rounded-br-sm"
            : "bg-muted text-foreground rounded-bl-sm"
        }`}
      >
        {message.content}
      </div>
    </div>
  );
}

export function ContextHelp() {
  const { session } = useAuth();
  const pathname = usePathname();
  const [isOpen, setIsOpen] = useState(false);
  const [inputText, setInputText] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      role: "ai",
      content:
        "こんにちは！何かお困りですか？\n下のボタンを押すか、質問を入力してください。",
    },
  ]);
  const [sending, setSending] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (isOpen) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, isOpen]);

  useEffect(() => {
    if (isOpen) {
      inputRef.current?.focus();
    }
  }, [isOpen]);

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || sending) return;
      setInputText("");
      setMessages((prev) => [...prev, { role: "user", content: trimmed }]);
      setSending(true);
      try {
        const res = await apiFetch<QAResponse>("/knowledge/qa", {
          method: "POST",
          token: session?.access_token,
          body: { question: trimmed },
        });
        setMessages((prev) => [
          ...prev,
          { role: "ai", content: res.answer },
        ]);
      } catch {
        setMessages((prev) => [
          ...prev,
          {
            role: "ai",
            content:
              "うまく回答できませんでした。もう一度お試しいただくか、しばらく経ってからお試しください。",
          },
        ]);
      } finally {
        setSending(false);
      }
    },
    [sending, session?.access_token]
  );

  const handleQuickQuestion = useCallback(() => {
    const label = PAGE_CONTEXT_LABEL(pathname);
    sendMessage(`${label}の使い方を教えてください`);
  }, [pathname, sendMessage]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage(inputText);
      }
    },
    [inputText, sendMessage]
  );

  return (
    <>
      {/* チャットパネル */}
      {isOpen && (
        <div className="fixed bottom-24 right-6 z-50 w-80 h-96 flex flex-col rounded-2xl bg-background border border-border shadow-2xl overflow-hidden">
          {/* ヘッダー */}
          <div className="flex items-center justify-between px-4 py-3 border-b bg-blue-600 text-white">
            <div className="flex items-center gap-2">
              <span className="text-base">💬</span>
              <span className="font-medium text-sm">何かお困りですか？</span>
            </div>
            <button
              type="button"
              onClick={() => setIsOpen(false)}
              className="h-6 w-6 flex items-center justify-center rounded-full hover:bg-white/20 transition-colors"
              aria-label="閉じる"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M18 6 6 18" />
                <path d="m6 6 12 12" />
              </svg>
            </button>
          </div>

          {/* チャット履歴 */}
          <div className="flex-1 overflow-y-auto p-3 space-y-3">
            {messages.map((msg, i) => (
              <MessageBubble key={i} message={msg} />
            ))}
            {sending && (
              <div className="flex justify-start">
                <div className="bg-muted rounded-2xl rounded-bl-sm px-4 py-2">
                  <div className="flex items-center gap-1.5">
                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground [animation-delay:0ms]" />
                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground [animation-delay:150ms]" />
                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground [animation-delay:300ms]" />
                  </div>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* クイックボタン */}
          {messages.length <= 2 && !sending && (
            <div className="px-3 pb-2">
              <button
                type="button"
                onClick={handleQuickQuestion}
                className="w-full text-left text-xs px-3 py-2 rounded-lg border border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100 transition-colors leading-snug"
              >
                💡 {PAGE_CONTEXT_LABEL(pathname)}の使い方を教えて
              </button>
            </div>
          )}

          {/* 入力欄 */}
          <div className="border-t p-2 flex gap-2 items-end">
            <textarea
              ref={inputRef}
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="質問を入力..."
              rows={1}
              className="flex-1 resize-none rounded-lg border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring placeholder:text-muted-foreground max-h-20 overflow-y-auto"
              style={{ minHeight: "36px" }}
              disabled={sending}
            />
            <button
              type="button"
              onClick={() => sendMessage(inputText)}
              disabled={!inputText.trim() || sending}
              className="shrink-0 h-9 w-9 flex items-center justify-center rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40 disabled:pointer-events-none transition-colors"
              aria-label="AIに質問する"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="m22 2-7 20-4-9-9-4Z" />
                <path d="M22 2 11 13" />
              </svg>
            </button>
          </div>
        </div>
      )}

      {/* 固定ヘルプボタン */}
      <button
        type="button"
        onClick={() => setIsOpen((prev) => !prev)}
        className="fixed bottom-6 right-6 z-40 flex h-14 w-14 flex-col items-center justify-center rounded-full bg-blue-600 text-white shadow-lg hover:bg-blue-700 active:scale-95 transition-all"
        aria-label="困ったときのヘルプを開く"
      >
        {isOpen ? (
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M18 6 6 18" />
            <path d="m6 6 12 12" />
          </svg>
        ) : (
          <>
            <span className="text-lg leading-none">💬</span>
            <span className="text-[10px] font-medium mt-0.5 leading-none">困った</span>
          </>
        )}
      </button>
    </>
  );
}
