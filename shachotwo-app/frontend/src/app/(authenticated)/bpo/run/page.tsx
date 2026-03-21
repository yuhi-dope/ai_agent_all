"use client";

import { useSearchParams, useRouter } from "next/navigation";
import { Suspense, useState } from "react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

// ---------- パイプラインメタデータ ----------

interface PipelineMeta {
  name: string;
  industry: string;
  description: string;
  icon: string;
  sampleInput: Record<string, unknown>;
}

const PIPELINE_META: Record<string, PipelineMeta> = {
  "construction/estimation": {
    name: "積算・見積",
    industry: "建設業",
    description: "図面・仕様書から工種・数量を自動積算し見積書を作成します",
    icon: "🏗️",
    sampleInput: {
      project_name: "〇〇ビル新築工事",
      document_text: "RC造3階建て、延床面積500m2。基礎工事、躯体工事、仕上げ工事を含む。",
    },
  },
  "construction/billing": {
    name: "出来高・請求",
    industry: "建設業",
    description: "出来高管理と請求書の自動作成・送付を行います",
    icon: "📋",
    sampleInput: {
      project_id: "PRJ-001",
      billing_month: "2026-03",
      progress_rate: 0.6,
    },
  },
  "construction/safety_docs": {
    name: "安全書類",
    industry: "建設業",
    description: "グリーンファイル等の安全書類を自動生成・管理します",
    icon: "🦺",
    sampleInput: {
      site_name: "〇〇現場",
      company_name: "株式会社サンプル建設",
      workers: ["山田太郎", "鈴木一郎"],
    },
  },
  "manufacturing/quoting": {
    name: "見積作成",
    industry: "製造業",
    description: "図面・仕様から原価計算し見積書を自動作成します",
    icon: "🏭",
    sampleInput: {
      product_name: "精密部品A",
      material: "SUS304",
      quantity: 100,
      drawing_notes: "公差±0.05mm、表面粗さRa1.6",
    },
  },
  "dental/receipt_check": {
    name: "レセプト点検",
    industry: "歯科クリニック",
    description: "診療報酬明細書の自動点検・エラー検出を行います",
    icon: "🦷",
    sampleInput: {
      patient_id: "P-001",
      treatment_month: "2026-03",
      treatments: ["初診料", "X線撮影", "齲蝕処置"],
    },
  },
  "restaurant/fl_cost": {
    name: "FLコスト管理",
    industry: "飲食業",
    description: "食材費・人件費の自動集計とFL比率の分析を行います",
    icon: "🍽️",
    sampleInput: {
      store_name: "サンプルレストラン",
      target_month: "2026-03",
      food_cost: 800000,
      labor_cost: 600000,
      sales: 2500000,
    },
  },
  "beauty/booking_recall": {
    name: "予約・再来促進",
    industry: "美容業",
    description: "予約管理と顧客への再来促進メッセージ送付を自動化します",
    icon: "💇",
    sampleInput: {
      salon_name: "サンプルサロン",
      last_visit_days_threshold: 60,
      message_template: "お久しぶりです。ご来店お待ちしております。",
    },
  },
  "logistics/dispatch": {
    name: "配車管理",
    industry: "物流業",
    description: "配送依頼から最適ルート計算と配車指示を自動化します",
    icon: "🚚",
    sampleInput: {
      delivery_date: "2026-03-21",
      deliveries: [
        { destination: "東京都新宿区", weight_kg: 50 },
        { destination: "東京都渋谷区", weight_kg: 30 },
      ],
    },
  },
  "ecommerce/product_listing": {
    name: "商品登録",
    industry: "EC・小売",
    description: "商品情報から複数モールへの一括登録・更新を行います",
    icon: "🛒",
    sampleInput: {
      product_name: "サンプル商品",
      price: 3980,
      description: "高品質な商品です。",
      categories: ["雑貨", "インテリア"],
    },
  },
  "nursing/care_billing": {
    name: "介護報酬請求",
    industry: "介護",
    description: "サービス実績から国保連請求データを自動生成します",
    icon: "🏥",
    sampleInput: {
      facility_name: "サンプルデイサービス",
      billing_month: "2026-03",
      service_records: [{ user_id: "U001", service_code: "15_0001", days: 20 }],
    },
  },
  "staffing/dispatch_contract": {
    name: "派遣契約管理",
    industry: "人材派遣",
    description: "派遣契約書の作成・更新・期限管理を自動化します",
    icon: "👥",
    sampleInput: {
      worker_name: "田中花子",
      client_company: "株式会社ABC",
      start_date: "2026-04-01",
      end_date: "2026-09-30",
      hourly_rate: 1800,
    },
  },
  "clinic/medical_receipt": {
    name: "医療レセプト",
    industry: "医療クリニック",
    description: "医療機関の診療報酬請求業務を自動化します",
    icon: "🩺",
    sampleInput: {
      clinic_name: "サンプルクリニック",
      billing_month: "2026-03",
      diagnoses: ["I10", "E11"],
    },
  },
  "pharmacy/dispensing_billing": {
    name: "調剤報酬請求",
    industry: "薬局",
    description: "調剤実績から請求データを自動作成します",
    icon: "💊",
    sampleInput: {
      pharmacy_name: "サンプル薬局",
      billing_month: "2026-03",
      prescriptions_count: 450,
    },
  },
  "hotel/revenue_mgmt": {
    name: "レベニューマネジメント",
    industry: "ホテル・宿泊",
    description: "稼働率・ADR分析と最適料金の自動設定を行います",
    icon: "🏨",
    sampleInput: {
      hotel_name: "サンプルホテル",
      target_date: "2026-03-21",
      room_types: ["シングル", "ダブル", "スイート"],
      current_occupancy_rate: 0.65,
    },
  },
  "realestate/rent_collection": {
    name: "家賃回収管理",
    industry: "不動産",
    description: "家賃入金確認・督促・滞納管理を自動化します",
    icon: "🏢",
    sampleInput: {
      property_name: "サンプルマンション",
      target_month: "2026-03",
      units_count: 24,
    },
  },
  "auto_repair/repair_quoting": {
    name: "修理見積",
    industry: "自動車修理",
    description: "車両状態から修理項目を特定し見積書を自動作成します",
    icon: "🔧",
    sampleInput: {
      vehicle_make: "トヨタ",
      vehicle_model: "プリウス",
      year: 2020,
      damage_description: "フロントバンパー損傷、左フロントフェンダー凹み",
    },
  },
  "professional/deadline_mgmt": {
    name: "期限管理",
    industry: "士業事務所",
    description: "申告・届出の期限を一元管理し自動リマインドします",
    icon: "⚖️",
    sampleInput: {
      office_name: "サンプル税理士事務所",
      client_count: 50,
      upcoming_days: 30,
    },
  },
  "common/expense": {
    name: "経費精算",
    industry: "共通",
    description: "領収書OCR・承認フロー・仕訳計上を自動化します",
    icon: "💴",
    sampleInput: {
      applicant: "山田太郎",
      application_date: "2026-03-20",
      items: [
        { description: "交通費（東京→大阪）", amount: 14000 },
        { description: "接待費", amount: 25000 },
      ],
    },
  },
  "common/payroll": {
    name: "給与計算",
    industry: "共通",
    description: "勤怠データから給与計算・明細発行を自動化します",
    icon: "💰",
    sampleInput: {
      target_month: "2026-03",
      employees_count: 20,
    },
  },
  "common/attendance": {
    name: "勤怠管理",
    industry: "共通",
    description: "打刻データの集計・残業管理・36協定チェックを行います",
    icon: "⏰",
    sampleInput: {
      target_month: "2026-03",
      department: "営業部",
    },
  },
  "common/contract": {
    name: "契約管理",
    industry: "共通",
    description: "契約書の作成・電子署名・更新期限管理を自動化します",
    icon: "📝",
    sampleInput: {
      contract_type: "業務委託契約",
      party_a: "株式会社ABC",
      party_b: "株式会社XYZ",
      start_date: "2026-04-01",
      amount: 500000,
    },
  },
};

// ---------- 型定義 ----------

interface ExecutionStep {
  step_name: string;
  status: string;
  confidence?: number | null;
  cost_yen?: number | null;
  output?: unknown;
  error?: string | null;
}

interface ExecutionResult {
  success: boolean;
  pipeline: string;
  steps: ExecutionStep[];
  final_output: unknown;
  total_cost_yen?: number | null;
  requires_approval?: boolean;
  execution_log_id?: string | null;
  error?: string | null;
}

// ---------- ステップステータスバッジ ----------

function StepStatusBadge({ status }: { status: string }) {
  const variants: Record<string, { label: string; variant: "default" | "secondary" | "destructive" | "outline" }> = {
    completed: { label: "完了", variant: "default" },
    success: { label: "完了", variant: "default" },
    skipped: { label: "スキップ", variant: "outline" },
    failed: { label: "失敗", variant: "destructive" },
    error: { label: "エラー", variant: "destructive" },
    pending: { label: "待機中", variant: "secondary" },
    running: { label: "実行中", variant: "secondary" },
  };
  const meta = variants[status] ?? { label: status, variant: "outline" as const };
  return <Badge variant={meta.variant}>{meta.label}</Badge>;
}

// ---------- Inner Page（useSearchParams を使用） ----------

function BPORunInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { session } = useAuth();

  const pipelineKey = searchParams.get("pipeline") ?? "";
  const meta = PIPELINE_META[pipelineKey];

  const defaultInput = meta
    ? JSON.stringify(meta.sampleInput, null, 2)
    : "{\n  \n}";

  const [inputData, setInputData] = useState<string>(defaultInput);
  const [isDryRun, setIsDryRun] = useState(true);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ExecutionResult | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);

  // パイプラインキーが不明の場合
  if (!pipelineKey) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-bold">業務自動化を実行</h1>
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            業務が指定されていません。
            <br />
            <Button
              variant="link"
              onClick={() => router.push("/bpo")}
              className="mt-2"
            >
              業務一覧に戻る
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  async function handleRun() {
    setParseError(null);
    setApiError(null);
    setResult(null);

    // JSON パース検証
    let parsedInput: unknown;
    try {
      parsedInput = JSON.parse(inputData);
    } catch (e) {
      setParseError(
        e instanceof Error ? `JSONパースエラー: ${e.message}` : "JSONパースエラー"
      );
      return;
    }

    setLoading(true);
    try {
      const token = session?.access_token;
      const res = await apiFetch<ExecutionResult>("/execution/bpo", {
        method: "POST",
        token,
        body: {
          pipeline: pipelineKey,
          input_data: parsedInput,
          force_dry_run: isDryRun,
        },
      });
      setResult(res);
    } catch (err) {
      setApiError(
        err instanceof Error ? err.message : "APIエラーが発生しました"
      );
    } finally {
      setLoading(false);
    }
  }

  function handleReset() {
    setResult(null);
    setApiError(null);
    setParseError(null);
    setInputData(
      meta ? JSON.stringify(meta.sampleInput, null, 2) : "{\n  \n}"
    );
  }

  const displayMeta = meta ?? {
    name: pipelineKey,
    industry: "不明",
    description: "",
    icon: "⚙️",
    sampleInput: {},
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      {/* ヘッダー */}
      <div className="flex items-center gap-3">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => router.push("/bpo")}
          className="text-muted-foreground"
        >
          ← 一覧に戻る
        </Button>
      </div>

      <div className="flex items-start gap-3">
        <span className="text-3xl">{displayMeta.icon}</span>
        <div>
          <h1 className="text-2xl font-bold">{displayMeta.name}</h1>
          <p className="text-sm text-muted-foreground">
            {displayMeta.industry} — {displayMeta.description}
          </p>
        </div>
      </div>

      {/* 入力フォーム */}
      {!result && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">実行パラメータ</CardTitle>
            <CardDescription>
              入力データをJSON形式で指定してください。
              {meta && (
                <span className="block mt-1 text-xs text-muted-foreground">
                  ※サンプルデータが自動入力されています
                </span>
              )}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="input-data">入力データ（JSON）</Label>
              <Textarea
                id="input-data"
                value={inputData}
                onChange={(e) => setInputData(e.target.value)}
                className="min-h-48 font-mono text-sm"
                placeholder='{"key": "value"}'
                disabled={loading}
              />
              {parseError && (
                <p className="text-sm text-destructive">{parseError}</p>
              )}
            </div>

            <div className="flex items-center gap-2">
              <input
                id="dry-run"
                type="checkbox"
                checked={isDryRun}
                onChange={(e) => setIsDryRun(e.target.checked)}
                disabled={loading}
                className="h-4 w-4 rounded border-input"
              />
              <Label htmlFor="dry-run" className="cursor-pointer font-normal">
                ドライラン（実際の処理は行わず動作確認のみ）
              </Label>
            </div>

            {apiError && (
              <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
                {apiError}
              </div>
            )}

            <Button
              onClick={handleRun}
              disabled={loading || !inputData.trim()}
              className="w-full"
            >
              {loading ? (
                <span className="flex items-center gap-2">
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  実行中...
                </span>
              ) : (
                "業務を実行する"
              )}
            </Button>
          </CardContent>
        </Card>
      )}

      {/* 実行結果 */}
      {result && (
        <div className="space-y-4">
          {/* 承認待ちバナー */}
          {result.requires_approval && (
            <div className="flex items-center gap-3 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-800">
              <span className="text-lg">⏳</span>
              <div>
                <p className="font-semibold">承認待ち</p>
                <p className="text-xs">
                  この処理は人間の承認が必要です。担当者の確認をお待ちください。
                </p>
              </div>
            </div>
          )}

          {/* 成功/失敗サマリー */}
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-base">実行結果</CardTitle>
                <div className="flex items-center gap-2">
                  {result.success ? (
                    <Badge className="bg-green-500 hover:bg-green-500">
                      成功
                    </Badge>
                  ) : (
                    <Badge variant="destructive">失敗</Badge>
                  )}
                  {isDryRun && (
                    <Badge variant="outline">ドライラン</Badge>
                  )}
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-1 text-sm text-muted-foreground">
              <p>業務: {PIPELINE_META[result.pipeline]?.name ?? result.pipeline}</p>
              {result.error && (
                <p className="text-destructive">{result.error}</p>
              )}
            </CardContent>
          </Card>

          {/* ステップ詳細テーブル */}
          {result.steps && result.steps.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">ステップ詳細</CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>#</TableHead>
                      <TableHead>ステップ名</TableHead>
                      <TableHead>ステータス</TableHead>
                      <TableHead className="text-right">信頼度</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {result.steps.map((step, idx) => (
                      <TableRow key={idx}>
                        <TableCell className="text-xs text-muted-foreground">
                          {idx + 1}
                        </TableCell>
                        <TableCell className="font-medium text-sm">
                          {step.step_name}
                          {step.error && (
                            <p className="text-xs text-destructive mt-0.5">
                              {step.error}
                            </p>
                          )}
                        </TableCell>
                        <TableCell>
                          <StepStatusBadge status={step.status} />
                        </TableCell>
                        <TableCell className="text-right text-sm">
                          {step.confidence == null ? (
                            "-"
                          ) : step.confidence >= 0.8 ? (
                            <Badge variant="default">高</Badge>
                          ) : step.confidence >= 0.5 ? (
                            <Badge variant="secondary">中</Badge>
                          ) : (
                            <Badge variant="outline">参考情報</Badge>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}

          {/* 最終出力 */}
          {result.final_output !== undefined && result.final_output !== null && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">最終出力</CardTitle>
              </CardHeader>
              <CardContent>
                <pre className="overflow-x-auto rounded-md bg-muted p-4 text-xs leading-relaxed">
                  {JSON.stringify(result.final_output, null, 2)}
                </pre>
              </CardContent>
            </Card>
          )}

          {/* リセットボタン */}
          <div className="flex gap-3">
            <Button variant="outline" onClick={handleReset} className="flex-1">
              もう一度実行
            </Button>
            <Button
              variant="ghost"
              onClick={() => router.push("/bpo")}
              className="flex-1"
            >
              一覧に戻る
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------- Page (Suspense wrapper) ----------

export default function BPORunPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center py-20">
          <div className="flex flex-col items-center gap-3">
            <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
            <p className="text-sm text-muted-foreground">読み込み中...</p>
          </div>
        </div>
      }
    >
      <BPORunInner />
    </Suspense>
  );
}
