"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";

const STEPS = ["基本情報", "ファイル入力", "数量確認", "単価設定", "完了"];

const PROJECT_TYPES = [
  { value: "public_civil", label: "公共土木" },
  { value: "private_civil", label: "民間土木" },
  { value: "public_building", label: "公共建築" },
  { value: "private_building", label: "民間建築" },
];

const REGIONS = [
  "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
  "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
  "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
  "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
  "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
  "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
  "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
];

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyRecord = Record<string, any>;

export default function NewEstimationPage() {
  const router = useRouter();
  const { session } = useAuth();
  const [step, setStep] = useState(0);
  const [loading, setLoading] = useState(false);
  const [formError, setFormError] = useState("");
  const [projectId, setProjectId] = useState<string | null>(null);
  const [extractedItems, setExtractedItems] = useState<AnyRecord[]>([]);

  // Step 1
  const [name, setName] = useState("");
  const [projectType, setProjectType] = useState("public_civil");
  const [region, setRegion] = useState("東京都");
  const [fiscalYear, setFiscalYear] = useState(2026);
  const [clientName, setClientName] = useState("");

  // Step 2
  const [rawText, setRawText] = useState("");

  // フィードバック学習ループ
  const [finalizeLoading, setFinalizeLoading] = useState(false);
  const [accuracySummary, setAccuracySummary] = useState<{
    avg_accuracy: number;
    items_modified: number;
    items_unchanged: number;
  } | null>(null);

  async function handleConfirmEstimation() {
    const token = session?.access_token;
    if (!projectId || !token || extractedItems.length === 0) return;
    setFinalizeLoading(true);
    try {
      const items = extractedItems.map((item) => ({
        item_id: item.id as string,
        confirmed_unit_price: Number(item.unit_price) || 0,
      }));
      const result = await apiFetch<{
        finalized_count: number;
        learned_prices_count: number;
        accuracy_summary: { avg_accuracy: number; items_modified: number; items_unchanged: number };
      }>(`/bpo/construction/estimation/projects/${projectId}/finalize`, {
        method: "POST",
        token,
        body: { items },
      });
      setAccuracySummary(result.accuracy_summary);
    } catch {
      setFormError("積算確定に失敗しました。しばらく経ってから再度お試しください");
    } finally {
      setFinalizeLoading(false);
    }
  }

  async function handleCreateProject() {
    const token = session?.access_token;
    if (!token) return;
    setFormError("");
    setLoading(true);
    try {
      const result = await apiFetch<AnyRecord>("/bpo/construction/estimation/projects", {
        method: "POST",
        token,
        body: {
          name,
          project_type: projectType,
          region,
          fiscal_year: fiscalYear,
          client_name: clientName || null,
        },
      });
      setProjectId(result.id as string);
      setStep(1);
    } catch {
      setFormError("プロジェクト作成に失敗しました。しばらく経ってから再度お試しください");
    } finally {
      setLoading(false);
    }
  }

  async function handleExtract() {
    const token = session?.access_token;
    if (!projectId || !rawText.trim() || !token) return;
    setFormError("");
    setLoading(true);
    try {
      const result = await apiFetch<AnyRecord>(
        `/bpo/construction/estimation/projects/${projectId}/extract`,
        { method: "POST", token, body: rawText },
      );
      setExtractedItems((result.items as AnyRecord[]) || []);
      setStep(2);
    } catch {
      setFormError("数量抽出に失敗しました。しばらく経ってから再度お試しください");
    } finally {
      setLoading(false);
    }
  }

  async function handleSuggestPrices() {
    const token = session?.access_token;
    if (!projectId || !token) return;
    setFormError("");
    setLoading(true);
    try {
      const result = await apiFetch<AnyRecord[]>(
        `/bpo/construction/estimation/projects/${projectId}/suggest-prices`,
        { method: "POST", token },
      );
      setExtractedItems(Array.isArray(result) ? result : []);
      setStep(3);
    } catch {
      setFormError("単価推定に失敗しました。しばらく経ってから再度お試しください");
    } finally {
      setLoading(false);
    }
  }

  async function handleFinalize() {
    const token = session?.access_token;
    if (!projectId || !token) return;
    setFormError("");
    setLoading(true);
    try {
      await apiFetch(
        `/bpo/construction/estimation/projects/${projectId}/calculate`,
        { method: "POST", token },
      );
      setStep(4);
    } catch {
      setFormError("諸経費計算に失敗しました。しばらく経ってから再度お試しください");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">新規積算</h1>

      {/* ステッパー */}
      <div className="flex gap-2">
        {STEPS.map((s, i) => (
          <div
            key={s}
            className={`flex-1 rounded-md border px-3 py-2 text-center text-sm ${
              i === step
                ? "border-primary bg-primary text-primary-foreground"
                : i < step
                ? "border-primary/40 bg-primary/10 text-primary"
                : "text-muted-foreground"
            }`}
          >
            {s}
          </div>
        ))}
      </div>

      {/* インラインエラー */}
      {formError && (
        <p className="text-sm text-destructive">{formError}</p>
      )}

      {step === 0 && (
        <Card>
          <CardHeader><CardTitle>基本情報</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <div>
              <Label>工事名</Label>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="○○道路改良工事" />
            </div>
            <div>
              <Label>工事種別</Label>
              <select className="w-full rounded-md border px-3 py-2" value={projectType} onChange={(e) => setProjectType(e.target.value)}>
                {PROJECT_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </div>
            <div>
              <Label>地域</Label>
              <select className="w-full rounded-md border px-3 py-2" value={region} onChange={(e) => setRegion(e.target.value)}>
                {REGIONS.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
            <div>
              <Label>年度</Label>
              <Input type="number" value={fiscalYear} onChange={(e) => setFiscalYear(Number(e.target.value))} />
            </div>
            <div>
              <Label>発注者名（任意）</Label>
              <Input value={clientName} onChange={(e) => setClientName(e.target.value)} />
            </div>
            <Button onClick={handleCreateProject} disabled={!name || loading}>
              {loading ? "作成中..." : "次へ"}
            </Button>
          </CardContent>
        </Card>
      )}

      {step === 1 && (
        <Card>
          <CardHeader><CardTitle>数量計算書・設計書の入力</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              数量計算書や設計書のテキストを貼り付けてください。AIが工種・数量・単位を自動抽出します。
            </p>
            <Textarea rows={15} value={rawText} onChange={(e) => setRawText(e.target.value)} placeholder="工種、数量、単位を含むテキストを貼り付け..." />
            <Button onClick={handleExtract} disabled={!rawText.trim() || loading}>
              {loading ? "AI抽出中..." : "AIで数量を抽出"}
            </Button>
          </CardContent>
        </Card>
      )}

      {step === 2 && (
        <Card>
          <CardHeader><CardTitle>抽出結果の確認（{extractedItems.length}件）</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b bg-muted/50">
                    <th className="px-3 py-2 text-left">工種</th>
                    <th className="px-3 py-2 text-left">種別</th>
                    <th className="px-3 py-2 text-left">細別</th>
                    <th className="px-3 py-2 text-right">数量</th>
                    <th className="px-3 py-2 text-left">単位</th>
                  </tr>
                </thead>
                <tbody>
                  {extractedItems.map((item, i) => (
                    <tr key={i} className="border-b">
                      <td className="px-3 py-2">{item.category}</td>
                      <td className="px-3 py-2">{item.subcategory || "-"}</td>
                      <td className="px-3 py-2">{item.detail || "-"}</td>
                      <td className="px-3 py-2 text-right">{Number(item.quantity).toLocaleString()}</td>
                      <td className="px-3 py-2">{item.unit}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Button onClick={handleSuggestPrices} disabled={loading}>
              {loading ? "単価推定中..." : "単価を推定"}
            </Button>
          </CardContent>
        </Card>
      )}

      {step === 3 && (
        <Card>
          <CardHeader><CardTitle>単価設定</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b bg-muted/50">
                    <th className="px-3 py-2 text-left">工種</th>
                    <th className="px-3 py-2 text-right">数量</th>
                    <th className="px-3 py-2 text-left">単位</th>
                    <th className="px-3 py-2 text-right">単価</th>
                    <th className="px-3 py-2 text-right">金額</th>
                    <th className="px-3 py-2 text-left">ソース</th>
                  </tr>
                </thead>
                <tbody>
                  {extractedItems.map((item, i) => (
                    <tr key={i} className="border-b">
                      <td className="px-3 py-2">{item.category}</td>
                      <td className="px-3 py-2 text-right">{Number(item.quantity).toLocaleString()}</td>
                      <td className="px-3 py-2">{item.unit}</td>
                      <td className="px-3 py-2 text-right">
                        {item.unit_price ? `¥${Number(item.unit_price).toLocaleString()}` : "-"}
                      </td>
                      <td className="px-3 py-2 text-right">
                        {item.amount ? `¥${Number(item.amount).toLocaleString()}` : "-"}
                      </td>
                      <td className="px-3 py-2 text-xs text-muted-foreground">
                        {item.price_candidates?.[0]?.detail || item.price_source || "-"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex gap-3">
              <Button onClick={handleConfirmEstimation} disabled={finalizeLoading || !!accuracySummary} variant="outline">
                {finalizeLoading ? "確定中..." : accuracySummary ? "確定済み" : "積算を確定する"}
              </Button>
              <Button onClick={handleFinalize} disabled={loading}>
                {loading ? "計算中..." : "諸経費計算・完了"}
              </Button>
            </div>
            {accuracySummary && (
              <div className="rounded-lg border border-primary/30 bg-primary/5 px-4 py-3 text-sm text-primary">
                確定しました。AI信頼度:{" "}
                {accuracySummary.avg_accuracy >= 0.8
                  ? "高"
                  : accuracySummary.avg_accuracy >= 0.5
                  ? "中"
                  : "低"}
                （修正 {accuracySummary.items_modified}件 / 変更なし {accuracySummary.items_unchanged}件）
                — 学習データとして保存済み
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {step === 4 && (
        <Card>
          <CardContent className="py-12 text-center space-y-4">
            <p className="text-2xl font-bold text-green-600">積算完了</p>
            <p className="text-muted-foreground">内訳書をダウンロードできます。</p>
            <div className="flex justify-center gap-3">
              <Button onClick={() => window.open(`/api/v1/bpo/construction/estimation/projects/${projectId}/export`, "_blank")}>
                内訳書ダウンロード（Excel）
              </Button>
              <Button variant="outline" onClick={() => router.push("/bpo/estimation")}>
                一覧に戻る
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
