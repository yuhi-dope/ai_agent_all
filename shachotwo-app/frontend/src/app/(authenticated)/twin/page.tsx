"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
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

// レーダーチャートの軸ラベル（5次元）
const RADAR_LABELS = ["ヒト", "プロセス", "コスト", "ツール", "リスク"];

// ---------- ヘルパー ----------

function getValueColor(value: number): string {
  if (value >= 0.7) return "text-green-600";
  if (value >= 0.4) return "text-yellow-600";
  return "text-destructive";
}

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

// ---------- SVGレーダーチャート ----------

interface RadarChartProps {
  values: number[]; // 0.0〜1.0、5要素
  labels: string[];
  size?: number;
}

function RadarChart({ values, labels, size = 240 }: RadarChartProps) {
  const cx = size / 2;
  const cy = size / 2;
  const radius = (size / 2) * 0.72;
  const n = labels.length;
  const levels = 4; // 同心円の数

  // 各軸の角度（上から時計回り、0度=上）
  function getAngle(i: number) {
    return (Math.PI * 2 * i) / n - Math.PI / 2;
  }

  function polarToXY(r: number, i: number) {
    const angle = getAngle(i);
    return {
      x: cx + r * Math.cos(angle),
      y: cy + r * Math.sin(angle),
    };
  }

  // 同心円（グリッド）のパス
  function gridPath(level: number) {
    const r = (radius * (level + 1)) / levels;
    const points = Array.from({ length: n }, (_, i) => {
      const { x, y } = polarToXY(r, i);
      return `${i === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    });
    return points.join(" ") + " Z";
  }

  // データポリゴンのパス
  function dataPath() {
    const points = values.map((v, i) => {
      const r = radius * Math.min(Math.max(v, 0), 1);
      const { x, y } = polarToXY(r, i);
      return `${i === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    });
    return points.join(" ") + " Z";
  }

  // ラベルの配置（軸の先端から少し外側）
  const labelOffset = radius * 1.22;

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      aria-label="会社の状態レーダーチャート"
      className="mx-auto block"
    >
      {/* 同心円グリッド */}
      {Array.from({ length: levels }, (_, i) => (
        <path
          key={i}
          d={gridPath(i)}
          fill="none"
          stroke="currentColor"
          strokeWidth="0.5"
          className="text-border"
          opacity="0.5"
        />
      ))}

      {/* 軸線 */}
      {Array.from({ length: n }, (_, i) => {
        const { x, y } = polarToXY(radius, i);
        return (
          <line
            key={i}
            x1={cx}
            y1={cy}
            x2={x.toFixed(2)}
            y2={y.toFixed(2)}
            stroke="currentColor"
            strokeWidth="0.5"
            className="text-border"
            opacity="0.5"
          />
        );
      })}

      {/* データポリゴン（塗りつぶし） */}
      <path
        d={dataPath()}
        fill="hsl(var(--primary) / 0.18)"
        stroke="hsl(var(--primary))"
        strokeWidth="2"
        strokeLinejoin="round"
      />

      {/* 低充足度（<0.4）の軸を赤でハイライト */}
      {values.map((v, i) => {
        if (v >= 0.4) return null;
        const r = radius * Math.min(Math.max(v, 0), 1);
        const { x, y } = polarToXY(r, i);
        return (
          <line
            key={`low-${i}`}
            x1={cx}
            y1={cy}
            x2={x.toFixed(2)}
            y2={y.toFixed(2)}
            stroke="hsl(var(--destructive))"
            strokeWidth="2"
            strokeDasharray="4 2"
          />
        );
      })}

      {/* データポイント */}
      {values.map((v, i) => {
        const r = radius * Math.min(Math.max(v, 0), 1);
        const { x, y } = polarToXY(r, i);
        const isLow = v < 0.4;
        return (
          <circle
            key={i}
            cx={x.toFixed(2)}
            cy={y.toFixed(2)}
            r="4"
            fill={isLow ? "hsl(var(--destructive))" : "hsl(var(--primary))"}
            stroke="white"
            strokeWidth="1.5"
          />
        );
      })}

      {/* ラベル */}
      {labels.map((label, i) => {
        const { x, y } = polarToXY(labelOffset, i);
        const isLow = values[i] < 0.4;
        // テキストアンカー調整
        const anchor =
          Math.abs(x - cx) < 6 ? "middle" : x < cx ? "end" : "start";
        return (
          <text
            key={i}
            x={x.toFixed(2)}
            y={y.toFixed(2)}
            textAnchor={anchor}
            dominantBaseline="central"
            fontSize="12"
            fontWeight="500"
            fill={isLow ? "hsl(var(--destructive))" : "currentColor"}
          >
            {label}
          </text>
        );
      })}
    </svg>
  );
}

// ---------- 次元詳細カード ----------

interface DimensionCardProps {
  label: string;
  value: number;
  recommendation?: string;
}

function DimensionCard({ label, value, recommendation }: DimensionCardProps) {
  const isLow = value < 0.4;
  return (
    <Card className={cn(isLow && "border-destructive/40")}>
      <CardContent className="py-4 px-4 space-y-2">
        <div className="flex items-center justify-between">
          <span className={cn("text-base font-medium", isLow && "text-destructive")}>
            {label}
          </span>
          <span className={cn("text-sm font-semibold", getValueColor(value))}>
            {formatPercent(value)}
          </span>
        </div>
        <Progress value={value * 100} max={100} />
        {isLow && recommendation && (
          <p className="text-xs text-destructive">{recommendation}</p>
        )}
      </CardContent>
    </Card>
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
    } catch {
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

        <Button onClick={handleRun} disabled={loading} className="w-full sm:w-auto">
          {loading ? (
            <span className="flex items-center gap-2">
              <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-primary-foreground border-t-transparent" />
              シミュレーション中...
            </span>
          ) : (
            "シミュレーションを実行する"
          )}
        </Button>

        {error && (
          <p className="text-sm text-destructive">{error}</p>
        )}

        {result && (
          <div className="space-y-4 rounded-lg border bg-muted/30 p-4">
            <div>
              <p className="text-sm font-semibold">インパクトサマリー</p>
              <p className="mt-1 text-base font-medium text-primary">{result.impact_summary}</p>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <p className="mb-2 text-xs font-semibold text-muted-foreground">変更前</p>
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
                <p className="mb-2 text-xs font-semibold text-muted-foreground">変更後</p>
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
                <p className="mb-2 text-xs font-semibold text-muted-foreground">差分</p>
                <div className="space-y-1">
                  {Object.entries(result.delta).map(([k, v]) => (
                    <div key={k} className="flex justify-between text-sm">
                      <span className="text-muted-foreground">{k}</span>
                      <span className={cn("font-semibold", v > 0 ? "text-destructive" : "text-green-600")}>
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

// ---------- 次元データカード（タブ内） ----------

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
                  : typeof val === "number" && key.includes("rate")
                  ? formatPercent(val)
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

// ---------- メインページ ----------

export default function TwinPage() {
  const { session } = useAuth();
  const router = useRouter();
  const token = session?.access_token;

  const [completeness, setCompleteness] = useState<CompletenessData | null>(null);
  const [snapshot, setSnapshot] = useState<TwinSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hasData, setHasData] = useState(false);

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
          // overall > 0 か values に 0 以外があればデータあり
          const hasAny = completenessRes.value.overall > 0 ||
            completenessRes.value.values.some((v) => v > 0);
          setHasData(hasAny);
        }

        if (snapshotRes.status === "fulfilled") {
          setSnapshot(snapshotRes.value);
          // スナップショットのいずれかのstateが非空ならデータあり
          const snap = snapshotRes.value;
          const stateKeys = ["people_state", "process_state", "cost_state", "tool_state", "risk_state"] as const;
          const anyState = stateKeys.some(
            (k) => snap[k] && Object.keys(snap[k] as object).length > 0
          );
          if (anyState) setHasData(true);
        }

        if (completenessRes.status === "rejected" && snapshotRes.status === "rejected") {
          setError("データの取得に失敗しました。しばらく経ってから再度お試しください");
        }
      } finally {
        setLoading(false);
      }
    }

    fetchData();
  }, [token]);

  // completenessのvaluesを5次元順に整列
  const radarValues: number[] = RADAR_LABELS.map((label, i) => {
    if (!completeness) return 0;
    const idx = completeness.labels.indexOf(label);
    return idx !== -1 ? completeness.values[idx] : completeness.values[i] ?? 0;
  });

  // 低充足度（<0.4）次元の改善提案マップ
  const recommendationMap: Record<string, string> = {};
  if (completeness) {
    for (const rec of completeness.recommendations) {
      recommendationMap[rec.dimension] = rec.message;
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      {/* ページヘッダー */}
      <div>
        <h1 className="text-2xl font-bold">会社の状態</h1>
        <p className="text-sm text-muted-foreground">
          ヒト・プロセス・コスト・ツール・リスクの5つの視点から会社の現状を可視化します
        </p>
      </div>

      {/* エラー */}
      {error && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* ローディング */}
      {loading && (
        <div className="flex items-center justify-center py-16">
          <div className="flex flex-col items-center gap-3">
            <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
            <p className="text-sm text-muted-foreground">会社の状態を読み込み中...</p>
          </div>
        </div>
      )}

      {!loading && !hasData && !error && (
        /* 空の状態 */
        <Card>
          <CardContent className="flex flex-col items-center gap-4 py-16">
            <div className="flex h-16 w-16 items-center justify-center rounded-full bg-muted">
              <svg
                className="h-8 w-8 text-muted-foreground"
                xmlns="http://www.w3.org/2000/svg"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={1.5}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M3.75 3v11.25A2.25 2.25 0 0 0 6 16.5h2.25M3.75 3h-1.5m1.5 0h16.5m0 0h1.5m-1.5 0v11.25A2.25 2.25 0 0 1 18 16.5h-2.25m-7.5 0h7.5m-7.5 0-1 3m8.5-3 1 3m0 0 .5 1.5m-.5-1.5h-9.5m0 0-.5 1.5"
                />
              </svg>
            </div>
            <div className="text-center">
              <p className="text-base font-medium">まだ会社の状態が登録されていません</p>
              <p className="mt-1 text-sm text-muted-foreground">
                ナレッジを入力すると、会社の全体像が見えるようになります
              </p>
            </div>
            <Button
              size="lg"
              className="mt-2"
              onClick={() => router.push("/knowledge/input")}
            >
              ナレッジを入力する
            </Button>
          </CardContent>
        </Card>
      )}

      {!loading && (hasData || !!completeness) && (
        <>
          {/* ─── レーダーチャート + 充足度カード ─── */}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            {/* レーダーチャート */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">5次元レーダー</CardTitle>
                <CardDescription>
                  全体充足度:{" "}
                  <span className={cn("font-semibold", completeness ? getValueColor(completeness.overall) : "text-muted-foreground")}>
                    {completeness ? formatPercent(completeness.overall) : "-"}
                  </span>
                </CardDescription>
              </CardHeader>
              <CardContent className="flex items-center justify-center py-4">
                <RadarChart values={radarValues} labels={RADAR_LABELS} size={260} />
              </CardContent>
            </Card>

            {/* 5次元充足度バー */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">次元別の充足度</CardTitle>
                <CardDescription>
                  赤色の次元は情報が不足しています
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3 py-4">
                {RADAR_LABELS.map((label, i) => {
                  const v = radarValues[i];
                  const rec = recommendationMap[label];
                  return (
                    <DimensionCard
                      key={label}
                      label={label}
                      value={v}
                      recommendation={rec}
                    />
                  );
                })}
              </CardContent>
            </Card>
          </div>

          {/* ─── 5次元タブ（詳細データ） ─── */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">5次元の詳細データ</CardTitle>
              <CardDescription>
                各次元の詳細情報を確認します
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
