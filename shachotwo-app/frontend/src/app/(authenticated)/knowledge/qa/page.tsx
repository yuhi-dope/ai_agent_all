"use client";

import { useState } from "react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";

const DEPARTMENTS = [
  "営業",
  "経理",
  "総務",
  "製造",
  "品質管理",
  "人事",
  "情報システム",
  "経営企画",
] as const;

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
}

interface QAEntry {
  question: string;
  department?: string;
  response: AskResponse;
}

export default function KnowledgeQAPage() {
  const { session } = useAuth();
  const [question, setQuestion] = useState("");
  const [department, setDepartment] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<QAEntry[]>([]);

  async function handleAsk(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim()) return;

    setLoading(true);
    setError(null);

    const currentQuestion = question.trim();
    const currentDepartment = department || undefined;

    try {
      const res = await apiFetch<AskResponse>("/knowledge/ask", {
        method: "POST",
        token: session?.access_token,
        body: {
          question: currentQuestion,
          ...(currentDepartment && { department: currentDepartment }),
        },
      });
      setHistory((prev) => [
        ...prev,
        {
          question: currentQuestion,
          department: currentDepartment,
          response: res,
        },
      ]);
      setQuestion("");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "回答の生成に失敗しました"
      );
    } finally {
      setLoading(false);
    }
  }

  function confidenceColor(confidence: number): "default" | "secondary" | "destructive" | "outline" {
    if (confidence >= 0.8) return "default";
    if (confidence >= 0.5) return "secondary";
    return "destructive";
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-bold">ナレッジQ&A</h1>
        <p className="text-muted-foreground">
          社内ナレッジに基づいてAIが質問に回答します。
        </p>
      </div>

      {/* History */}
      {history.length > 0 && (
        <div className="space-y-4">
          {history.map((entry, index) => (
            <div key={index} className="space-y-3">
              {/* Question */}
              <div className="flex justify-end">
                <div className="max-w-[80%] rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground">
                  {entry.question}
                  {entry.department && (
                    <span className="ml-2 text-xs opacity-70">
                      ({entry.department})
                    </span>
                  )}
                </div>
              </div>

              {/* Answer */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    回答
                    <Badge variant={confidenceColor(entry.response.confidence)}>
                      信頼度: {Math.round(entry.response.confidence * 100)}%
                    </Badge>
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <p className="text-sm whitespace-pre-wrap">
                    {entry.response.answer}
                  </p>

                  {entry.response.sources.length > 0 && (
                    <div className="space-y-2">
                      <h4 className="text-xs font-medium text-muted-foreground">
                        参照ソース
                      </h4>
                      <div className="space-y-1">
                        {entry.response.sources.map((source) => (
                          <div
                            key={source.knowledge_id}
                            className="flex items-center justify-between rounded border px-3 py-1.5 text-xs"
                          >
                            <span className="font-medium">{source.title}</span>
                            <Badge variant="outline">
                              関連度: {Math.round(source.relevance * 100)}%
                            </Badge>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {entry.response.missing_info && (
                    <div className="rounded-lg border border-yellow-500/30 bg-yellow-500/10 p-3 text-xs text-yellow-700 dark:text-yellow-400">
                      <span className="font-medium">不足情報: </span>
                      {entry.response.missing_info}
                    </div>
                  )}

                  <p className="text-xs text-muted-foreground">
                    モデル: {entry.response.model_used} / コスト:{" "}
                    {entry.response.cost_yen}円
                  </p>
                </CardContent>
              </Card>
            </div>
          ))}
        </div>
      )}

      {/* Loading indicator */}
      {loading && (
        <Card>
          <CardContent className="flex items-center gap-2 py-6">
            <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            <span className="text-sm text-muted-foreground">
              回答を生成中...
            </span>
          </CardContent>
        </Card>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* Input form */}
      <Card>
        <CardHeader>
          <CardTitle>質問を入力</CardTitle>
          <CardDescription>
            ナレッジベースに対して質問できます。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleAsk} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="question">質問 *</Label>
              <Input
                id="question"
                placeholder="例: 新規取引先の与信審査フローを教えてください"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="qa-department">部署フィルタ（任意）</Label>
              <select
                id="qa-department"
                value={department}
                onChange={(e) => setDepartment(e.target.value)}
                className="h-8 w-full rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                <option value="">全部署</option>
                {DEPARTMENTS.map((dep) => (
                  <option key={dep} value={dep}>
                    {dep}
                  </option>
                ))}
              </select>
            </div>

            <Button type="submit" disabled={loading || !question.trim()}>
              {loading ? "生成中..." : "質問する"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
