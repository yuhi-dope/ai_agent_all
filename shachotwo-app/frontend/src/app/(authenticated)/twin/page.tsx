"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { cn } from "@/lib/utils";

// ---------- 型定義 ----------

interface CompletenessRecommendation {
  dimension: string;
  completeness: number;
  message: string;
}

interface CompletenessData {
  labels: string[];
  values: number[];
  overall: number;
  recommendations: CompletenessRecommendation[];
}

interface TwinSnapshot {
  id: string;
  snapshot_at: string;
  people_state: Record<string, unknown> | null;
  process_state: Record<string, unknown> | null;
  cost_state: Record<string, unknown> | null;
  tool_state: Record<string, unknown> | null;
  risk_state: Record<string, unknown> | null;
}

interface WhatifResult {
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  delta: Record<string, number>;
  impact_summary: string;
}

// ---------- 定数 ----------

const DIMENSION_TAB_LABELS = ["ヒト", "プロセス", "コスト", "ツール", "リスク"];

const DIMENSION_STATE_KEYS: Record<string, keyof TwinSnapshot> = {
  ヒト: "people_state",
  プロセス: "process_state",
  コスト: "cost_state",
  ツール: "tool_state",
  リスク: "risk_state",
};

const WHATIF_DIMENSION_OPTIONS = [
  { value: "people", label: "ヒト" },
  { value: "process", label: "プロセス" },
  { value: "cost", label: "コスト" },
  { value: "tool", label: "ツール" },
  { value: "risk", label: "リスク" },
];

// ---------- ヘルパー ----------

function getValueColor(value: number): string {
  if (value >= 0.7) return "text-green-600";
  if (value >= 0.4) return "text-yellow-600";
  return "text-red-500";
}

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function JsonCard({ data, title }: { data: Record<string, unknown> | null; title: string }) {
  if (!data || Object.keys(data).length === 0) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-sm text-muted-foreground">
          まだ {title} のデータがありません。
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-2">
      {Object.entries(data).map(([key, val]) => (
        <Card key={key}>
          <CardContent className="py-3 px-4">
            <div className="flex items-start justify-between gap-2">
              <span className="shrink-0 text-xs font-medium text-muted-foreground">{key}</span>
              <span className="text-right text-sm break-all">
                {Array.isArray(val)
                  ? val.length === 0
                    ? <span className="text-muted-foreground italic">（なし）</span>
                    : (val as unknown[]).map((v, i) => (
                      <span key={i} className="ml-1 inline-block rounded-full bg-muted px-2 py-0.5 text-xs">
                        {String(v)}
                      </span>
                    ))
                  : typeof val === "number"
                  ? typeof val === "number" && key.includes("rate")
                    ? formatPercent(val as number)
                    : String(val)
                  : typeof val === "boolean"
                  ? val ? "はい" : "いいえ"
                  : val === null || val === undefined
                  ? <span className="text-muted-foreground italic">-</span>
                  : String(val)}
              </span>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

// ---------- What-if フォーム ----------

function WhatifSimulator({ token }: { token: string | undefined }) {
  const [dimension, setDimension] = useState("cost");
  const [field, setField] = useState("");
  const [value, setValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<WhatifResult | null>(null);

  async function handleRun() {
    if (!field.trim()) {
      setError("フィールド名を入力してください");
      return;
    }
    if (!value.trim()) {
      setError("新しい値を入力してください");
      return;
    }

    setLoading(true);
    setError(null);
    setResult(null);

    try {
      // 数値として解釈できる場合は数値に変換
      const parsedValue = isNaN(Number(value)) ? value : Number(value);

      const res = await apiFetch<WhatifResult>("/twin/whatif", {
        token,
        method: "POST",
        body: {
          changes: {
            dimension,
            field: field.trim(),
            value: parsedValue,
          },
        },
      });
      setResult(res);
    } catch (err) {
      setError("シミュレーションに失敗しました。しばらく経ってから再度お試しください");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">What-if シミュレーター</CardTitle>
        <CardDescription>
          パラメータを変更した場合のインパクトを試算します
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* フォーム */}
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="space-y-1.5">
            <Label htmlFor="whatif-dimension">次元</Label>
            <Select
              id="whatif-dimension"
              value={dimension}
              onChange={(e) => setDimension(e.target.value)}
            >
              {WHATIF_DIMENSION_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="whatif-field">フィールド名</Label>
            <Input
              id="whatif-field"
              placeholder="例: 月額固定費"
              value={field}
              onChange={(e) => setField(e.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="whatif-value">新しい値</Label>
            <Input
              id="whatif-value"
              placeholder="例: 500000"
              value={value}
              onChange={(e) => setValue(e.target.value)}
            />
          </div>
        </div>

        <Button onClick={handleRun} disabled={loading}>
          {loading ? (
            <span className="flex items-center gap-2">
              <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-primary-foreground border-t-transparent" />
              シミュレーション中...
            </span>
          ) : (
            "シミュレーション実行"
          )}
        </Button>

        {/* エラー */}
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {/* 結果 */}
        {result && (
          <div className="space-y-4 rounded-lg border bg-muted/30 p-4">
            <div>
              <p className="text-sm font-semibold text-foreground">インパクトサマリー</p>
              <p className="mt-1 text-base font-medium text-primary">{result.impact_summary}</p>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">変更前</p>
                <div className="space-y-1">
                  {Object.entries(result.before).map(([k, v]) => (
                    <div key={k} className="flex justify-between text-sm">
                      <span className="text-muted-foreground">{k}</span>
                      <span className={cn(k === field && "font-semibold")}>
                        {Array.isArray(v) ? `[${(v as unknown[]).length}件]` : String(v ?? "-")}
                      </span>
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">変更後</p>
                <div className="space-y-1">
                  {Object.entries(result.after).map(([k, v]) => (
                    <div key={k} className="flex justify-between text-sm">
                      <span className="text-muted-foreground">{k}</span>
                      <span className={cn(k === field && "font-semibold text-primary")}>
                        {Array.isArray(v) ? `[${(v as unknown[]).length}件]` : String(v ?? "-")}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {Object.keys(result.delta).length > 0 && (
              <div>
                <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">差分</p>
                <div className="space-y-1">
                  {Object.entries(result.delta).map(([k, v]) => (
                    <div key={k} className="flex justify-between text-sm">
                      <span className="text-muted-foreground">{k}</span>
                      <span className={cn("font-semibold", v > 0 ? "text-red-500" : "text-green-600")}>
                        {v > 0 ? "+" : ""}{typeof v === "number" ? v.toLocaleString() : String(v)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------- メインページ ----------

export default function TwinPage() {
  const { session } = useAuth();
  const token = session?.access_token;

  const [completeness, setCompleteness] = useState<CompletenessData | null>(null);
  const [snapshot, setSnapshot] = useState<TwinSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;

    async function fetchData() {
      setLoading(true);
      setError(null);
      try {
        const [completenessRes, snapshotRes] = await Promise.allSettled([
          apiFetch<CompletenessData>("/visualization/completeness", { token }),
          apiFetch<TwinSnapshot>("/twin/snapshot", { token }),
        ]);

        if (completenessRes.status === "fulfilled") {
          setCompleteness(completenessRes.value);
        }

        if (snapshotRes.status === "fulfilled") {
          setSnapshot(snapshotRes.value);
        }

        if (completenessRes.status === "rejected" && snapshotRes.status === "rejected") {
          setError("データの取得に失敗しました");
        }
      } catch (err) {
        setError("データの取得に失敗しました。しばらく経ってから再度お試しください");
      } finally {
        setLoading(false);
      }
    }

    fetchData();
  }, [token]);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      {/* ページヘッダー */}
      <div>
        <h1 className="text-2xl font-bold">会社の状態</h1>
        <p className="text-sm text-muted-foreground">
          会社の5次元状態を可視化し、What-if シミュレーションを実行します
        </p>
      </div>

      {/* エラー */}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* ローディング */}
      {loading && (
        <div className="flex items-center justify-center py-16">
          <div className="flex flex-col items-center gap-3">
            <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
            <p className="text-sm text-muted-foreground">データを読み込み中...</p>
          </div>
        </div>
      )}

      {!loading && (
        <>
          {/* ─── 充足度セクション ─── */}
          <Card>
            <CardHeader>
              <CardTitle>
                会社の状態 充足度:{" "}
                <span
                  className={cn(
                    "text-2xl font-bold",
                    completeness ? getValueColor(completeness.overall) : "text-muted-foreground"
                  )}
                >
                  {completeness ? formatPercent(completeness.overall) : "-"}
                </span>
              </CardTitle>
              <CardDescription>
                5次元それぞれの情報がどれだけ蓄積されているかを示します
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {completeness ? (
                <>
                  {completeness.labels.map((label, i) => (
                    <div key={label} className="space-y-1.5">
                      <div className="flex items-center justify-between text-sm">
                        <span className="font-medium">{label}</span>
                        <span className={cn("font-semibold", getValueColor(completeness.values[i]))}>
                          {formatPercent(completeness.values[i])}
                        </span>
                      </div>
                      <Progress value={completeness.values[i] * 100} max={100} />
                    </div>
                  ))}

                  {/* 低充足度の推奨 */}
                  {completeness.recommendations
                    .filter((r) => r.completeness < 0.5)
                    .map((rec) => (
                      <Alert key={rec.dimension} variant="warning">
                        <AlertDescription>
                          <span className="font-medium">{rec.dimension}:</span> {rec.message}
                        </AlertDescription>
                      </Alert>
                    ))}
                </>
              ) : (
                <div className="space-y-4">
                  {[1, 2, 3, 4, 5].map((i) => (
                    <div key={i} className="space-y-1.5">
                      <div className="h-4 w-32 animate-pulse rounded bg-muted" />
                      <div className="h-2 w-full animate-pulse rounded-full bg-muted" />
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {/* ─── 5次元タブ ─── */}
          <Card>
            <CardHeader>
              <CardTitle>5次元状態</CardTitle>
              <CardDescription>
                各次元の詳細データを確認します
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Tabs defaultValue="ヒト">
                <TabsList className="mb-4 flex h-auto w-full flex-wrap gap-1">
                  {DIMENSION_TAB_LABELS.map((label) => (
                    <TabsTrigger key={label} value={label}>
                      {label}
                    </TabsTrigger>
                  ))}
                </TabsList>

                {DIMENSION_TAB_LABELS.map((label) => {
                  const stateKey = DIMENSION_STATE_KEYS[label];
                  const stateData = snapshot
                    ? (snapshot[stateKey] as Record<string, unknown> | null)
                    : null;

                  return (
                    <TabsContent key={label} value={label}>
                      {snapshot ? (
                        <JsonCard data={stateData} title={label} />
                      ) : (
                        <div className="space-y-2">
                          {[1, 2, 3].map((i) => (
                            <div key={i} className="h-10 animate-pulse rounded-lg bg-muted" />
                          ))}
                        </div>
                      )}
                    </TabsContent>
                  );
                })}
              </Tabs>
            </CardContent>
          </Card>

          {/* ─── What-if シミュレーター ─── */}
          <WhatifSimulator token={token} />
        </>
      )}
    </div>
  );
}

