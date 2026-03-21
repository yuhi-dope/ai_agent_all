"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Select } from "@/components/ui/select";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";

// ---------- 定数 ----------

const STEPS = ["入力", "AI分析結果", "原価確認"];

const MATERIALS = [
  { value: "SS400", label: "SS400（一般構造用圧延鋼材）" },
  { value: "S45C", label: "S45C（機械構造用炭素鋼）" },
  { value: "SUS304", label: "SUS304（ステンレス鋼）" },
  { value: "SUS316", label: "SUS316（耐食ステンレス鋼）" },
  { value: "A2017", label: "A2017（ジュラルミン）" },
  { value: "A5052", label: "A5052（アルミニウム合金）" },
  { value: "A6061", label: "A6061（アルミニウム合金）" },
  { value: "C3604", label: "C3604（快削黄銅）" },
  { value: "CAC406", label: "CAC406（青銅鋳物）" },
  { value: "FCD400", label: "FCD400（球状黒鉛鋳鉄）" },
  { value: "other", label: "その他" },
];

const SURFACE_TREATMENTS = [
  { value: "none", label: "なし" },
  { value: "mekki", label: "メッキ" },
  { value: "alumite", label: "アルマイト" },
  { value: "kuroshime", label: "黒染め" },
  { value: "coating", label: "塗装" },
  { value: "nitriding", label: "窒化処理" },
  { value: "plating_hard_chrome", label: "硬質クロムメッキ" },
  { value: "other", label: "その他" },
];

const SUB_INDUSTRIES = [
  { value: "metalwork", label: "金属加工" },
  { value: "plastics", label: "樹脂・プラスチック加工" },
  { value: "food_chemical", label: "食品・化学" },
  { value: "electronics", label: "電子部品・精密機器" },
  { value: "general", label: "汎用製造" },
];

// ---------- 型定義（v2 QuoteResult に合わせる） ----------

interface ProcessRow {
  sort_order: number;
  process_name: string;
  equipment: string;
  equipment_type: string;
  setup_time_min: number;
  cycle_time_min: number;
  is_outsource: boolean;
  confidence: number;
  notes: string;
}

interface ProcessCostDetail {
  process_name: string;
  equipment: string;
  setup_time_min: number;
  cycle_time_min: number;
  total_time_min: number;
  charge_rate: number;
  process_cost: number;
}

interface QuoteCostBreakdown {
  material_cost: number;
  process_costs: ProcessCostDetail[];
  surface_treatment_cost: number;
  outsource_cost: number;
  inspection_cost: number;
  subtotal: number;
  overhead_cost: number;
  overhead_rate: number;
  profit: number;
  profit_rate: number;
  total_amount: number;
  unit_price: number;
}

interface AdditionalCostItem {
  cost_type: string;
  description: string;
  amount: number;
  per_piece: boolean;
  confidence: number;
}

interface LayerSource {
  field: string;
  layer: string; // "customer_db" | "yaml" | "llm" | "plugin"
  value: string;
  confidence: number;
  source_detail: string;
}

// v2 QuoteResult
interface QuoteResult {
  quote_id: string;
  sub_industry: string;
  processes: ProcessRow[];
  costs: QuoteCostBreakdown | null;
  additional_costs: AdditionalCostItem[];
  layers_used: LayerSource[];
  overall_confidence: number;
  warnings: string[];
}

// ---------- 確度バッジ ----------

function ConfidenceBadge({ confidence }: { confidence: number }) {
  if (confidence >= 0.8) {
    return (
      <span className="inline-flex items-center rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800">
        確度：高
      </span>
    );
  }
  if (confidence >= 0.5) {
    return (
      <span className="inline-flex items-center rounded-full bg-yellow-100 px-2 py-0.5 text-xs font-medium text-yellow-800">
        確度：中
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded-full bg-secondary px-2 py-0.5 text-xs font-medium text-secondary-foreground">
      参考情報
    </span>
  );
}

// ---------- レイヤーバッジ ----------

const LAYER_LABELS: Record<string, string> = {
  customer_db: "自社DB",
  yaml: "業界標準",
  llm: "AI推定",
  plugin: "プラグイン",
};

function LayerBadge({ layer }: { layer: string }) {
  return (
    <Badge variant="outline" className="text-xs">
      {LAYER_LABELS[layer] ?? layer}
    </Badge>
  );
}

// ---------- ステッパー ----------

function Stepper({ steps, current }: { steps: string[]; current: number }) {
  return (
    <div className="flex gap-2">
      {steps.map((s, i) => (
        <div
          key={s}
          className={`flex-1 rounded-md border px-3 py-2 text-center text-sm font-medium ${
            i === current
              ? "border-primary bg-primary text-primary-foreground"
              : i < current
              ? "border-primary/40 bg-primary/10 text-primary"
              : "border-border text-muted-foreground"
          }`}
        >
          {i < current ? `${i + 1}. ${s} ✓` : `${i + 1}. ${s}`}
        </div>
      ))}
    </div>
  );
}

// ---------- Page ----------

export default function NewManufacturingQuotePage() {
  const router = useRouter();
  const { session } = useAuth();
  const [step, setStep] = useState(0);
  const [analyzeLoading, setAnalyzeLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Step 0: 入力フォーム
  const [productName, setProductName] = useState("");
  const [customerName, setCustomerName] = useState("");
  const [description, setDescription] = useState("");
  const [material, setMaterial] = useState("SS400");
  const [quantity, setQuantity] = useState<number | "">(1);
  const [surfaceTreatment, setSurfaceTreatment] = useState("none");
  const [subIndustry, setSubIndustry] = useState("metalwork");
  const [deliveryDays, setDeliveryDays] = useState<number | "">("");
  const [overheadRate, setOverheadRate] = useState<number>(15);
  const [profitRate, setProfitRate] = useState<number>(15);

  // Step 1: v2 QuoteResult
  const [quoteResult, setQuoteResult] = useState<QuoteResult | null>(null);
  const [processes, setProcesses] = useState<ProcessRow[]>([]);

  // ---------- Step 0 → Step 1: v2 AI分析 ----------

  async function handleAnalyze() {
    const token = session?.access_token;
    if (!token) return;
    if (!productName.trim()) {
      setError("製品名を入力してください");
      return;
    }
    if (!quantity || Number(quantity) <= 0) {
      setError("数量を正しく入力してください");
      return;
    }
    setError(null);
    setAnalyzeLoading(true);
    try {
      // HearingInput フィールドにマッピング
      const body = {
        product_name: productName.trim(),
        specification: description.trim(),
        material,
        quantity: Number(quantity),
        surface_treatment: surfaceTreatment,
        sub_industry: subIndustry,
        delivery_days: deliveryDays !== "" ? Number(deliveryDays) : null,
        overhead_rate: overheadRate / 100,
        profit_rate: profitRate / 100,
        notes: customerName.trim() ? `顧客名: ${customerName.trim()}` : "",
      };
      const data = await apiFetch<QuoteResult>(
        "/bpo/manufacturing/quotes/v2",
        { method: "POST", token, body }
      );
      setQuoteResult(data);
      setProcesses(data.processes ?? []);
      setStep(1);
    } catch {
      setError("AI分析に失敗しました。入力内容を確認してもう一度お試しください。");
    } finally {
      setAnalyzeLoading(false);
    }
  }

  // ---------- Step 1: 再推定 ----------

  function handleReEstimate() {
    setStep(0);
    setQuoteResult(null);
    setProcesses([]);
  }

  // ---------- Step 1 → Step 2: 原価確認 ----------

  function handleGoToReview() {
    setStep(2);
  }

  // ---------- Step 2: 見積確定（v2はDB保存済みなので詳細へ遷移） ----------

  function handleFinalize() {
    if (!quoteResult?.quote_id) return;
    router.push(`/bpo/manufacturing/${quoteResult.quote_id}`);
  }

  // ---------- プロセス行の編集 ----------

  function updateProcess<K extends keyof ProcessRow>(
    index: number,
    field: K,
    value: ProcessRow[K]
  ) {
    setProcesses((prev) => {
      const next = [...prev];
      next[index] = { ...next[index], [field]: value };
      return next;
    });
  }

  // ---------- 原価サマリ（プロセス編集を反映） ----------

  function getDisplayCosts(): QuoteCostBreakdown | null {
    if (!quoteResult?.costs) return null;
    const base = quoteResult.costs;

    // プロセスが編集されていれば加工費を再計算
    const processTotal = processes.reduce((sum, p) => {
      const timeH = (p.setup_time_min + p.cycle_time_min * Number(quantity || 1)) / 60;
      // 元のコスト詳細からチャージレートを引き継ぐ（見つからなければ0）
      const detail = base.process_costs.find(
        (d) => d.process_name === p.process_name
      );
      const chargeRate = detail?.charge_rate ?? 0;
      return sum + timeH * chargeRate;
    }, 0);

    const mat = base.material_cost;
    const add = quoteResult.additional_costs.reduce((s, a) => s + a.amount, 0);
    const subtotal = mat + Math.round(processTotal) + add;
    const overhead = Math.round(subtotal * (overheadRate / 100));
    const profit = Math.round((subtotal + overhead) * (profitRate / 100));
    const total = subtotal + overhead + profit;

    return {
      ...base,
      subtotal,
      overhead_cost: overhead,
      profit,
      total_amount: total,
      unit_price: Number(quantity) > 0 ? Math.round(total / Number(quantity)) : 0,
    };
  }

  const displayCosts = step >= 1 ? getDisplayCosts() : quoteResult?.costs ?? null;

  // ---------- 完了画面（v2はすでにDB保存済みなのでボタン遷移のみ） ----------

  if (step === 3 && quoteResult?.quote_id) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold">新規見積作成</h1>
        <Card>
          <CardContent className="py-16 text-center space-y-4">
            <p className="text-2xl font-bold text-green-600">見積を保存しました</p>
            <div className="flex flex-wrap justify-center gap-3">
              <Button onClick={() => router.push(`/bpo/manufacturing/${quoteResult.quote_id}`)}>
                見積詳細を確認する
              </Button>
              <Button variant="outline" onClick={() => router.push("/bpo/manufacturing")}>
                一覧に戻る
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">新規見積作成</h1>

      <Stepper steps={STEPS} current={step} />

      {/* エラー */}
      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* ======== STEP 0: 入力 ======== */}
      {step === 0 && (
        <Card>
          <CardHeader>
            <CardTitle>製品・仕様の入力</CardTitle>
            <CardDescription>
              製品情報と仕様を入力してください。AIが加工工程と原価を自動推定します。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="grid gap-5 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="product-name">製品名</Label>
                <Input
                  id="product-name"
                  value={productName}
                  onChange={(e) => setProductName(e.target.value)}
                  placeholder="例: ブラケット部品 A-001"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="customer-name">顧客名（任意）</Label>
                <Input
                  id="customer-name"
                  value={customerName}
                  onChange={(e) => setCustomerName(e.target.value)}
                  placeholder="例: 株式会社山田製作所"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="description">仕様・説明（任意）</Label>
              <Textarea
                id="description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={4}
                placeholder="例: t=6mm フランジ付きブラケット、穴径φ10×4カ所、公差±0.05"
              />
            </div>

            <div className="grid gap-5 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="material">材質</Label>
                <Select
                  id="material"
                  value={material}
                  onChange={(e) => setMaterial(e.target.value)}
                  className="h-9"
                >
                  {MATERIALS.map((m) => (
                    <option key={m.value} value={m.value}>
                      {m.label}
                    </option>
                  ))}
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="quantity">数量（個）</Label>
                <Input
                  id="quantity"
                  type="number"
                  min={1}
                  value={quantity}
                  onChange={(e) =>
                    setQuantity(e.target.value === "" ? "" : Number(e.target.value))
                  }
                  placeholder="例: 100"
                />
              </div>
            </div>

            <div className="grid gap-5 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="surface-treatment">表面処理</Label>
                <Select
                  id="surface-treatment"
                  value={surfaceTreatment}
                  onChange={(e) => setSurfaceTreatment(e.target.value)}
                  className="h-9"
                >
                  {SURFACE_TREATMENTS.map((s) => (
                    <option key={s.value} value={s.value}>
                      {s.label}
                    </option>
                  ))}
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="sub-industry">業種・加工種別</Label>
                <Select
                  id="sub-industry"
                  value={subIndustry}
                  onChange={(e) => setSubIndustry(e.target.value)}
                  className="h-9"
                >
                  {SUB_INDUSTRIES.map((s) => (
                    <option key={s.value} value={s.value}>
                      {s.label}
                    </option>
                  ))}
                </Select>
              </div>
            </div>

            <div className="grid gap-5 sm:grid-cols-3">
              <div className="space-y-1.5">
                <Label htmlFor="delivery-days">納期（日）（任意）</Label>
                <Input
                  id="delivery-days"
                  type="number"
                  min={1}
                  value={deliveryDays}
                  onChange={(e) =>
                    setDeliveryDays(e.target.value === "" ? "" : Number(e.target.value))
                  }
                  placeholder="例: 14"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="overhead-rate">諸経費率（%）</Label>
                <Input
                  id="overhead-rate"
                  type="number"
                  min={0}
                  max={100}
                  value={overheadRate}
                  onChange={(e) => setOverheadRate(Number(e.target.value))}
                  placeholder="例: 15"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="profit-rate">利益率（%）</Label>
                <Input
                  id="profit-rate"
                  type="number"
                  min={0}
                  max={100}
                  value={profitRate}
                  onChange={(e) => setProfitRate(Number(e.target.value))}
                  placeholder="例: 15"
                />
              </div>
            </div>

            <Button
              onClick={handleAnalyze}
              disabled={analyzeLoading || !productName.trim() || !quantity}
              className="w-full sm:w-auto"
            >
              {analyzeLoading ? (
                <>
                  <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  AIが加工工程を分析しています...
                </>
              ) : (
                "AIで加工工程・原価を推定する"
              )}
            </Button>

            {analyzeLoading && (
              <p className="text-xs text-muted-foreground">
                材質・仕様をもとに最適な加工工程と原価を計算しています。少しお待ちください。
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {/* ======== STEP 1: AI分析結果 ======== */}
      {step === 1 && quoteResult && (
        <div className="space-y-4">
          {/* 信頼情報バナー */}
          <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-muted/30 px-4 py-3">
            <ConfidenceBadge confidence={quoteResult.overall_confidence} />
            <span className="text-sm text-muted-foreground">
              推定精度: AIが工程・原価を自動推定しました。内容を確認・修正してから次へ進んでください。
            </span>
          </div>

          {/* 警告 */}
          {quoteResult.warnings.length > 0 && (
            <div className="rounded-md border border-yellow-300 bg-yellow-50 px-4 py-3">
              <p className="text-xs font-medium text-yellow-800 mb-1">注意事項</p>
              <ul className="list-disc list-inside space-y-0.5">
                {quoteResult.warnings.map((w, i) => (
                  <li key={i} className="text-xs text-yellow-700">{w}</li>
                ))}
              </ul>
            </div>
          )}

          {/* データ根拠（layers_used） */}
          {quoteResult.layers_used.length > 0 && (
            <div className="rounded-md border bg-background p-3">
              <p className="text-xs font-medium text-muted-foreground mb-2">推定に使用したデータソース</p>
              <div className="flex flex-wrap gap-2">
                {/* 重複レイヤー名をまとめて表示 */}
                {Array.from(new Set(quoteResult.layers_used.map((l) => l.layer))).map((layer) => {
                  const count = quoteResult.layers_used.filter((l) => l.layer === layer).length;
                  return (
                    <Badge key={layer} variant="outline" className="text-xs">
                      {LAYER_LABELS[layer] ?? layer}（{count}件）
                    </Badge>
                  );
                })}
              </div>
            </div>
          )}

          {/* 工程テーブル（編集可能） */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">推定加工工程</CardTitle>
              <CardDescription className="text-xs">
                各行の値を直接編集できます。原価は自動で再計算されます。
              </CardDescription>
            </CardHeader>
            <CardContent>
              {/* PC: テーブル表示 */}
              <div className="hidden md:block overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b bg-muted/50">
                      <th className="px-3 py-2 text-left">工程名</th>
                      <th className="px-3 py-2 text-left">設備</th>
                      <th className="px-3 py-2 text-right">段取時間（分）</th>
                      <th className="px-3 py-2 text-right">サイクル（分）</th>
                      <th className="px-3 py-2 text-center">確度</th>
                      <th className="px-3 py-2 text-center">ソース</th>
                    </tr>
                  </thead>
                  <tbody>
                    {processes.map((p, i) => {
                      // このプロセスに対応するレイヤー情報を取得
                      const layerEntry = quoteResult.layers_used.find(
                        (l) => l.field === p.process_name || l.field === `process_${i}`
                      );
                      return (
                        <tr key={i} className="border-b">
                          <td className="px-3 py-2">
                            <Input
                              value={p.process_name}
                              onChange={(e) => updateProcess(i, "process_name", e.target.value)}
                              className="h-7 text-sm"
                            />
                          </td>
                          <td className="px-3 py-2">
                            <Input
                              value={p.equipment}
                              onChange={(e) => updateProcess(i, "equipment", e.target.value)}
                              className="h-7 text-sm"
                            />
                          </td>
                          <td className="px-3 py-2">
                            <Input
                              type="number"
                              min={0}
                              value={p.setup_time_min}
                              onChange={(e) =>
                                updateProcess(i, "setup_time_min", Number(e.target.value))
                              }
                              className="h-7 text-sm text-right"
                            />
                          </td>
                          <td className="px-3 py-2">
                            <Input
                              type="number"
                              min={0}
                              value={p.cycle_time_min}
                              onChange={(e) =>
                                updateProcess(i, "cycle_time_min", Number(e.target.value))
                              }
                              className="h-7 text-sm text-right"
                            />
                          </td>
                          <td className="px-3 py-2 text-center">
                            <ConfidenceBadge confidence={p.confidence} />
                          </td>
                          <td className="px-3 py-2 text-center">
                            {layerEntry ? (
                              <LayerBadge layer={layerEntry.layer} />
                            ) : (
                              <span className="text-xs text-muted-foreground">—</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* スマホ: カードリスト */}
              <div className="md:hidden space-y-3">
                {processes.map((p, i) => {
                  const layerEntry = quoteResult.layers_used.find(
                    (l) => l.field === p.process_name || l.field === `process_${i}`
                  );
                  return (
                    <div key={i} className="rounded-md border p-3 space-y-3">
                      <div className="flex items-center justify-between flex-wrap gap-2">
                        <span className="text-xs font-medium text-muted-foreground">
                          工程 {i + 1}
                        </span>
                        <div className="flex gap-1.5">
                          <ConfidenceBadge confidence={p.confidence} />
                          {layerEntry && <LayerBadge layer={layerEntry.layer} />}
                        </div>
                      </div>
                      <div className="space-y-2">
                        <div className="space-y-1">
                          <Label className="text-xs">工程名</Label>
                          <Input
                            value={p.process_name}
                            onChange={(e) => updateProcess(i, "process_name", e.target.value)}
                            className="h-8"
                          />
                        </div>
                        <div className="space-y-1">
                          <Label className="text-xs">設備</Label>
                          <Input
                            value={p.equipment}
                            onChange={(e) => updateProcess(i, "equipment", e.target.value)}
                            className="h-8"
                          />
                        </div>
                        <div className="grid grid-cols-2 gap-2">
                          <div className="space-y-1">
                            <Label className="text-xs">段取（分）</Label>
                            <Input
                              type="number"
                              min={0}
                              value={p.setup_time_min}
                              onChange={(e) =>
                                updateProcess(i, "setup_time_min", Number(e.target.value))
                              }
                              className="h-8"
                            />
                          </div>
                          <div className="space-y-1">
                            <Label className="text-xs">サイクル（分）</Label>
                            <Input
                              type="number"
                              min={0}
                              value={p.cycle_time_min}
                              onChange={(e) =>
                                updateProcess(i, "cycle_time_min", Number(e.target.value))
                              }
                              className="h-8"
                            />
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </CardContent>
          </Card>

          {/* ボタン群 */}
          <div className="flex flex-wrap gap-3">
            <Button onClick={handleGoToReview} className="w-full sm:w-auto">
              原価内訳を確認する
            </Button>
            <Button
              variant="outline"
              onClick={handleReEstimate}
              className="w-full sm:w-auto"
            >
              入力に戻って再推定する
            </Button>
          </div>
        </div>
      )}

      {/* ======== STEP 2: 原価確認 ======== */}
      {step === 2 && displayCosts && (
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">原価・見積金額の内訳</CardTitle>
              <CardDescription className="text-xs">
                内容を確認して「見積を確定する」を押してください。見積はすでに下書き保存されています。
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* 原価内訳 */}
              <div className="rounded-md border overflow-hidden">
                <table className="w-full text-sm">
                  <tbody>
                    <tr className="border-b">
                      <td className="px-4 py-3 text-muted-foreground">材料費</td>
                      <td className="px-4 py-3 text-right font-medium">
                        ¥{displayCosts.material_cost.toLocaleString()}
                      </td>
                    </tr>
                    <tr className="border-b">
                      <td className="px-4 py-3 text-muted-foreground">
                        加工費（{processes.length}工程）
                      </td>
                      <td className="px-4 py-3 text-right font-medium">
                        ¥{displayCosts.process_costs
                          .reduce((s, p) => s + p.process_cost, 0)
                          .toLocaleString()}
                      </td>
                    </tr>
                    {displayCosts.surface_treatment_cost > 0 && (
                      <tr className="border-b">
                        <td className="px-4 py-3 text-muted-foreground">表面処理費</td>
                        <td className="px-4 py-3 text-right font-medium">
                          ¥{displayCosts.surface_treatment_cost.toLocaleString()}
                        </td>
                      </tr>
                    )}
                    {displayCosts.outsource_cost > 0 && (
                      <tr className="border-b">
                        <td className="px-4 py-3 text-muted-foreground">外注費</td>
                        <td className="px-4 py-3 text-right font-medium">
                          ¥{displayCosts.outsource_cost.toLocaleString()}
                        </td>
                      </tr>
                    )}
                    {/* プラグイン由来の追加コスト */}
                    {quoteResult?.additional_costs.map((ac, i) => (
                      <tr key={i} className="border-b">
                        <td className="px-4 py-3 text-muted-foreground">
                          {ac.description}
                          {ac.per_piece && (
                            <span className="ml-1 text-xs text-muted-foreground">
                              （×{Number(quantity)}個）
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-right font-medium">
                          ¥{(ac.per_piece ? ac.amount * Number(quantity || 1) : ac.amount).toLocaleString()}
                        </td>
                      </tr>
                    ))}
                    <tr className="border-b bg-muted/30">
                      <td className="px-4 py-3 text-muted-foreground">
                        諸経費（{overheadRate}%）
                      </td>
                      <td className="px-4 py-3 text-right font-medium">
                        ¥{displayCosts.overhead_cost.toLocaleString()}
                      </td>
                    </tr>
                    <tr className="border-b bg-muted/30">
                      <td className="px-4 py-3 text-muted-foreground">
                        利益（{profitRate}%）
                      </td>
                      <td className="px-4 py-3 text-right font-medium">
                        ¥{displayCosts.profit.toLocaleString()}
                      </td>
                    </tr>
                    <tr className="bg-primary/5">
                      <td className="px-4 py-3 font-semibold">合計金額</td>
                      <td className="px-4 py-3 text-right text-lg font-bold">
                        ¥{displayCosts.total_amount.toLocaleString()}
                      </td>
                    </tr>
                    {displayCosts.unit_price > 0 && (
                      <tr className="bg-primary/5 border-t">
                        <td className="px-4 py-3 text-muted-foreground text-sm">
                          単価（1個あたり）
                        </td>
                        <td className="px-4 py-3 text-right font-medium">
                          ¥{displayCosts.unit_price.toLocaleString()}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>

              {/* 工程別加工費内訳 */}
              {displayCosts.process_costs.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-2">
                    工程別加工費の内訳
                  </p>
                  <div className="rounded-md border overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b bg-muted/50">
                          <th className="px-3 py-2 text-left">工程名</th>
                          <th className="px-3 py-2 text-right">チャージ（円/時）</th>
                          <th className="px-3 py-2 text-right">加工費</th>
                        </tr>
                      </thead>
                      <tbody>
                        {displayCosts.process_costs.map((pc, i) => (
                          <tr key={i} className="border-b">
                            <td className="px-3 py-2">{pc.process_name}</td>
                            <td className="px-3 py-2 text-right text-muted-foreground">
                              ¥{pc.charge_rate.toLocaleString()}
                            </td>
                            <td className="px-3 py-2 text-right">
                              ¥{pc.process_cost.toLocaleString()}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* ボタン */}
              <div className="flex flex-wrap gap-3 pt-2">
                <Button
                  onClick={handleFinalize}
                  className="w-full sm:w-auto"
                >
                  見積詳細を確認する
                </Button>
                <Button
                  variant="outline"
                  onClick={() => setStep(1)}
                  className="w-full sm:w-auto"
                >
                  工程の修正に戻る
                </Button>
                <Button
                  variant="outline"
                  onClick={() => router.push("/bpo/manufacturing")}
                  className="w-full sm:w-auto"
                >
                  一覧に戻る
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
